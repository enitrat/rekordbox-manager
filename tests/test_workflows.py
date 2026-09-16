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
    provider.playlists["A"]["entries"][0]["title"] = "one"
    service.follow("A")
    provider.failure = Problem("drm-protected", "Protected source")
    service.sync()
    service.sync()
    assert provider.resolutions == 1
    issue = service.issues()[0]
    assert issue["title"] is None
    assert issue["provider_title"] == "one"
    assert issue["url"] == "https://soundcloud.com/artist/one"
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
    assert rb.playlists[playlist["id"]] == [
        library.one("SELECT rb_id FROM tracks WHERE id=?", (second["id"],))["rb_id"],
        library.one("SELECT rb_id FROM tracks WHERE id=?", (first["id"],))["rb_id"],
    ]
    assert len(rb.tracks) == 2


def test_acquisition_metadata_replaces_provider_id_placeholder(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["2083418979"])

    def resolve(source):
        provider.resolutions += 1
        return {
            "id": "soundcloud:2083418979",
            "url": "https://soundcloud.com/artist/track",
            "title": "Stay High (Club Mix)",
            "artist": "Ueberrest",
            "duration": 2,
            "downloader": "fake",
            "settings": {},
        }

    provider.resolve = resolve
    service.follow("A")
    service.sync()
    track = library.one("SELECT title,artist FROM tracks")
    assert track == {"title": "Stay High (Club Mix)", "artist": "Ueberrest"}
    assert rb.tracks[next(iter(rb.tracks))]["title"] == "Stay High (Club Mix)"


