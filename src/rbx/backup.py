from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from .common import Problem, atomic_json, checked_path, digest, locked, now
from .dependencies import bridge
from .rekordbox import Rekordbox


def copy_verified(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = digest(source)
    shutil.copy2(source, destination)
    if digest(destination) != before or digest(source) != before:
        raise Problem("file-changed", f"File changed while backing up: {source.name}")
    return before


def verify(snapshot: Path):
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        raise Problem("incomplete-snapshot", "No completed snapshot manifest found.")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
        raise Problem("unsupported-version", "Unsupported snapshot format.")
    for relative, expected in manifest["files"].items():
        path = checked_path(snapshot, relative)
        if not path.is_file() or digest(path) != expected:
            raise Problem("snapshot-corrupt", f"Missing or changed snapshot file: {relative}")
    required = {"library.sqlite", "config.json", "rekordbox/master.db"}
    if not required.issubset(manifest["files"]):
        raise Problem("incomplete-snapshot", "Snapshot lacks required library files.")
    return manifest


def backup(library, destination=None):
    with locked(Rekordbox(library).path.parent):
        return _backup(library, destination)


def _backup(library, destination=None):
    destination = destination or library.config.get("backup_destination")
    if not destination:
        raise Problem(
            "destination-required",
            "No backup destination configured.",
            action="Run rbx backup --destination /path/to/cloud-folder.",
        )
    destination = Path(destination).expanduser().resolve()
    rb = Rekordbox(library)
    rb.require_closed()
    if destination.is_relative_to(library.root) or destination.is_relative_to(rb.path.parent):
        raise Problem(
            "invalid-path",
            "Backup destination must be outside the working library and Rekordbox directory.",
        )
    inventory = rb.inventory()
    snapshot_id = now().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
    staging = destination / (".pending-" + snapshot_id)
    staging.mkdir(parents=True)
    hashes, mappings, streaming = {}, {}, []

    def copy(source, relative):
        hashes[relative] = copy_verified(source, checked_path(staging, relative))

    library.db.commit()
    with sqlite3.connect(staging / "library.sqlite") as output:
        library.db.backup(output)
    hashes["library.sqlite"] = digest(staging / "library.sqlite")
    config = {**library.config, "tools": {}}
    atomic_json(staging / "config.json", config)
    hashes["config.json"] = digest(staging / "config.json")
    for track in library.rows("SELECT * FROM tracks"):
        source = checked_path(library.root, track["path"])
        if not source.is_file() or digest(source) != track["sha256"]:
            raise Problem(
                "missing-audio",
                "A managed track is missing or changed; incomplete snapshot retained for diagnosis.",
            )
        copy(source, "library/" + track["path"])
        mappings[str(source)] = "library/" + track["path"]
        adopted = library.setting("adopt_path:" + track["id"])
        if adopted:
            if Path(adopted).is_file() and digest(Path(adopted)) != track["sha256"]:
                raise Problem(
                    "adopted-audio-changed",
                    "An adopted file changed after import; resolve the replacement before backing up.",
                )
            mappings[adopted] = "library/" + track["path"]
    for track in inventory["tracks"]:
        raw_path = track["path"] or ""
        if not raw_path or "://" in raw_path:
            streaming.append(track["id"])
            continue
        if raw_path in mappings:
            continue
        source = Path(raw_path)
        if not source.is_file():
            raise Problem(
                "missing-audio",
                f"Rekordbox track {track['id']} has missing local audio.",
                action="Relocate it in Rekordbox before backing up.",
            )
        relative = "external/" + digest(source) + source.suffix.lower()
        if relative not in hashes:
            copy(source, relative)
        mappings[raw_path] = relative
    before = {}
    for source in rb.path.parent.rglob("*"):
        if source.name == ".lock":
            continue
        if source.is_symlink():
            raise Problem(
                "unsupported-symlink",
                "Rekordbox bundle contains a symlink; snapshot requires a resolved bundle.",
            )
        if source.is_file():
            relative = "rekordbox/" + str(source.relative_to(rb.path.parent))
            copy(source, relative)
            before[str(source)] = hashes[relative]
    rb.require_closed()
    for source, expected in before.items():
        if not Path(source).is_file() or digest(Path(source)) != expected:
            raise Problem(
                "rekordbox-changed",
                "Rekordbox files changed during backup; retry with Rekordbox closed.",
            )
    manifest = {
        "version": 1,
        "id": snapshot_id,
        "created": now(),
        "files": hashes,
        "audio_paths": mappings,
        "streaming_only_ids": streaming,
        "source_library": str(library.root),
        "source_rekordbox": str(rb.path.parent),
        "cloud_upload": "unverified",
    }
    atomic_json(staging / "manifest.json", manifest)
    verify(staging)
    snapshot = destination / snapshot_id
    staging.rename(snapshot)
    result = {
        "snapshot": str(snapshot),
        "created": manifest["created"],
        "files": len(hashes),
        "verified": True,
        "cloud_upload": "unverified",
        "streaming_only_ids": streaming,
    }
    library.config["backup_destination"] = str(destination)
    library.save_config()
    library.set_setting("last_backup", result)
    return result


def restore(
    library,
    snapshot,
    *,
    target: Path,
    rekordbox_target: Path,
    replace_existing=False,
    preview=False,
):
    snapshot, target, rekordbox_target = (
        snapshot.resolve(),
        target.resolve(),
        rekordbox_target.resolve(),
    )
    manifest = verify(snapshot)
    for directory in (target, rekordbox_target):
        if directory == Path(directory.anchor) or directory == Path.home():
            raise Problem("invalid-path", "Restore target must be a dedicated library directory.")
        if snapshot.is_relative_to(directory) or directory.is_relative_to(snapshot):
            raise Problem(
                "invalid-path", "Restore targets must not contain or be inside the snapshot."
            )
    if (
        target == rekordbox_target
        or target.is_relative_to(rekordbox_target)
        or rekordbox_target.is_relative_to(target)
    ):
        raise Problem("invalid-path", "Library and Rekordbox targets must be separate directories.")
    if target.exists() and any(target.iterdir()):
        raise Problem(
            "destination-diverged",
            "The library restore target is not empty.",
            action="Restore to a new --to directory; existing libraries are never overwritten.",
        )
    rb_exists = rekordbox_target.exists() and any(rekordbox_target.iterdir())
    if rb_exists and not (rekordbox_target / "master.db").is_file():
        raise Problem(
            "invalid-path",
            "An existing Rekordbox target must contain master.db; unrelated directories cannot be replaced.",
        )
    if rb_exists and not replace_existing:
        raise Problem(
            "destination-diverged",
            "The Rekordbox target contains an existing library.",
            action="Choose an empty --rekordbox-dir, or explicitly use --replace-existing to retain it as a recovery directory.",
        )
    if preview:
        return {
            "preview": True,
            "snapshot": str(snapshot),
            "library": str(target),
            "rekordbox": str(rekordbox_target),
            "files": len(manifest["files"]),
            "will_retain_existing_rekordbox": rb_exists,
        }
    Rekordbox(library).require_closed()
    target.mkdir(parents=True, exist_ok=True)
    rb_staging = rekordbox_target.parent / (".rbx-restore-" + uuid.uuid4().hex)
    rb_staging.mkdir(parents=True)
    for relative in manifest["files"]:
        source = checked_path(snapshot, relative)
        if relative.startswith("rekordbox/"):
            destination = checked_path(rb_staging, relative.removeprefix("rekordbox/"))
        elif relative.startswith("library/"):
            destination = checked_path(target, relative.removeprefix("library/"))
        else:
            destination = checked_path(target, relative)
        copy_verified(source, destination)
    paths = {}
    for old_path, relative in manifest["audio_paths"].items():
        if relative not in manifest["files"]:
            raise Problem("invalid-manifest", "Audio mapping refers to an unverified file.")
        paths[old_path] = str(checked_path(target, relative.removeprefix("library/")))
    with sqlite3.connect(target / "library.sqlite") as db:
        db.row_factory = sqlite3.Row
        for row in db.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'adopt_path:%'"
        ).fetchall():
            original = json.loads(row["value"])
            if original in paths:
                db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (json.dumps(paths[original]), row["key"]),
                )
    response = bridge(
        library,
        "rekordbox",
        {"operation": "relocate", "db": str(rb_staging / "master.db"), "paths": paths},
    )
    inventory = bridge(
        library, "rekordbox", {"operation": "inventory", "db": str(rb_staging / "master.db")}
    )
    for track in inventory["tracks"]:
        if (
            track["id"] not in manifest["streaming_only_ids"]
            and not Path(track["path"] or "").is_file()
        ):
            raise Problem(
                "restore-verification-failed", "A restored Rekordbox track is missing audio."
            )
    Rekordbox(library).require_closed()
    retained = None
    if rb_exists:
        retained = rekordbox_target.with_name(
            rekordbox_target.name + ".before-rbx-" + uuid.uuid4().hex[:8]
        )
        rekordbox_target.rename(retained)
    elif rekordbox_target.exists():
        rekordbox_target.rmdir()
    rb_staging.rename(rekordbox_target)
    config = json.loads((target / "config.json").read_text())
    config["tools"] = library.config["tools"]
    config["rekordbox_db"] = str(rekordbox_target / "master.db")
    atomic_json(target / "config.json", config)
    return {
        "library": str(target),
        "rekordbox": str(rekordbox_target),
        "retained_previous_library": str(retained) if retained else None,
        "relocated": response["relocated"],
        "verified": True,
        "next": f"Use rbx --library {target}; open Rekordbox with the restored database location.",
    }
