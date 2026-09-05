import json
import sqlite3
from pathlib import Path

import pytest

from rbx.backup import backup, restore, verify
from rbx.common import Problem, atomic_json, digest
from rbx.service import Service


@pytest.fixture
def bundle(library, audio, tmp_path, monkeypatch):
    rb_dir = tmp_path / "rekordbox"
    rb_dir.mkdir()
    with sqlite3.connect(rb_dir / "master.db") as db:
        db.execute("CREATE TABLE content(id,path,cues)")
        db.execute("INSERT INTO content VALUES ('rb1',?,'[1234,2345]')", (str(audio),))
    (rb_dir / "share").mkdir()
    (rb_dir / "share" / "analysis.DAT").write_bytes(b"analysis-fixture")
    (rb_dir / "masterPlaylists6.xml").write_text("<playlists/>")
    library.config["rekordbox_db"] = str(rb_dir / "master.db")
    library.save_config()

    def bridge(lib, kind, request):
        if request["operation"] == "doctor":
            return {"running": False}
        with sqlite3.connect(request["db"]) as db:
            if request["operation"] == "inventory":
                return {
                    "tracks": [
                        {"id": r[0], "path": r[1]}
                        for r in db.execute("SELECT id,path FROM content")
                    ],
                    "playlists": [],
                }
            if request["operation"] == "relocate":
                for old, new in request["paths"].items():
                    db.execute("UPDATE content SET path=? WHERE path=?", (new, old))
                return {"relocated": 1}
        raise AssertionError(request)

    monkeypatch.setattr("rbx.rekordbox.bridge", bridge)
    monkeypatch.setattr("rbx.backup.bridge", bridge)
    return rb_dir


def test_roundtrip_preserves_external_audio_and_cues(library, audio, bundle, tmp_path):
    Service(library).import_file(audio, "Set")
    result = backup(library, tmp_path / "cloud")
    snapshot = Path(result["snapshot"])
    manifest = verify(snapshot)
    assert manifest["cloud_upload"] == "unverified"
    restored = restore(
        library, snapshot, target=tmp_path / "new-library", rekordbox_target=tmp_path / "new-rb"
    )
    assert restored["verified"]
    with sqlite3.connect(tmp_path / "new-rb/master.db") as db:
        path, cues = db.execute("SELECT path,cues FROM content").fetchone()
    assert Path(path).exists()
    assert cues == "[1234,2345]"
    assert digest(Path(path)) == digest(audio)
    assert (tmp_path / "new-rb/share/analysis.DAT").read_bytes() == b"analysis-fixture"


def test_corruption_prevents_restore(library, audio, bundle, tmp_path):
    result = backup(library, tmp_path / "cloud")
    snapshot = Path(result["snapshot"])
    (snapshot / "rekordbox/master.db").write_bytes(b"broken")
    with pytest.raises(Problem, match="Missing or changed"):
        restore(
            library, snapshot, target=tmp_path / "new-library", rekordbox_target=tmp_path / "new-rb"
        )
    assert not (tmp_path / "new-library").exists()


def test_existing_destination_requires_explicit_replacement(library, bundle, tmp_path):
    snapshot = Path(backup(library, tmp_path / "cloud")["snapshot"])
    with pytest.raises(Problem, match="existing library"):
        restore(library, snapshot, target=tmp_path / "new-library", rekordbox_target=bundle)
    result = restore(
        library,
        snapshot,
        target=tmp_path / "new-library",
        rekordbox_target=bundle,
        replace_existing=True,
    )
    assert Path(result["retained_previous_library"]).is_dir()


def test_manifest_path_traversal_rejected(library, bundle, tmp_path):
    snapshot = Path(backup(library, tmp_path / "cloud")["snapshot"])
    manifest = json.loads((snapshot / "manifest.json").read_text())
    manifest["files"]["../../outside"] = "x"
    atomic_json(snapshot / "manifest.json", manifest)
    with pytest.raises(Problem, match="escapes"):
        verify(snapshot)
