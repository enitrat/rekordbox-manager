"""Isolated spotDL integration; stdin and stdout carry one JSON document each."""

import contextlib
import importlib.metadata
import json
import os
import sys


def execute(request):
    if request["operation"] == "doctor":
        return {"version": importlib.metadata.version("spotdl")}
    from spotdl.utils.spotify import SpotifyClient

    SpotifyClient.init(
        client_id=os.environ.get("SPOTIPY_CLIENT_ID", ""),
        client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET", ""),
        no_cache=True,
        headless=True,
        use_official_api=bool(os.environ.get("SPOTIPY_CLIENT_ID")),
    )
    client = SpotifyClient()
    if request["operation"] == "playlist":
        metadata = client.playlist(request["url"])
        if not metadata:
            raise ValueError("Spotify returned no playlist metadata")
        page = client.playlist_items(request["url"])
        entries, seen, expected = [], set(), None
        while True:
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise ValueError("Incomplete Spotify playlist page; existing memberships preserved")
            if expected is None:
                expected = page.get("total")
            entries.extend(page["items"])
            if not page.get("next"):
                break
            if page["next"] in seen:
                raise ValueError("Repeated Spotify page")
            seen.add(page["next"])
            page = client.next(page)
        if expected is not None and len(entries) != expected:
            raise ValueError("Incomplete Spotify playlist count; existing memberships preserved")
        after = client.playlist(request["url"])
        if metadata.get("snapshot_id") != after.get("snapshot_id"):
            raise ValueError("Playlist changed during enumeration; retry sync")
        tracks = []
        for item in entries:
            track = item.get("track") or item.get("item") if isinstance(item, dict) else None
            if (
                not track
                or not track.get("id")
                or track.get("is_local")
                or track.get("type") != "track"
            ):
                raise ValueError(
                    "Playlist contains an unavailable or local entry without a stable track ID; existing memberships preserved"
                )
            tracks.append(
                {
                    "provider_id": track["id"],
                    "url": f"https://open.spotify.com/track/{track['id']}",
                    "title": track["name"],
                    "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                    "duration": track.get("duration_ms", 0) / 1000,
                }
            )
        return {
            "provider": "spotify",
            "provider_id": metadata["id"],
            "name": metadata["name"],
            "url": request["url"],
            "entries": tracks,
        }
    if request["operation"] == "resolve":
        from spotdl.download.downloader import Downloader
        from spotdl.types.song import Song

        song = Song.from_url(request["url"])
        downloader = Downloader(
            settings={
                "simple_tui": True,
                "threads": 1,
                "ffmpeg": request["ffmpeg"],
                "lyrics_providers": [],
            }
        )
        url = downloader.search(song)
        if not url:
            raise ValueError("spotDL found no audio candidate")
        return {"url": url}
    raise ValueError("Unknown Spotify operation")


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            response = execute(request)
    except Exception as exc:
        response = {
            "error": {
                "code": "provider-failed",
                "message": type(exc).__name__ + ": " + str(exc),
                "action": "Check provider access and run rbx issues retry after resolving it.",
            }
        }
    print(json.dumps(response))
