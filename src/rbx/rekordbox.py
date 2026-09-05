from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from .common import Problem, atomic_json, locked, now
from .dependencies import bridge


class Rekordbox:
    def __init__(self, library):
        self.library = library

    @property
    def path(self):
        return Path(self.library.config["rekordbox_db"]).expanduser().resolve()

    def inventory(self):
        return bridge(self.library, "rekordbox", {"operation": "inventory", "db": str(self.path)})

    def require_closed(self):
        result = bridge(self.library, "rekordbox", {"operation": "doctor"})
        if result["running"]:
            raise Problem(
                "rekordbox-running", "Close Rekordbox and rerun; downloaded audio is retained."
            )

    def apply(self, tracks, playlists):
        with locked(self.path.parent):
            return self._apply(tracks, playlists)

    def _apply(self, tracks, playlists):
        self.require_closed()
        journal = self.library.root / "pending-rekordbox.json"
        if journal.exists():
            pending = json.loads(journal.read_text())
            if pending["db"] != str(self.path):
                raise Problem(
                    "pending-other-library",
                    "A pending import belongs to another Rekordbox database.",
                )
            inventory = self.inventory()
            existing_tracks = {t["id"]: t for t in inventory["tracks"]}
            existing_playlists = {p["id"]: p for p in inventory["playlists"]}
            with self.library.db:
                for local_id, rb_id in pending["tracks"].items():
                    local = self.library.one("SELECT path FROM tracks WHERE id=?", (local_id,))
                    if local and rb_id in existing_tracks:
                        expected = self.library.setting("adopt_path:" + local_id) or str(
                            self.library.root / local["path"]
                        )
                        if existing_tracks[rb_id]["path"] != expected:
                            raise Problem(
                                "pending-conflict",
                                "Pending track identity no longer matches its audio path.",
                            )
                        self.library.db.execute(
                            "UPDATE tracks SET rb_id=? WHERE id=?", (rb_id, local_id)
                        )
                for local_id, rb_id in pending["playlists"].items():
                    local = self.library.one("SELECT name FROM playlists WHERE id=?", (local_id,))
                    if local and rb_id in existing_playlists:
                        if existing_playlists[rb_id]["name"] != local["name"]:
                            raise Problem(
                                "pending-conflict", "Pending playlist was renamed externally."
                            )
                        self.library.db.execute(
                            "UPDATE playlists SET rb_id=? WHERE id=?", (rb_id, local_id)
                        )
            for track in tracks:
                saved = pending["tracks"].get(track["id"])
                if (
                    saved
                    and saved in existing_tracks
                    and existing_tracks[saved]["path"] == track["path"]
                ):
                    track["rb_id"] = saved
            for playlist in playlists:
                saved = pending["playlists"].get(playlist["id"])
                if saved and saved in existing_playlists:
                    if existing_playlists[saved]["name"] != playlist["name"]:
                        raise Problem(
                            "pending-conflict",
                            "A pending mirrored playlist was renamed externally.",
                        )
                    playlist["rb_id"] = saved
        recovery = self.library.root / "recovery" / str(uuid.uuid4())
        if self.library.root.is_relative_to(self.path.parent):
            raise Problem(
                "invalid-path", "CLI library must be outside the Rekordbox database directory."
            )
        recovery.mkdir(parents=True)
        for name in ("master.db", "master.db-wal", "master.db-shm", "masterPlaylists6.xml"):
            source = self.path.parent / name
            if source.is_file():
                shutil.copy2(source, recovery / name)
        atomic_json(
            recovery / "operation.json",
            {"created": now(), "db": str(self.path), "state": "pending", "playlists": playlists},
        )
        response = bridge(
            self.library,
            "rekordbox",
            {
                "operation": "apply",
                "db": str(self.path),
                "tracks": tracks,
                "playlists": playlists,
                "journal": str(journal),
            },
        )
        atomic_json(
            recovery / "operation.json",
            {"created": now(), "db": str(self.path), "state": "complete", "playlists": playlists},
        )
        return response
