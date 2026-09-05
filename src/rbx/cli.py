from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .backup import backup, restore, verify
from .common import Problem, locked, redact
from .dependencies import doctor, install
from .service import Service
from .state import Library


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Problem(
            "invalid-arguments", message, action="Run rbx --help or rbx <command> --help."
        )


def options(parser):
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit versioned JSON on stdout",
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Never prompt (all commands support explicit arguments)",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=argparse.SUPPRESS,
        help="Library directory; also RBX_LIBRARY",
    )


def parser():
    root = Parser(
        prog="rbx", description="Follow playlists. Prepare your library. Keep a recoverable backup."
    )
    root.add_argument("--version", action="version", version=__version__)
    options(root)
    sub = root.add_subparsers(dest="command")

    def command(name, help):
        p = sub.add_parser(name, help=help, description=help)
        options(p)
        return p

    p = command("setup", "Configure the library and optionally install isolated downloader tools")
    p.add_argument("--install", action="store_true")
    p.add_argument("--rekordbox-db", type=Path)
    p.add_argument("--backup-destination", type=Path)
    p.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Override a tool executable or spotify-python/rekordbox-python",
    )
    command("doctor", "Check downloader, conversion, and Rekordbox dependencies")
    p = command(
        "follow", "Follow an online playlist; online order and membership own its Rekordbox mirror"
    )
    p.add_argument("url")
    p.add_argument("--name")
    p.add_argument(
        "--rekordbox-playlist-id",
        help="Explicitly take ownership of an existing Rekordbox playlist",
    )
    p.add_argument("--dry-run", action="store_true")
    p = command("unfollow", "Stop updates; retain audio and the existing Rekordbox playlist")
    p.add_argument("playlist")
    p = command(
        "status", "Show locally known readiness and backup status; use scan for online changes"
    )
    p.add_argument("--playlist")
    for name, help in [
        ("scan", "Inspect online changes without downloading or updating Rekordbox"),
        ("sync", "Acquire tracks and reconcile mirrors; rerun to resume"),
    ]:
        p = command(name, help)
        p.add_argument("--playlist")
        p.add_argument("--dry-run", action="store_true")
        if name == "sync":
            p.add_argument(
                "--offline",
                action="store_true",
                help="Apply cached memberships and local audio without contacting providers",
            )
    p = command(
        "import", "Copy external audio into the library; optionally add to a local playlist"
    )
    p.add_argument("path", type=Path)
    p.add_argument("--playlist")
    p.add_argument("--dry-run", action="store_true")
    p = command(
        "adopt", "Adopt existing Rekordbox audio while preserving original paths and preparation"
    )
    p.add_argument("--dry-run", action="store_true")
    command("tracks", "List shared library tracks and stable IDs")
    p = command("issues", "List or resolve persistent acquisition issues")
    issue_sub = p.add_subparsers(dest="issue_command", required=True)
    p = issue_sub.add_parser("list", help="Show unresolved entries")
    options(p)
    p.add_argument("--all", action="store_true")
    for name in ("ignore", "retry", "resolve"):
        p = issue_sub.add_parser(name)
        options(p)
        p.add_argument("id")
        if name == "resolve":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--file", type=Path)
            group.add_argument(
                "--url", help="Explicitly select an alternate SoundCloud or YouTube recording"
            )
            group.add_argument(
                "--track-id", help="Explicitly confirm an existing library track as the match"
            )
    p = command(
        "backup", "Create and verify a complete local snapshot; cloud upload remains unverified"
    )
    p.add_argument("--destination", type=Path)
    p = command("verify", "Verify all files in a completed snapshot")
    p.add_argument("snapshot", type=Path)
    p = command(
        "restore", "Restore to a new library directory and an explicit Rekordbox destination"
    )
    p.add_argument("snapshot", type=Path)
    p.add_argument("--to", type=Path, required=True)
    p.add_argument("--rekordbox-dir", type=Path, required=True)
    p.add_argument(
        "--replace-existing",
        action="store_true",
        help="Retain the previous Rekordbox directory alongside the restored one",
    )
    p.add_argument("--dry-run", action="store_true")
    return root


