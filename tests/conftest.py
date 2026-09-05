import shutil
import subprocess
from pathlib import Path

import pytest

from rbx.state import Library


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "library", create=True)
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if not path:
            pytest.skip("Integration tests require ffmpeg and ffprobe")
        lib.config["tools"][name] = path
    lib.save_config()
    yield lib
    lib.close()


@pytest.fixture
def audio(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("Audio fixture requires ffmpeg")
    path = tmp_path / "track.wav"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(path)],
        check=True,
        capture_output=True,
    )
    return path


class FakeProviders:
    def __init__(self, audio):
        self.audio = audio
        self.playlists = {}
        self.downloads = 0
        self.resolutions = 0
        self.failure = None

    def playlist(self, url):
        value = self.playlists[url]
        if isinstance(value, Exception):
            raise value
        return value

    def resolve(self, source):
        self.resolutions += 1
        if self.failure:
            raise self.failure
        return {
            "id": "youtube:recording",
            "url": "https://youtube.com/watch?v=recording",
            "duration": 2,
            "downloader": "fake",
            "settings": {},
        }

    def download(self, acquisition, destination):
        self.downloads += 1
        result = Path(destination) / "audio.wav"
        shutil.copy2(self.audio, result)
        return result


class FakeRekordbox:
    def __init__(self):
        self.tracks = {}
        self.playlists = {}
        self.failure = None

    def apply(self, tracks, playlists):
        if self.failure:
            raise self.failure
        for track in tracks:
            self.tracks.setdefault(track["id"], {"cues": [1234, 2345], **track})
        for playlist in playlists:
            self.playlists[playlist["id"]] = list(playlist["track_ids"])
        return {
            "tracks": [
                {"id": t["id"], "rb_id": "rb-" + t["id"], "analysis": "needs-analysis"}
                for t in tracks
            ],
            "playlists": [{"id": p["id"], "rb_id": "rb-" + p["id"]} for p in playlists],
        }


def snapshot(name, ids):
    return {
        "id": "soundcloud:" + name,
        "provider": "soundcloud",
        "provider_id": name,
        "url": name,
        "name": name,
        "entries": [
            {
                "id": "soundcloud:" + id,
                "provider": "soundcloud",
                "provider_id": id,
                "url": "https://soundcloud.com/artist/" + id,
                "title": "Same title",
                "artist": "Artist",
                "duration": 2,
            }
            for id in ids
        ],
    }
