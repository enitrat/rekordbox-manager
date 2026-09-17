"""Isolated pyrekordbox adapter; never prints database keys or raw tracebacks."""

import contextlib
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from pathlib import Path


def running():
    from pyrekordbox.utils import get_rekordbox_pid

    return bool(get_rekordbox_pid())


def serialize(content):
    return {
        "id": str(content.ID),
        "path": content.FolderPath,
        "title": content.Title or "",
        "artist": content.Artist.Name if content.Artist else "",
        "duration": content.Length,
        "analysis_path": content.AnalysisDataPath or "",
        "preparation_hash": hashlib.sha256(
            json.dumps(
                {
                    "cues": [c.to_dict() for c in sorted(content.Cues, key=lambda cue: cue.ID)],
                    "bpm": content.BPM,
                    "rating": content.Rating,
                    "comment": content.Commnt,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest(),
    }


def execute(request):
    from pyrekordbox import Rekordbox6Database

    if request["operation"] == "doctor":
        import sqlcipher3

        del sqlcipher3
        return {"version": importlib.metadata.version("pyrekordbox"), "running": running()}
    if request["operation"] in ("apply", "relocate") and running():
        return {
            "error": {
                "code": "rekordbox-running",
                "message": "Close Rekordbox and rerun the command.",
                "action": "Acquired audio is retained; sync will resume pending imports.",
            }
        }
    path = Path(request["db"])
    if not path.is_file():
        raise FileNotFoundError("Configured Rekordbox database does not exist")
    with Rekordbox6Database(path=path, db_dir=path.parent, unlock=True) as db:
        if request["operation"] == "inventory":
            playlists = []
            for playlist in db.get_playlist():
                songs = sorted(
                    db.get_playlist_songs(PlaylistID=playlist.ID).all(),
                    key=lambda song: song.TrackNo,
                )
                playlists.append(
                    {
                        "id": str(playlist.ID),
                        "name": playlist.Name,
                        "content_ids": [str(song.ContentID) for song in songs],
                    }
                )
            return {
                "tracks": [serialize(c) for c in db.get_content()],
                "playlists": playlists,
            }
        if request["operation"] == "apply":
            content_map, playlists = {}, []
            for track in request["tracks"]:
                content = db.get_content(ID=track["rb_id"]) if track.get("rb_id") else None
                if content is not None and content.FolderPath != track["path"]:
                    raise ValueError(
                        "Recorded Rekordbox identity points at a different path; adopt or repair first"
                    )
                if content is not None and str(content.Title or "").strip().isdigit():
                    artist_id = content.ArtistID
                    if track["artist"]:
                        artist = db.get_artist(Name=track["artist"]).first() or db.add_artist(
                            track["artist"]
                        )
                        artist_id = artist.ID
                    content.Title = track["title"]
                    content.SrcTitle = track["title"]
                    content.SrcArtistName = track["artist"]
                    content.ArtistID = artist_id
                if content is None:
                    matches = db.get_content(FolderPath=track["path"]).all()
                    if len(matches) > 1:
                        raise ValueError(
                            "Multiple Rekordbox records share this path; manual adoption required"
                        )
                    artist_id = None
                    if track["artist"] and not matches:
                        artist = db.get_artist(Name=track["artist"]).first() or db.add_artist(
                            track["artist"]
                        )
                        artist_id = artist.ID
                    content = (
                        matches[0]
                        if matches
                        else db.add_content(
                            track["path"],
                            Title=track["title"],
                            SrcTitle=track["title"],
                            SrcArtistName=track["artist"],
                            ArtistID=artist_id,
                            Length=round(track["duration"] or 0),
                        )
                    )
                content_map[track["id"]] = content
            for desired in request["playlists"]:
                playlist = db.get_playlist(ID=desired["rb_id"]) if desired.get("rb_id") else None
                if playlist is None:
                    existing = db.get_playlist(Name=desired["name"]).all()
                    if existing:
                        raise ValueError(
                            "Playlist name already exists in Rekordbox; use follow --rekordbox-playlist-id to explicitly adopt it"
                        )
                    playlist = db.create_playlist(desired["name"])
                managed_content_ids = list(
                    dict.fromkeys(str(content_map[t].ID) for t in desired["track_ids"])
                )
                content_ids = list(managed_content_ids)
                content_ids.extend(
                    content_id
                    for content_id in desired.get("preserve_content_ids", [])
                    if content_id not in content_ids
                    and db.get_content(ID=content_id) is not None
                )
                songs = sorted(
                    db.get_playlist_songs(PlaylistID=playlist.ID).all(), key=lambda s: s.TrackNo
                )
                current_ids = [str(s.ContentID) for s in songs]
                expected_content_ids = desired.get("expected_content_ids")
                if expected_content_ids is not None and current_ids != expected_content_ids:
                    return {
                        "error": {
                            "code": "reconciliation-stale",
                            "message": f'Rekordbox playlist "{playlist.Name}" changed after review.',
                            "action": "Run rbx reconcile again before applying changes.",
                        }
                    }
                if current_ids != content_ids or [s.TrackNo for s in songs] != list(
                    range(1, len(songs) + 1)
                ):
                    for song in songs:
                        db.delete(song)
                    db.flush()
                    for index, content_id in enumerate(content_ids, 1):
                        db.add_to_playlist(playlist, db.get_content(ID=content_id), track_no=index)
                playlists.append(
                    {
                        "id": desired["id"],
                        "rb_id": str(playlist.ID),
                        "content_ids": content_ids,
                        "managed_content_ids": managed_content_ids,
                    }
                )
            if request.get("journal"):
                journal = Path(request["journal"])
                fd, temporary = tempfile.mkstemp(dir=journal.parent)
                with os.fdopen(fd, "w") as output:
                    json.dump(
                        {
                            "db": str(path),
                            "tracks": {tid: str(c.ID) for tid, c in content_map.items()},
                            "playlists": {p["id"]: p["rb_id"] for p in playlists},
                        },
                        output,
                    )
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, journal)
            db.commit()
            for playlist in playlists:
                actual = sorted(
                    db.get_playlist_songs(PlaylistID=playlist["rb_id"]).all(),
                    key=lambda s: s.TrackNo,
                )
                if [str(s.ContentID) for s in actual] != playlist["content_ids"]:
                    raise ValueError(
                        "Post-commit playlist verification failed; recovery bundle retained"
                    )
            return {
                "tracks": [
                    {
                        "id": tid,
                        "rb_id": str(c.ID),
                        "analysis": (
                            "present"
                            if c.AnalysisDataPath and db.get_anlz_dir(c).exists()
                            else "needs-analysis"
                        ),
                    }
                    for tid, c in content_map.items()
                ],
                "playlists": playlists,
            }
        if request["operation"] == "relocate":
            from pyrekordbox.anlz import AnlzFile

            changed = 0
            for content in db.get_content():
                new_path = request["paths"].get(content.FolderPath)
                if not new_path:
                    continue
                if not Path(new_path).is_file():
                    raise ValueError("Relocation target audio is missing")
                if content.AnalysisDataPath:
                    for analysis in db.get_anlz_paths(content).values():
                        if analysis:
                            file = AnlzFile.parse_file(analysis)
                            if "PPTH" in file:
                                file.set_path(new_path)
                                file.save()
                content.FolderPath = new_path
                content.FileNameL = Path(new_path).name
                changed += 1
            db.commit()
            return {"relocated": changed}
    raise ValueError("Unknown Rekordbox operation")


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            response = execute(request)
    except Exception as exc:
        response = {
            "error": {
                "code": "rekordbox-failed",
                "message": type(exc).__name__ + ": " + str(exc),
                "action": "Run rbx doctor. The pre-change recovery bundle is retained.",
            }
        }
    print(json.dumps(response))
