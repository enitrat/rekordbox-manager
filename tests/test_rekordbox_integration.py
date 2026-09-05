import os
import shutil
from pathlib import Path

import pytest

from rbx.backup import backup, restore
from rbx.common import digest
from rbx.dependencies import bridge
from rbx.service import Service


@pytest.mark.skipif(
    not os.environ.get("RBX_TEST_DB"),
    reason="Set RBX_TEST_DB and RBX_TEST_PYTHON for disposable-copy integration",
)
def test_encrypted_rekordbox_roundtrip(library, audio, tmp_path, monkeypatch):
    source_db = Path(os.environ["RBX_TEST_DB"]).resolve()
    original_hash = digest(source_db)
    rb_dir = tmp_path / "real-rekordbox-copy"
    shutil.copytree(source_db.parent, rb_dir)
    library.config["rekordbox_db"] = str(rb_dir / "master.db")
    library.config["tools"]["rekordbox-python"] = os.environ["RBX_TEST_PYTHON"]
    library.save_config()
    initial = bridge(
        library, "rekordbox", {"operation": "inventory", "db": str(rb_dir / "master.db")}
    )
    service = Service(library)
    track = service.import_file(audio, "RBX integration A")
    service.import_file(audio, "RBX integration B")
    import rbx.rekordbox as adapter
    from rbx.common import Problem

    real_bridge = adapter.bridge
    lost_response = False

    def interrupted_bridge(lib, kind, request):
        nonlocal lost_response
        response = real_bridge(lib, kind, request)
        if request["operation"] == "apply" and not lost_response:
            lost_response = True
            raise Problem("interrupted", "Simulated response loss after Rekordbox commit")
        return response

    monkeypatch.setattr(adapter, "bridge", interrupted_bridge)
    interrupted = service.sync(offline=True)
    assert interrupted["errors"][0]["code"] == "interrupted"
    assert (library.root / "pending-rekordbox.json").exists()
    result = service.sync(offline=True)
    assert not result["errors"], result["errors"]
    result = service.sync(offline=True)
    assert not result["errors"], result["errors"]
    current = bridge(
        library, "rekordbox", {"operation": "inventory", "db": str(rb_dir / "master.db")}
    )
    assert len(current["tracks"]) == len(initial["tracks"]) + 1
    assert len(current["playlists"]) == len(initial["playlists"]) + 2
    assert {t["id"]: t for t in initial["tracks"]}.items() <= {
        t["id"]: t for t in current["tracks"]
    }.items()
    assert library.one("SELECT rb_id FROM tracks WHERE id=?", (track["id"],))["rb_id"]
    result = backup(library, tmp_path / "cloud")
    restored = restore(
        library,
        Path(result["snapshot"]),
        target=tmp_path / "restored-library",
        rekordbox_target=tmp_path / "restored-rekordbox",
    )
    assert restored["verified"]
    after = bridge(
        library,
        "rekordbox",
        {"operation": "inventory", "db": str(tmp_path / "restored-rekordbox/master.db")},
    )
    assert {t["id"]: t["preparation_hash"] for t in current["tracks"]} == {
        t["id"]: t["preparation_hash"] for t in after["tracks"]
    }
    assert digest(source_db) == original_hash
