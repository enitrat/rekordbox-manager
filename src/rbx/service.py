from __future__ import annotations

import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audio import AUDIO_SUFFIXES, store_audio
from .common import Problem, checked_path, digest, now
from .providers import Providers, provider_for
from .rekordbox import Rekordbox


class Service:
    def __init__(self, library, *, providers=None, rekordbox=None, progress=lambda message: None):
        self.library = library
        self.providers = providers or Providers(library)
        self.rekordbox = rekordbox or Rekordbox(library)
        self.progress = progress

    def save_playlist(self, snapshot, *, name=None, rb_id=None):
        lib = self.library
        existing = lib.one("SELECT * FROM playlists WHERE id=?", (snapshot["id"],))
        name = name or (existing["name"] if existing else snapshot["name"])
        collision = lib.one(
            "SELECT id FROM playlists WHERE name=? AND id!=?", (name, snapshot["id"])
        )
        if collision:
            raise Problem(
                "name-conflict",
                f"A different followed playlist is already called {name}.",
                action="Choose another --name.",
            )
        with lib.db:
            lib.db.execute(
                """INSERT INTO playlists (id,provider,provider_id,url,name,rb_id,last_scan)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET active=1,last_scan=excluded.last_scan,
                error=NULL,name=excluded.name,rb_id=COALESCE(excluded.rb_id,playlists.rb_id)""",
                (
                    snapshot["id"],
                    snapshot["provider"],
                    snapshot["provider_id"],
                    snapshot["url"],
                    name,
                    rb_id,
                    now(),
                ),
            )
            for entry in snapshot["entries"]:
                lib.db.execute(
                """INSERT INTO sources (id,provider,provider_id,url,title,artist,duration)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=CASE WHEN excluded.title=excluded.provider_id
                    AND sources.title!=sources.provider_id THEN sources.title
                    ELSE excluded.title END,
                artist=CASE WHEN excluded.title=excluded.provider_id AND sources.artist!=''
                    THEN sources.artist ELSE excluded.artist END,
                duration=COALESCE(excluded.duration,sources.duration),
                url=CASE WHEN excluded.title=excluded.provider_id
                    AND sources.title!=sources.provider_id THEN sources.url
                    ELSE excluded.url END""",
                    (
                        entry["id"],
                        entry["provider"],
                        entry["provider_id"],
                        entry["url"],
                        entry["title"],
                        entry.get("artist", ""),
                        entry.get("duration"),
                    ),
                )
            lib.db.execute("DELETE FROM memberships WHERE playlist_id=?", (snapshot["id"],))
            lib.db.executemany(
                "INSERT INTO memberships VALUES (?,?,?)",
                [
                    (snapshot["id"], entry["id"], position)
                    for position, entry in enumerate(snapshot["entries"], 1)
                ],
            )

    def follow(self, url, name=None, rb_id=None, preview=False):
        snapshot = self.providers.playlist(url)
        existing_rb = None
        if rb_id:
            inventory = self.rekordbox.inventory()
            existing_rb = next((p for p in inventory["playlists"] if p["id"] == rb_id), None)
            if existing_rb is None:
                raise Problem("not-found", "Requested Rekordbox playlist ID does not exist.")
            if self.library.one(
                "SELECT id FROM playlists WHERE rb_id=? AND id!=?", (rb_id, snapshot["id"])
            ):
                raise Problem(
                    "mapping-conflict", "That Rekordbox playlist already mirrors another source."
                )
        if not preview:
            self.save_playlist(snapshot, name=name, rb_id=rb_id)
            if existing_rb and existing_rb.get("content_ids"):
                self.library.set_setting(
                    "reconciliation:" + snapshot["id"],
                    {
                        "state": "required",
                        "rb_content_ids": existing_rb["content_ids"],
                    },
                )
        requires_reconciliation = bool(existing_rb and existing_rb.get("content_ids"))
        return {
            "playlist": name or snapshot["name"],
            "id": snapshot["id"],
            "entries": len(snapshot["entries"]),
            "preview": preview,
            "rekordbox_playlist": (
                {
                    "id": rb_id,
                    "existing_entries": len(existing_rb.get("content_ids", [])),
                }
                if rb_id and existing_rb
                else None
            ),
            "status": (
                "reconciliation-required" if requires_reconciliation else "followed"
            ),
            "ownership": (
                "Confirmed online memberships follow the online playlist; unmatched local memberships are preserved."
            ),
            "next": (
                f'rbx reconcile "{name or snapshot["name"]}"'
                if requires_reconciliation
                else "rbx sync"
            ),
            "duplicate_occurrences_collapsed": snapshot.get("duplicate_occurrences", 0),
        }

    def scan(self, selector=None, preview=False):
        reports = []
        for playlist in self.library.playlists(selector):
            if playlist["provider"] == "local":
                continue
            self.progress(f"Scanning {playlist['name']}…")
            try:
                snapshot = self.providers.playlist(playlist["url"])
                if snapshot["id"] != playlist["id"]:
                    raise Problem(
                        "source-changed", "Playlist URL resolves to a different identity."
                    )
                previous = [
                    r["source_id"]
                    for r in self.library.rows(
                        "SELECT source_id FROM memberships WHERE playlist_id=? ORDER BY position",
                        (playlist["id"],),
                    )
                ]
                current = [e["id"] for e in snapshot["entries"]]
                reports.append(
                    {
                        "id": playlist["id"],
                        "name": playlist["name"],
                        "added": [s for s in current if s not in previous],
                        "removed": [s for s in previous if s not in current],
                        "reordered": [s for s in previous if s in current]
                        != [s for s in current if s in previous],
                        "entries": len(current),
                    }
                )
                if not preview:
                    self.save_playlist(snapshot)
            except Problem as exc:
                reports.append(
                    {"id": playlist["id"], "name": playlist["name"], "error": exc.as_dict()}
                )
                if not preview:
                    self.library.db.execute(
                        "UPDATE playlists SET error=? WHERE id=?",
                        (json.dumps(exc.as_dict()), playlist["id"]),
                    )
                    self.library.db.commit()
        return reports

    def reconcile(
        self, selector, *, apply=False, accept_suggestions=False, matches=None, offline=False
    ):
        if not offline:
            report = self.scan(selector)[0]
            if "error" in report:
                error = report["error"]
                raise Problem(
                    error["code"],
                    error["message"],
                    action=error.get("action", ""),
                    details=error.get("details"),
                )
        playlist = self.library.playlists(selector)[0]
        if not playlist["rb_id"]:
            raise Problem(
                "reconciliation-not-needed",
                "This followed playlist is not linked to an existing Rekordbox playlist.",
                action="Run rbx sync to create its Rekordbox mirror.",
            )
        inventory = self.rekordbox.inventory()
        rb_playlist = next(
            (item for item in inventory["playlists"] if item["id"] == playlist["rb_id"]),
            None,
        )
        if rb_playlist is None:
            raise Problem("not-found", "The linked Rekordbox playlist no longer exists.")
        content = {item["id"]: item for item in inventory["tracks"]}
        current_ids = rb_playlist.get("content_ids", [])
        content_hashes = {}
        for content_id in current_ids:
            path = Path(content.get(content_id, {}).get("path") or "")
            if path.is_file():
                try:
                    content_hashes[content_id] = digest(path)
                except OSError:
                    pass
        sources = self.library.rows(
            """SELECT s.* FROM memberships m JOIN sources s ON s.id=m.source_id
            WHERE m.playlist_id=? ORDER BY m.position""",
            (playlist["id"],),
        )
        metadata_enriched = 0
        for source in sources:
            if (
                offline
                or source["provider"] != "soundcloud"
                or not self.placeholder_title(source)
            ):
                continue
            try:
                resolved = self.providers.metadata(source)
            except Problem:
                continue
            title = resolved.get("title") or source["title"]
            artist = resolved.get("artist") or source["artist"]
            if self.normalized(title) == self.normalized(source["title"]) and not artist:
                continue
            with self.library.db:
                self.library.db.execute(
                    """UPDATE sources SET title=?,artist=?,duration=COALESCE(?,duration),
                    url=? WHERE id=?""",
                    (
                        title,
                        artist,
                        resolved.get("duration"),
                        resolved.get("url") or source["url"],
                        source["id"],
                    ),
                )
                if source.get("track_id"):
                    track = self.library.one(
                        "SELECT title,artist FROM tracks WHERE id=?", (source["track_id"],)
                    )
                    if track and self.placeholder_value(track["title"], source["provider_id"]):
                        self.library.db.execute(
                            "UPDATE tracks SET title=?,artist=? WHERE id=?",
                            (title, artist, source["track_id"]),
                        )
            metadata_enriched += 1
        if metadata_enriched:
            sources = self.library.rows(
                """SELECT s.* FROM memberships m JOIN sources s ON s.id=m.source_id
                WHERE m.playlist_id=? ORDER BY m.position""",
                (playlist["id"],),
            )
        exact, suggested, ambiguous, online_only = [], [], [], []
        claimed = set()
        for source in sources:
            track = (
                self.library.one("SELECT * FROM tracks WHERE id=?", (source["track_id"],))
                if source["track_id"]
                else None
            )
            if track and track["rb_id"] in current_ids:
                exact.append(self.match_result(source, content[track["rb_id"]], "confirmed"))
                claimed.add(track["rb_id"])
                continue
            hash_candidates = [
                content[content_id]
                for content_id in current_ids
                if content_id not in claimed
                and track
                and content_hashes.get(content_id) == track["sha256"]
            ]
            if len(hash_candidates) == 1:
                exact.append(self.match_result(source, hash_candidates[0], "file-hash"))
                claimed.add(hash_candidates[0]["id"])
                continue
            if len(hash_candidates) > 1:
                ambiguous.append(
                    {
                        "source_id": source["id"],
                        "title": source["title"],
                        "artist": source["artist"],
                        "candidates": [
                            self.match_result(source, item, "file-hash")
                            for item in hash_candidates
                        ],
                    }
                )
                claimed.update(item["id"] for item in hash_candidates)
                continue
            candidates = [
                item
                for content_id in current_ids
                if content_id not in claimed
                and (item := content.get(content_id))
                and self.suggested_match(source, item)
            ]
            if len(candidates) == 1:
                suggested.append(self.match_result(source, candidates[0], "metadata"))
                claimed.add(candidates[0]["id"])
            elif len(candidates) > 1:
                ambiguous.append(
                    {
                        "source_id": source["id"],
                        "title": source["title"],
                        "artist": source["artist"],
                        "candidates": [
                            self.match_result(source, item, "metadata") for item in candidates
                        ],
                    }
                )
                claimed.update(item["id"] for item in candidates)
            else:
                online_only.append(
                    {
                        "source_id": source["id"],
                        "title": source["title"],
                        "artist": source["artist"],
                    }
                )
        local_only = [
            {
                "rb_content_id": content_id,
                "title": content.get(content_id, {}).get("title", ""),
                "artist": content.get(content_id, {}).get("artist", ""),
            }
            for content_id in current_ids
            if content_id not in claimed
        ]
        plan = {
            "playlist": playlist["name"],
            "playlist_id": playlist["id"],
            "rekordbox_playlist_id": playlist["rb_id"],
            "status": "reconciliation-required",
            "exact": exact,
            "suggested": suggested,
            "ambiguous": ambiguous,
            "online_only": online_only,
            "local_only": local_only,
            "metadata_enriched": metadata_enriched,
            "preview": not apply,
        }
        if not apply:
            return plan

        explicit = matches or {}
        accepted = list(exact)
        if accept_suggestions:
            accepted.extend(suggested)
        for source_id, rb_content_id in explicit.items():
            source = next((item for item in sources if item["id"] == source_id), None)
            rb_content = content.get(rb_content_id)
            if source is None or rb_content_id not in current_ids or rb_content is None:
                raise Problem(
                    "invalid-match",
                    f"Cannot match {source_id} to Rekordbox content {rb_content_id}.",
                )
            accepted = [item for item in accepted if item["source_id"] != source_id]
            accepted.append(self.match_result(source, rb_content, "explicit"))
        accepted_by_source = {item["source_id"]: item for item in accepted}
        for source in sources:
            match = accepted_by_source.get(source["id"])
            if not match:
                continue
            track = self.adopt_rekordbox_content(content[match["rb_content_id"]])
            self.library.db.execute(
                "UPDATE sources SET track_id=? WHERE id=?", (track["id"], source["id"])
            )
            self.library.db.execute(
                "UPDATE issues SET status='resolved',updated=? WHERE source_id=?",
                (now(), source["id"]),
            )
        self.library.db.commit()
        accepted_content_ids = {
            item["rb_content_id"] for item in accepted_by_source.values()
        }
        pending = [
            source["id"]
            for source in sources
            if source["id"] not in accepted_by_source
            and any(
                item["source_id"] == source["id"] for item in suggested + ambiguous
            )
        ]
        self.library.set_setting(
            "reconciliation:" + playlist["id"],
            {
                "state": "active",
                "pending_sources": pending,
                "preserve_content_ids": [
                    content_id for content_id in current_ids if content_id not in accepted_content_ids
                ],
            },
        )
        result = self.sync(playlist["id"], refresh=False)
        stale = next(
            (error for error in result["errors"] if error["code"] == "reconciliation-stale"),
            None,
        )
        if stale:
            reconciliation = self.library.setting("reconciliation:" + playlist["id"], {})
            reconciliation["state"] = "required"
            self.library.set_setting("reconciliation:" + playlist["id"], reconciliation)
        return {
            **plan,
            "status": "reconciliation-stale" if stale else "reconciled",
            "preview": False,
            "accepted": list(accepted_by_source.values()),
            "pending_sources": pending,
            "sync": result,
        }

    @staticmethod
    def normalized(value):
        return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))

    @classmethod
    def normalized_title(cls, title, artist):
        normalized = cls.normalized(title)
        prefix = cls.normalized(artist)
        if prefix and normalized.startswith(prefix + " "):
            return normalized[len(prefix) + 1 :]
        return normalized

    @classmethod
    def placeholder_value(cls, value, provider_id):
        candidate = str(value or "").strip().casefold()
        provider_id = str(provider_id or "").strip().casefold()
        return candidate in {
            provider_id,
            "soundcloud:" + provider_id,
        }

    @classmethod
    def placeholder_title(cls, source):
        return cls.placeholder_value(source.get("title"), source.get("provider_id"))

    def suggested_match(self, source, content):
        if not source["title"] or not source["artist"]:
            return False
        if self.normalized_title(source["title"], source["artist"]) != self.normalized_title(
            content.get("title"), content.get("artist")
        ):
            return False
        if self.normalized(source["artist"]) != self.normalized(content.get("artist")):
            return False
        source_duration = source.get("duration")
        content_duration = content.get("duration")
        return (
            source_duration is not None
            and content_duration is not None
            and abs(float(source_duration) - float(content_duration)) <= 3
        )

    @staticmethod
    def match_result(source, content, evidence):
        return {
            "source_id": source["id"],
            "rb_content_id": content["id"],
            "title": content.get("title", ""),
            "artist": content.get("artist", ""),
            "duration": content.get("duration"),
            "evidence": evidence,
        }

    def adopt_rekordbox_content(self, content):
        existing = self.library.one("SELECT * FROM tracks WHERE rb_id=?", (content["id"],))
        if existing:
            return existing
        path = Path(content.get("path") or "")
        if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
            raise Problem(
                "missing-local-audio",
                f'Rekordbox content "{content.get("title") or content["id"]}" has no usable local audio.',
            )
        track = store_audio(
            self.library,
            path,
            title=content.get("title", ""),
            artist=content.get("artist", ""),
            rb_id=content["id"],
        )
        self.library.db.execute(
            "UPDATE tracks SET rb_id=? WHERE id=?", (content["id"], track["id"])
        )
        self.library.db.commit()
        self.library.set_setting("adopt_path:" + track["id"], str(path))
        return self.library.one("SELECT * FROM tracks WHERE id=?", (track["id"],))

    def acquire(self, source):
        lib = self.library
        if source["track_id"]:
            track = lib.one("SELECT * FROM tracks WHERE id=?", (source["track_id"],))
            path = checked_path(lib.root, track["path"])
            if path.is_file():
                return track
        attempt = lib.db.execute(
            "INSERT INTO attempts (source_id,started,status) VALUES (?,?,'pending')",
            (source["id"], now()),
        ).lastrowid
        lib.db.commit()
        try:
            acquisition = self.providers.resolve(source)
            lib.db.execute(
                "UPDATE sources SET acquisition_id=?,acquisition_url=?,downloader=?,settings=? WHERE id=?",
                (
                    acquisition["id"],
                    acquisition["url"],
                    acquisition["downloader"],
                    json.dumps(acquisition["settings"]),
                    source["id"],
                ),
            )
            lib.db.commit()
            existing = lib.one(
                """SELECT t.* FROM tracks t JOIN sources s ON s.track_id=t.id
                WHERE s.acquisition_id=? LIMIT 1""",
                (acquisition["id"],),
            )
            if existing and checked_path(lib.root, existing["path"]).is_file():
                track = existing
            else:
                with tempfile.TemporaryDirectory(prefix="rbx-download-") as temporary:
                    audio = self.providers.download(acquisition, Path(temporary))
                    if source["track_id"]:
                        old = lib.one("SELECT * FROM tracks WHERE id=?", (source["track_id"],))
                        if digest(audio) != old["sha256"]:
                            raise Problem(
                                "source-changed",
                                "Reacquired audio differs from the prepared file; restore from backup or review replacement.",
                            )
                    track = store_audio(
                        lib,
                        audio,
                        title=acquisition.get("title") or source["title"],
                        artist=acquisition.get("artist") or source["artist"],
                        expected_duration=source.get("duration") or acquisition.get("duration"),
                    )
            with lib.db:
                lib.db.execute(
                    "UPDATE sources SET track_id=? WHERE id=?", (track["id"], source["id"])
                )
                lib.db.execute(
                    "UPDATE issues SET status='resolved',updated=? WHERE source_id=?",
                    (now(), source["id"]),
                )
                lib.db.execute(
                    "UPDATE attempts SET status='complete',acquisition_url=? WHERE id=?",
                    (acquisition["url"], attempt),
                )
            return track
        except Problem as exc:
            lib.db.execute(
                "UPDATE attempts SET status='failed',code=? WHERE id=?", (exc.code, attempt)
            )
            lib.issue(source["id"], exc)
            raise

    def sync(self, selector=None, *, preview=False, offline=False, refresh=True):
        lib = self.library
        scans = [] if offline or not refresh else self.scan(selector, preview=preview)
        if preview:
            return {"preview": True, "changes": scans, "status": self.status(selector)}
        failed = {r["id"] for r in scans if "error" in r}
        playlists = [p for p in lib.playlists(selector) if p["id"] not in failed and not p["error"]]
        tracks, desired, errors = {}, [], []
        rb_inventory = None
        for playlist in playlists:
            reconciliation = lib.setting("reconciliation:" + playlist["id"])
            rb_playlist = None
            if playlist["rb_id"]:
                rb_inventory = rb_inventory or self.rekordbox.inventory()
                rb_playlist = next(
                    (
                        item
                        for item in rb_inventory["playlists"]
                        if item["id"] == playlist["rb_id"]
                    ),
                    None,
                )
            if reconciliation and reconciliation.get("state") == "required":
                errors.append(
                    {
                        "code": "reconciliation-required",
                        "message": (
                            f'Review "{playlist["name"]}" before changing its existing '
                            "Rekordbox membership."
                        ),
                        "action": f'rbx reconcile "{playlist["name"]}"',
                    }
                )
                continue
            if (
                playlist["provider"] != "local"
                and reconciliation is None
                and rb_playlist
                and rb_playlist.get("content_ids")
                and lib.setting("managed:" + playlist["id"]) is None
            ):
                lib.set_setting(
                    "reconciliation:" + playlist["id"],
                    {
                        "state": "required",
                        "rb_content_ids": rb_playlist["content_ids"],
                    },
                )
                errors.append(
                    {
                        "code": "reconciliation-required",
                        "message": (
                            f'Review legacy mapping "{playlist["name"]}" before its next sync.'
                        ),
                        "action": f'rbx reconcile "{playlist["name"]}"',
                    }
                )
                continue
            preserve_content_ids = []
            expected_content_ids = None
            if rb_playlist:
                expected_content_ids = rb_playlist.get("content_ids", [])
            if reconciliation and reconciliation.get("state") == "active":
                previous_managed = lib.setting("managed:" + playlist["id"])
                if rb_playlist and previous_managed is not None:
                    preserve_content_ids = [
                        content_id
                        for content_id in rb_playlist.get("content_ids", [])
                        if content_id not in previous_managed
                    ]
                else:
                    preserve_content_ids = reconciliation.get("preserve_content_ids", [])
            members = lib.rows(
                """SELECT s.* FROM memberships m JOIN sources s ON s.id=m.source_id
                WHERE m.playlist_id=? ORDER BY m.position""",
                (playlist["id"],),
            )
            track_ids = []
            for source in members:
                if source["id"] in (reconciliation or {}).get("pending_sources", []):
                    continue
                issue = lib.one("SELECT * FROM issues WHERE source_id=?", (source["id"],))
                if issue:
                    if issue["status"] == "ignored":
                        continue
                    if issue["status"] == "open" and not self.should_retry(issue):
                        continue
                self.progress(f"Preparing {source['title']}…")
                try:
                    if offline and (
                        not source["track_id"]
                        or not checked_path(
                            lib.root,
                            lib.one("SELECT path FROM tracks WHERE id=?", (source["track_id"],))[
                                "path"
                            ],
                        ).is_file()
                    ):
                        continue
                    track = self.acquire(source)
                    tracks[track["id"]] = self.rb_track(track)
                    if track["id"] not in track_ids:
                        track_ids.append(track["id"])
                except Problem as exc:
                    errors.append({"source_id": source["id"], **exc.as_dict()})
            desired.append(
                {
                    "id": playlist["id"],
                    "name": playlist["name"],
                    "rb_id": playlist["rb_id"],
                    "track_ids": track_ids,
                    "preserve_content_ids": preserve_content_ids,
                    "expected_content_ids": expected_content_ids,
                }
            )
        rb_result = None
        for track in lib.rows("SELECT * FROM tracks WHERE rb_id IS NULL"):
            if checked_path(lib.root, track["path"]).is_file():
                tracks.setdefault(track["id"], self.rb_track(track))
        if desired or tracks:
            try:
                rb_result = self.rekordbox.apply(list(tracks.values()), desired)
                with lib.db:
                    for track in rb_result["tracks"]:
                        lib.db.execute(
                            "UPDATE tracks SET rb_id=?,analysis=? WHERE id=?",
                            (track["rb_id"], track["analysis"], track["id"]),
                        )
                    for playlist in rb_result["playlists"]:
                        lib.db.execute(
                            "UPDATE playlists SET rb_id=? WHERE id=?",
                            (playlist["rb_id"], playlist["id"]),
                        )
                        if "managed_content_ids" in playlist:
                            lib.db.execute(
                                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                                (
                                    "managed:" + playlist["id"],
                                    json.dumps(playlist["managed_content_ids"]),
                                ),
                            )
                for playlist in desired:
                    lib.set_setting("applied:" + playlist["id"], playlist["track_ids"])
                (lib.root / "pending-rekordbox.json").unlink(missing_ok=True)
            except Problem as exc:
                errors.append(exc.as_dict())
        return {
            "changes": scans,
            "errors": errors,
            "rekordbox_updated": rb_result is not None,
            "status": self.status(selector),
        }

    def rb_track(self, track):
        adopted = self.library.setting("adopt_path:" + track["id"])
        path = checked_path(self.library.root, track["path"])
        if adopted:
            path = Path(adopted)
            if not path.is_file() or digest(path) != track["sha256"]:
                raise Problem(
                    "adopted-audio-changed",
                    "An adopted Rekordbox file is missing or changed; relocate or restore it first.",
                )
        return {**track, "path": str(path)}

    def status(self, selector=None):
        playlists = []
        for playlist in self.library.playlists(selector):
            rows = self.library.rows(
                """SELECT s.id,s.title,t.id AS track_id,t.path,t.rb_id,t.analysis,i.status AS issue_status
                FROM memberships m JOIN sources s ON s.id=m.source_id LEFT JOIN tracks t ON t.id=s.track_id
                LEFT JOIN issues i ON i.source_id=s.id WHERE m.playlist_id=? ORDER BY m.position""",
                (playlist["id"],),
            )
            available = [
                r
                for r in rows
                if r["path"] and checked_path(self.library.root, r["path"]).is_file()
            ]
            applied = self.library.setting("applied:" + playlist["id"], [])
            wanted = list(
                dict.fromkeys(
                    r["track_id"] for r in available if r["issue_status"] not in ("open", "ignored")
                )
            )
            playlists.append(
                {
                    "id": playlist["id"],
                    "name": playlist["name"],
                    "entries": len(rows),
                    "downloaded": len(available),
                    "imported": sum(bool(r["rb_id"]) for r in available),
                    "membership_pending": applied != wanted or not playlist["rb_id"],
                    "needs_analysis": sum(r["analysis"] == "needs-analysis" for r in available),
                    "unresolved": sum(r["issue_status"] == "open" for r in rows),
                    "ignored": sum(r["issue_status"] == "ignored" for r in rows),
                    "missing_audio": len(rows) - len(available),
                    "last_scan": playlist["last_scan"],
                    "error": json.loads(playlist["error"]) if playlist["error"] else None,
                }
            )
        return {
            "playlists": playlists,
            "tracks": self.library.db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
            "last_backup": self.library.setting("last_backup"),
            "readiness_note": "Import and analysis are last observed; open Rekordbox to analyze new tracks.",
        }

    def import_file(self, path, playlist=None):
        lib = self.library
        target = lib.one("SELECT * FROM playlists WHERE name=?", (playlist,)) if playlist else None
        if target and target["provider"] != "local":
            raise Problem(
                "online-owned",
                "Use issues resolve --file to fulfill an online entry; its membership is online-owned.",
            )
        track = store_audio(lib, path)
        sid = "local:" + track["sha256"]
        with lib.db:
            lib.db.execute(
                "INSERT OR IGNORE INTO sources (id,provider,provider_id,url,title,artist,duration,track_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    sid,
                    "local",
                    track["sha256"],
                    path.as_uri(),
                    track["title"],
                    track["artist"],
                    track["duration"],
                    track["id"],
                ),
            )
            if playlist:
                if not target:
                    pid = "local:" + str(uuid.uuid4())
                    lib.db.execute(
                        "INSERT INTO playlists (id,provider,provider_id,url,name) VALUES (?,'local',?,'',?)",
                        (pid, pid, playlist),
                    )
                else:
                    pid = target["id"]
                position = lib.db.execute(
                    "SELECT COALESCE(MAX(position),0)+1 FROM memberships WHERE playlist_id=?",
                    (pid,),
                ).fetchone()[0]
                lib.db.execute(
                    "INSERT OR IGNORE INTO memberships VALUES (?,?,?)", (pid, sid, position)
                )
        return track

    def issues(self, include_closed=False):
        issues = self.library.rows(
            """SELECT i.*,s.title,s.url,s.provider_id FROM issues i
            JOIN sources s ON s.id=i.source_id """
            + ("" if include_closed else "WHERE i.status='open' ")
            + "ORDER BY i.updated"
        )
        for issue in issues:
            if issue["title"] == issue["provider_id"]:
                issue["provider_title"] = issue["title"]
                issue["title"] = None
            issue["playlists"] = [
                p["name"]
                for p in self.library.rows(
                    "SELECT p.name FROM playlists p JOIN memberships m ON m.playlist_id=p.id WHERE m.source_id=? AND p.active=1 ORDER BY p.name",
                    (issue["source_id"],),
                )
            ]
        return issues

    def should_retry(self, issue):
        if issue["code"] not in ("timeout", "network-failed"):
            return False
        attempts = self.library.rows(
            "SELECT started FROM attempts WHERE source_id=? AND status='failed' ORDER BY id",
            (issue["source_id"],),
        )
        if not attempts or len(attempts) >= 3:
            return False
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(attempts[-1]["started"])
        ).total_seconds()
        return age >= 60 * len(attempts)

    def resolve_issue(self, issue_id, *, action, file=None, track_id=None, url=None):
        lib = self.library
        issue = lib.one("SELECT * FROM issues WHERE id=?", (issue_id,))
        if not issue:
            raise Problem("not-found", "No issue with that ID.")
        if action == "resolve":
            source = lib.one("SELECT * FROM sources WHERE id=?", (issue["source_id"],))
            if source["track_id"]:
                raise Problem(
                    "prepared-track",
                    "This entry already has a library track. Restore missing audio from backup; automatic replacement is disabled.",
                )
            if url:
                if provider_for(url) == "spotify":
                    raise Problem(
                        "invalid-source", "An alternate audio source must be SoundCloud or YouTube."
                    )
                lib.db.execute(
                    "UPDATE sources SET acquisition_id=NULL,acquisition_url=? WHERE id=?",
                    (url, issue["source_id"]),
                )
            else:
                track = (
                    store_audio(lib, file)
                    if file
                    else lib.one("SELECT * FROM tracks WHERE id=?", (track_id,))
                )
                if not track:
                    raise Problem("not-found", "No library track with that ID.")
                lib.db.execute(
                    "UPDATE sources SET track_id=? WHERE id=?", (track["id"], issue["source_id"])
                )
        status = {"resolve": "resolved", "retry": "retry", "ignore": "ignored"}[action]
        if url:
            status = "retry"
        lib.db.execute("UPDATE issues SET status=?,updated=? WHERE id=?", (status, now(), issue_id))
        lib.db.commit()
        return {"id": issue_id, "status": status, "next": "Run rbx sync to apply."}

    def adopt(self, preview=False):
        inventory = self.rekordbox.inventory()
        reports = []
        seen = {}
        for content in inventory["tracks"]:
            path = Path(content["path"] or "")
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                reports.append(
                    {"rb_id": content["id"], "status": "skipped", "reason": "not-local-audio"}
                )
                continue
            sha = digest(path)
            if sha in seen:
                reports.append(
                    {
                        "rb_id": content["id"],
                        "status": "review",
                        "reason": "duplicate-existing-content",
                        "other_rb_id": seen[sha],
                    }
                )
                continue
            seen[sha] = content["id"]
            if not preview:
                existing = self.library.one("SELECT * FROM tracks WHERE sha256=?", (sha,))
                if existing and existing["rb_id"] and existing["rb_id"] != content["id"]:
                    reports.append(
                        {
                            "rb_id": content["id"],
                            "status": "review",
                            "reason": "conflicting-existing-content",
                        }
                    )
                    continue
                track = store_audio(
                    self.library,
                    path,
                    title=content["title"],
                    artist=content["artist"],
                    rb_id=content["id"],
                )
                self.library.db.execute(
                    "UPDATE tracks SET rb_id=? WHERE id=?", (content["id"], track["id"])
                )
                self.library.db.commit()
                self.library.set_setting("adopt_path:" + track["id"], str(path))
            reports.append(
                {"rb_id": content["id"], "status": "would-adopt" if preview else "adopted"}
            )
        return {
            "tracks": reports,
            "preview": preview,
            "note": "Original paths and preparation retained; managed copies support backup and future matches.",
        }
