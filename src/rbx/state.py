from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .common import Problem, atomic_json, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tracks (
 id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
 title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '', duration REAL,
 rb_id TEXT, analysis TEXT NOT NULL DEFAULT 'unknown', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
 url TEXT NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '', duration REAL,
 track_id TEXT REFERENCES tracks(id), acquisition_id TEXT, acquisition_url TEXT,
 downloader TEXT, settings TEXT, UNIQUE(provider, provider_id)
);
CREATE TABLE IF NOT EXISTS playlists (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
 url TEXT NOT NULL, name TEXT NOT NULL UNIQUE, rb_id TEXT,
 active INTEGER NOT NULL DEFAULT 1, last_scan TEXT, error TEXT,
 UNIQUE(provider, provider_id)
);
CREATE TABLE IF NOT EXISTS memberships (
 playlist_id TEXT NOT NULL REFERENCES playlists(id), source_id TEXT NOT NULL REFERENCES sources(id),
 position INTEGER NOT NULL, PRIMARY KEY(playlist_id, source_id)
);
CREATE TABLE IF NOT EXISTS issues (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE REFERENCES sources(id),
 code TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, started TEXT NOT NULL,
 status TEXT NOT NULL, code TEXT, acquisition_url TEXT
);
"""


class Library:
    def __init__(self, root: Path, *, create=False):
        self.root = root.expanduser().resolve()
        self.config_path = self.root / "config.json"
        if not self.config_path.exists() and not create:
            raise Problem(
                "not-configured", f"No library configured at {self.root}.", action="Run rbx setup."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = (
            json.loads(self.config_path.read_text())
            if self.config_path.exists()
            else {
                "version": 1,
                "rekordbox_db": str(Path.home() / "Library/Pioneer/rekordbox/master.db"),
                "backup_destination": None,
                "tools": {},
            }
        )
        if self.config.get("version") != 1:
            raise Problem("unsupported-version", "Unsupported library configuration version.")
        self.db = sqlite3.connect(self.root / "library.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise Problem("unsupported-version", "Unsupported library database version.")
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()

    def save_config(self):
        atomic_json(self.config_path, self.config)

    def close(self):
        self.db.close()

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def playlists(self, selector=None):
        rows = self.rows("SELECT * FROM playlists WHERE active=1 ORDER BY name")
        if selector:
            rows = [p for p in rows if selector in (p["id"], p["name"])]
            if not rows:
                raise Problem("not-found", f"No active playlist named {selector}.")
        return rows

    def issue(self, source_id, problem):
        self.db.execute(
            """INSERT INTO issues VALUES (?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET code=excluded.code,message=excluded.message,
            status='open',updated=excluded.updated""",
            (source_id, source_id, problem.code, problem.message, "open", now()),
        )
        self.db.commit()