def dispatch(args, library):
    service = Service(library, progress=lambda message: print(message, file=sys.stderr))
    command = args.command or "status"
    if command == "setup":
        if args.rekordbox_db:
            if args.rekordbox_db.name != "master.db":
                raise Problem("invalid-path", "Rekordbox database must be named master.db.")
            library.config["rekordbox_db"] = str(args.rekordbox_db.expanduser().resolve())
        if args.backup_destination:
            library.config["backup_destination"] = str(
                args.backup_destination.expanduser().resolve()
            )
        for item in args.tool:
            name, separator, value = item.partition("=")
            if not separator or name not in {
                "spotdl",
                "yt-dlp",
                "ffmpeg",
                "ffprobe",
                "spotify-python",
                "rekordbox-python",
            }:
                raise Problem("invalid-tool", "Use --tool NAME=PATH for a documented tool.")
            library.config["tools"][name] = os.path.abspath(Path(value).expanduser())
        library.save_config()
        checks = install(library) if args.install else doctor(library)
        return {
            "library": str(library.root),
            "checks": checks,
            "next": "Follow a playlist or use adopt --dry-run to inspect existing Rekordbox tracks.",
        }
    if command == "doctor":
        return {"checks": doctor(library)}
    if command == "follow":
        return service.follow(args.url, args.name, args.rekordbox_playlist_id, args.dry_run)
    if command == "unfollow":
        playlist = library.playlists(args.playlist)[0]
        library.db.execute("UPDATE playlists SET active=0 WHERE id=?", (playlist["id"],))
        library.db.commit()
        return {
            "id": playlist["id"],
            "status": "unfollowed",
            "retained": "audio, preparation, Rekordbox playlist",
        }
    if command == "status":
        return service.status(getattr(args, "playlist", None))
    if command == "scan":
        return {"preview": True, "changes": service.scan(args.playlist, preview=True)}
    if command == "sync":
        return service.sync(args.playlist, preview=args.dry_run, offline=args.offline)
    if command == "tracks":
        return {"tracks": library.rows("SELECT * FROM tracks ORDER BY title")}
    if command == "import":
        path = args.path.expanduser().resolve()
        from .audio import AUDIO_SUFFIXES

        paths = (
            sorted(p for p in path.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES)
            if path.is_dir()
            else [path]
        )
        if args.dry_run:
            return {"preview": True, "files": [str(p) for p in paths], "playlist": args.playlist}
        tracks, errors = [], []
        for path in paths:
            try:
                tracks.append(service.import_file(path, args.playlist))
            except Problem as exc:
                errors.append({"file": str(path), **exc.as_dict()})
        return {
            "tracks": tracks,
            "errors": errors,
            "next": "Run rbx sync --offline to import into Rekordbox.",
        }
    if command == "adopt":
        return service.adopt(args.dry_run)
    if command == "issues":
        if args.issue_command == "list":
            return {"issues": service.issues(args.all)}
        file = getattr(args, "file", None)
        return service.resolve_issue(
            args.id,
            action=args.issue_command,
            file=file.expanduser().resolve() if file else None,
            track_id=getattr(args, "track_id", None),
            url=getattr(args, "url", None),
        )
    if command == "backup":
        return backup(library, args.destination)
    if command == "verify":
        manifest = verify(args.snapshot.expanduser().resolve())
        return {"verified": True, "files": len(manifest["files"]), "cloud_upload": "unverified"}
    if command == "restore":
        return restore(
            library,
            args.snapshot.expanduser(),
            target=args.to.expanduser(),
            rekordbox_target=args.rekordbox_dir.expanduser(),
            replace_existing=args.replace_existing,
            preview=args.dry_run,
        )
    raise Problem("unknown-command", command)


def partial(data):
    if data.get("preview"):
        return any("error" in change for change in data.get("changes", []))
    if data.get("errors") or any(not c["ok"] for c in data.get("checks", [])):
        return True
    if any("error" in change for change in data.get("changes", [])):
        return True
    status = data.get("status", data)
    return any(
        p["unresolved"]
        or p["missing_audio"] > p["ignored"]
        or p["membership_pending"]
        or p["error"]
        for p in status.get("playlists", [])
    )


