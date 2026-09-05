from pathlib import Path

import pytest
from conftest import FakeProviders, FakeRekordbox, snapshot

from rbx.common import Problem
from rbx.service import Service


def setup(library, audio):
    providers, rb = FakeProviders(audio), FakeRekordbox()
    return Service(library, providers=providers, rekordbox=rb), providers, rb


def test_shared_download_memberships_and_removal_preserve_cues(library, audio):
    service, provider, rb = setup(library, audio)
    for name in ("A", "B"):
        provider.playlists[name] = snapshot(name, ["one"])
        service.follow(name)
    service.sync()
    assert provider.downloads == 1
    assert len(rb.tracks) == 1
    assert rb.playlists["soundcloud:A"] == rb.playlists["soundcloud:B"]
    track = next(iter(rb.tracks.values()))
    service.sync()
    assert provider.downloads == 1
    provider.playlists["A"] = snapshot("A", [])
    service.sync()
    assert rb.playlists["soundcloud:A"] == []
    assert len(rb.playlists["soundcloud:B"]) == 1
    assert track["cues"] == [1234, 2345]
    assert Path(track["path"]).exists()


def test_failed_enumeration_preserves_memberships(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    service.sync()
    before = dict(rb.playlists)
    provider.playlists["A"] = Problem("incomplete-playlist", "Missing page")
    result = service.sync()
    assert result["changes"][0]["error"]["code"] == "incomplete-playlist"
    assert rb.playlists == before
    assert library.db.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 1


def test_preview_changes_no_persisted_state(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    provider.playlists["A"] = snapshot("A", ["two"])
    result = service.sync(preview=True)
    assert result["changes"][0]["removed"] == ["soundcloud:one"]
    assert library.one("SELECT source_id FROM memberships")["source_id"] == "soundcloud:one"
    assert provider.downloads == 0
    assert not rb.tracks


def test_open_rekordbox_resumes_without_downloading(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    rb.failure = Problem("rekordbox-running", "Close Rekordbox")
    result = service.sync()
    assert result["errors"][0]["code"] == "rekordbox-running"
    assert result["status"]["playlists"][0]["membership_pending"]
    rb.failure = None
    result = service.sync(offline=True)
    assert provider.downloads == 1
    assert not result["status"]["playlists"][0]["membership_pending"]


def test_failure_persists_without_repeated_retry_and_local_resolution(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    provider.failure = Problem("drm-protected", "Protected source")
    service.sync()
    service.sync()
    assert provider.resolutions == 1
    issue = service.issues()[0]
    service.resolve_issue(issue["id"], action="resolve", file=audio)
    service.sync()
    assert provider.resolutions == 1
    assert len(rb.tracks) == 1
    assert not service.issues()


def test_import_deduplicates_identical_files_without_title_matching(library, audio, tmp_path):
    service, provider, rb = setup(library, audio)
    first = service.import_file(audio, "Set")
    second = service.import_file(audio, "Other set")
    assert first["id"] == second["id"]
    service.sync(offline=True)
    assert len(rb.tracks) == 1
    assert len(rb.playlists) == 2
    different = tmp_path / "different.wav"
    content = bytearray(audio.read_bytes())
    content[-8] ^= 1
    different.write_bytes(content)
    third = service.import_file(different, "Set")
    assert third["id"] != first["id"]


def test_acquisition_provenance_shared_across_source_ids(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one", "two"])
    service.follow("A")
    service.sync()
    assert provider.downloads == 1
    assert (
        library.db.execute(
            "SELECT COUNT(*) FROM sources WHERE acquisition_id='youtube:recording'"
        ).fetchone()[0]
        == 2
    )
    assert len(rb.playlists["soundcloud:A"]) == 1


def test_local_import_cannot_edit_online_membership(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    with pytest.raises(Problem, match="online-owned"):
        service.import_file(audio, "A")


def test_reorder_preserves_track_identity(library, audio, tmp_path):
    service, provider, rb = setup(library, audio)
    first = service.import_file(audio, "Set")
    different = tmp_path / "different.wav"
    content = bytearray(audio.read_bytes())
    content[-8] ^= 1
    different.write_bytes(content)
    second = service.import_file(different, "Set")
    service.sync(offline=True)
    playlist = library.playlists()[0]
    with library.db:
        library.db.execute(
            "UPDATE memberships SET position=3-position WHERE playlist_id=?", (playlist["id"],)
        )
    service.sync(offline=True)
    assert rb.playlists[playlist["id"]] == [second["id"], first["id"]]
    assert len(rb.tracks) == 2