def test_follow_nonempty_playlist_requires_reconciliation_before_sync(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    rb.playlists["rb-existing"] = ["prepared-old-track"]

    result = service.follow("A", rb_id="rb-existing")

    assert result["status"] == "reconciliation-required"
    assert result["rekordbox_playlist"]["existing_entries"] == 1
    assert result["next"] == 'rbx reconcile "A"'

    sync = service.sync(offline=True)
    assert sync["errors"][0]["code"] == "reconciliation-required"
    assert rb.playlists["rb-existing"] == ["prepared-old-track"]
    assert provider.downloads == 0


def test_legacy_nonempty_mapping_requires_reconciliation_after_upgrade(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    service.follow("A")
    service.sync()
    playlist = library.playlists("A")[0]
    library.db.execute("DELETE FROM settings WHERE key=?", ("managed:" + playlist["id"],))
    library.db.commit()
    before = list(rb.playlists[playlist["rb_id"]])

    result = service.sync(offline=True)

    assert result["errors"][0]["code"] == "reconciliation-required"
    assert rb.playlists[playlist["rb_id"]] == before


def test_reconcile_approves_unique_match_and_preserves_local_membership(library, audio):
    service, provider, rb = setup(library, audio)
    online = snapshot("A", ["stay-high"])
    online["entries"][0].update(
        {"title": "Stay High (Club Mix)", "artist": "Ueberrest", "duration": 120}
    )
    provider.playlists["A"] = online
    rb.tracks = {
        "rb-match": {
            "id": "rb-match",
            "title": "Stay High (Club Mix)",
            "artist": "Ueberrest",
            "duration": 120,
            "path": str(audio),
            "cues": [1234, 2345],
        },
        "rb-local": {
            "id": "rb-local",
            "title": "Private edit",
            "artist": "DJ",
            "duration": 180,
            "path": str(audio),
            "cues": [999],
        },
    }
    rb.playlists["rb-existing"] = ["rb-match", "rb-local"]
    service.follow("A", rb_id="rb-existing")

    plan = service.reconcile("A")

    assert [item["source_id"] for item in plan["suggested"]] == [
        "soundcloud:stay-high"
    ]
    assert plan["suggested"][0]["rb_content_id"] == "rb-match"
    assert [item["rb_content_id"] for item in plan["local_only"]] == ["rb-local"]

    result = service.reconcile("A", apply=True, accept_suggestions=True)

    assert result["status"] == "reconciled"
    assert provider.downloads == 0
    assert rb.playlists["rb-existing"] == ["rb-match", "rb-local"]
    assert rb.tracks["rb-match"]["cues"] == [1234, 2345]
    linked = library.one("SELECT track_id FROM sources WHERE id='soundcloud:stay-high'")
    assert library.one("SELECT rb_id FROM tracks WHERE id=?", (linked["track_id"],))[
        "rb_id"
    ] == "rb-match"


def test_sync_removes_managed_membership_but_keeps_new_local_extras(library, audio):
    service, provider, rb = setup(library, audio)
    online = snapshot("A", ["stay-high"])
    online["entries"][0].update(
        {"title": "Stay High", "artist": "Ueberrest", "duration": 120}
    )
    provider.playlists["A"] = online
    rb.tracks = {
        "rb-match": {
            "id": "rb-match",
            "title": "Stay High",
            "artist": "Ueberrest",
            "duration": 120,
            "path": str(audio),
            "cues": [1234],
        },
        "rb-local": {
            "id": "rb-local",
            "title": "Local old",
            "artist": "DJ",
            "duration": 180,
            "path": str(audio),
            "cues": [999],
        },
        "rb-later": {
            "id": "rb-later",
            "title": "Local later",
            "artist": "DJ",
            "duration": 181,
            "path": str(audio),
            "cues": [888],
        },
    }
    rb.playlists["rb-existing"] = ["rb-match", "rb-local"]
    service.follow("A", rb_id="rb-existing")
    service.reconcile("A", apply=True, accept_suggestions=True)
    rb.playlists["rb-existing"].append("rb-later")
    provider.playlists["A"] = snapshot("A", [])

    service.sync()

    assert rb.playlists["rb-existing"] == ["rb-local", "rb-later"]
    assert rb.tracks["rb-match"]["cues"] == [1234]


def test_reconcile_does_not_overwrite_playlist_changed_during_apply(library, audio):
    service, provider, rb = setup(library, audio)
    online = snapshot("A", ["stay-high"])
    online["entries"][0].update(
        {"title": "Stay High", "artist": "Ueberrest", "duration": 120}
    )
    provider.playlists["A"] = online
    rb.tracks["rb-match"] = {
        "id": "rb-match",
        "title": "Stay High",
        "artist": "Ueberrest",
        "duration": 120,
        "path": str(audio),
        "cues": [1234],
    }
    rb.playlists["rb-existing"] = ["rb-match"]
    service.follow("A", rb_id="rb-existing")
    rb.before_apply = lambda: rb.playlists["rb-existing"].append("rb-surprise")

    result = service.reconcile("A", apply=True, accept_suggestions=True)

    assert result["status"] == "reconciliation-stale"
    assert result["sync"]["errors"][0]["code"] == "reconciliation-stale"
    assert rb.playlists["rb-existing"] == ["rb-match", "rb-surprise"]
    retry = service.sync(offline=True)
    assert retry["errors"][0]["code"] == "reconciliation-required"
    assert rb.playlists["rb-existing"] == ["rb-match", "rb-surprise"]


def test_ambiguous_matches_wait_for_explicit_choice(library, audio):
    service, provider, rb = setup(library, audio)
    online = snapshot("A", ["same"])
    online["entries"][0].update(
        {"title": "Same Recording", "artist": "Artist", "duration": 120}
    )
    provider.playlists["A"] = online
    for content_id in ("rb-one", "rb-two"):
        rb.tracks[content_id] = {
            "id": content_id,
            "title": "Same Recording",
            "artist": "Artist",
            "duration": 120,
            "path": str(audio),
            "cues": [1 if content_id == "rb-one" else 2],
        }
    rb.playlists["rb-existing"] = ["rb-one", "rb-two"]
    service.follow("A", rb_id="rb-existing")

    plan = service.reconcile("A")
    assert len(plan["ambiguous"]) == 1
    pending = service.reconcile("A", apply=True)
    assert pending["pending_sources"] == ["soundcloud:same"]
    assert provider.downloads == 0
    assert rb.playlists["rb-existing"] == ["rb-one", "rb-two"]

    applied = service.reconcile(
        "A", apply=True, matches={"soundcloud:same": "rb-one"}
    )
    assert applied["pending_sources"] == []
    assert provider.downloads == 0
    assert rb.playlists["rb-existing"] == ["rb-one", "rb-two"]


def test_reconcile_reuses_prepared_content_with_identical_audio(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["numeric-id"])
    service.follow("A")
    service.sync()
    source = library.one("SELECT * FROM sources WHERE id='soundcloud:numeric-id'")
    downloaded = library.one("SELECT * FROM tracks WHERE id=?", (source["track_id"],))
    rb.tracks["rb-prepared"] = {
        "id": "rb-prepared",
        "title": "Human title",
        "artist": "Actual artist",
        "duration": 2,
        "path": str(audio),
        "cues": [111, 222],
    }
    rb.playlists["rb-existing"] = ["rb-prepared"]
    service.follow("A", rb_id="rb-existing")

    plan = service.reconcile("A")

    assert plan["exact"] == [
        {
            "source_id": "soundcloud:numeric-id",
            "rb_content_id": "rb-prepared",
            "title": "Human title",
            "artist": "Actual artist",
            "duration": 2,
            "evidence": "file-hash",
        }
    ]
    result = service.reconcile("A", apply=True)
    assert result["status"] == "reconciled"
    assert provider.downloads == 1
    assert library.one("SELECT rb_id FROM tracks WHERE id=?", (downloaded["id"],))[
        "rb_id"
    ] == "rb-prepared"
    assert rb.tracks["rb-prepared"]["cues"] == [111, 222]


def test_reconcile_applies_the_same_online_snapshot_it_reviews(library, audio):
    service, provider, rb = setup(library, audio)
    provider.playlists["A"] = snapshot("A", ["one"])
    rb.tracks["rb-local"] = {
        "id": "rb-local",
        "title": "Local track",
        "artist": "DJ",
        "duration": 2,
        "path": str(audio),
        "cues": [123],
    }
    rb.playlists["rb-existing"] = ["rb-local"]
    service.follow("A", rb_id="rb-existing")
    provider.playlists["A"] = snapshot("A", [])

    result = service.reconcile("A", apply=True)

    assert result["online_only"] == []
    assert [item["rb_content_id"] for item in result["local_only"]] == ["rb-local"]
    assert rb.playlists["rb-existing"] == ["rb-local"]