def human(data):
    status = data.get("status", data)
    if "playlists" in status:
        print("Rekordbox Manager")
        for p in status["playlists"]:
            print(f"\n{p['name']} — {p['entries']} entries")
            print(
                f"  {p['downloaded']} downloaded · {p['imported']} in collection · {p['needs_analysis']} need analysis"
            )
            print(
                f"  {p['unresolved']} unresolved · {p['ignored']} ignored"
                + (" · playlist update pending" if p["membership_pending"] else "")
            )
            if p["error"]:
                print(f"  Source problem: {p['error']['message']}")
        if not status["playlists"]:
            print("\nNo followed playlists yet. Start with rbx follow <playlist-url>.")
        last = status["last_backup"]
        print(f"\nBackup: {last['created'] if last else 'none'} · cloud upload unverified")
        print("\nNext: rbx scan · rbx sync · rbx issues list · rbx backup")
        for error in data.get("errors", []):
            print(f"\n{error['code']}: {error['message']}")
    elif "checks" in data:
        print("Dependency checks")
        for check in data["checks"]:
            print(
                f"  {'OK' if check['ok'] else 'FAIL':4} {check['tool']:18} {check.get('version') or check.get('message', '')}"
            )
        if data.get("next"):
            print(f"\n{data['next']}")
    elif "issues" in data:
        if not data["issues"]:
            print("No unresolved issues.")
        for issue in data["issues"]:
            print(f"\n{issue['title']} · {issue['code']} · {issue['status']}")
            print(f"  {issue['message']}\n  ID: {issue['id']}")
            print(f"  Playlists: {', '.join(issue['playlists']) or 'no active playlists'}")
        if data["issues"]:
            print(
                "\nNext: rbx issues retry <id> · rbx issues ignore <id> · rbx issues resolve <id> --file <path>"
            )
    elif "changes" in data:
        if not data["changes"]:
            print("No online playlists to scan.")
        for change in data["changes"]:
            if "error" in change:
                print(f"{change['name']}: {change['error']['message']}")
            else:
                print(
                    f"{change['name']} · {len(change['added'])} new · {len(change['removed'])} removed · {'reordered' if change['reordered'] else 'order unchanged'}"
                )
        print("\nPreview only. Run rbx sync to apply.")
    elif "tracks" in data:
        for track in data["tracks"]:
            print(f"{track.get('title', track.get('rb_id', 'Track'))} · {track.get('artist', '')}")
            print(
                f"  {track.get('id', '')} · {track.get('status') or ('in Rekordbox' if track.get('rb_id') else 'pending import')}"
            )
        for error in data.get("errors", []):
            print(f"{error['code']}: {error['message']}")
        if data.get("next") or data.get("note"):
            print(f"\n{data.get('next') or data.get('note')}")
    else:
        for key, value in data.items():
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value) or "none"
            print(f"{key.replace('_', ' ').capitalize()}: {value}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    try:
        args = parser().parse_args(argv)
        root = (
            getattr(
                args, "library", Path(os.environ.get("RBX_LIBRARY", "~/Music/RekordboxManager"))
            )
            .expanduser()
            .resolve()
        )
        with locked(root):
            library = Library(root, create=args.command == "setup")
            try:
                data = dispatch(args, library)
            finally:
                library.close()
        code = 2 if partial(data) else 0
        if json_mode:
            print(
                json.dumps(
                    {"schema_version": 1, "status": "partial" if code else "ok", "data": data},
                    ensure_ascii=False,
                )
            )
        else:
            human(data)
        return code
    except KeyboardInterrupt:
        problem = Problem("interrupted", "Stopped. Completed work is retained; rerun to resume.")
        code = 130
    except Problem as exc:
        problem, code = exc, 1
    except (OSError, ValueError, sqlite3.Error) as exc:
        problem, code = Problem("operation-failed", redact(str(exc))), 1
    if json_mode:
        print(json.dumps({"schema_version": 1, "status": "error", "error": problem.as_dict()}))
    else:
        print(f"{problem.code}: {problem.message}", file=sys.stderr)
        if problem.action:
            print(problem.action, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
