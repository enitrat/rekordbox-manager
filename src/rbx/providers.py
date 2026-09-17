from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .common import Problem, run
from .dependencies import bridge, tool


def provider_for(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise Problem("invalid-url", "Use an HTTPS Spotify, SoundCloud, or YouTube URL.")
    host = (parsed.hostname or "").lower()
    if host == "open.spotify.com":
        return "spotify"
    if host in ("soundcloud.com", "www.soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"):
        return "soundcloud"
    if host in ("youtube.com", "www.youtube.com", "music.youtube.com", "youtu.be"):
        return "youtube"
    raise Problem("unsupported-provider", f"No provider adapter for {host}.")


def identity(provider, provider_id):
    return f"{provider}:{provider_id}"


def youtube_identity(url):
    parsed = urlparse(url)
    return (
        parsed.path.strip("/")
        if parsed.hostname == "youtu.be"
        else parse_qs(parsed.query).get("v", [None])[0]
    )


class Providers:
    def __init__(self, library):
        self.library = library

    def playlist(self, url):
        provider = provider_for(url)
        if provider == "spotify":
            match = re.search(r"/playlist/([A-Za-z0-9]+)", urlparse(url).path)
            if not match:
                raise Problem("invalid-url", "Follow expects a Spotify playlist URL.")
            result = bridge(
                self.library,
                "spotify",
                {"operation": "playlist", "url": f"https://open.spotify.com/playlist/{match[1]}"},
            )
        else:
            data = json.loads(
                run(
                    [
                        tool(self.library, "yt-dlp"),
                        "--ignore-config",
                        "--flat-playlist",
                        "--dump-single-json",
                        "--no-warnings",
                        "--abort-on-error",
                        "--",
                        url,
                    ]
                )
            )
            if data.get("_type") != "playlist" or not isinstance(data.get("entries"), list):
                raise Problem(
                    "invalid-playlist", "Follow expects a playlist, not an individual track."
                )
            entries = data["entries"]
            expected = data.get("playlist_count") or data.get("n_entries")
            if expected is not None and expected != len(entries):
                raise Problem(
                    "incomplete-playlist",
                    "Provider returned an incomplete playlist; membership preserved.",
                )
            tracks = []
            for entry in entries:
                if not entry or not entry.get("id") or entry.get("_type") == "playlist":
                    raise Problem(
                        "incomplete-playlist",
                        "An entry lacks a stable identity; membership preserved.",
                    )
                entry_url = entry.get("webpage_url") or entry.get("url")
                if provider == "youtube":
                    entry_url = "https://www.youtube.com/watch?v=" + entry["id"]
                if not entry_url or not entry_url.startswith("https://"):
                    raise Problem(
                        "incomplete-playlist", "An entry has no usable URL; membership preserved."
                    )
                tracks.append(
                    {
                        "provider_id": str(entry["id"]),
                        "url": entry_url,
                        "title": entry.get("title") or entry["id"],
                        "artist": entry.get("uploader") or "",
                        "duration": entry.get("duration"),
                    }
                )
            result = {
                "provider": provider,
                "provider_id": str(data["id"]),
                "url": data.get("webpage_url") or url,
                "name": data.get("title") or data["id"],
                "entries": tracks,
            }
        result["id"] = identity(result["provider"], result["provider_id"])
        seen, unique = set(), []
        for entry in result["entries"]:
            entry["provider"] = result["provider"]
            entry["id"] = identity(result["provider"], entry["provider_id"])
            if entry["id"] not in seen:
                seen.add(entry["id"])
                unique.append(entry)
        result["duplicate_occurrences"] = len(result["entries"]) - len(unique)
        result["entries"] = unique
        return result

    def resolve(self, source):
        url = source.get("acquisition_url")
        if not url:
            url = source["url"]
            if source["provider"] == "spotify":
                url = bridge(
                    self.library,
                    "spotify",
                    {"operation": "resolve", "url": url, "ffmpeg": tool(self.library, "ffmpeg")},
                )["url"]
        data = json.loads(
            run(
                [
                    tool(self.library, "yt-dlp"),
                    "--ignore-config",
                    "--no-playlist",
                    "--dump-single-json",
                    "--skip-download",
                    "--no-warnings",
                    "--",
                    url,
                ]
            )
        )
        if data.get("is_live") or data.get("live_status") == "is_live":
            raise Problem("unsuitable-audio", "Live streams cannot be imported as DJ tracks.")
        if data.get("has_drm"):
            raise Problem("drm-protected", "The source reports protected audio.")
        if (
            data.get("duration")
            and source.get("duration")
            and data["duration"] < source["duration"] * 0.8
        ):
            raise Problem(
                "preview-only", "Available audio is substantially shorter than the requested track."
            )
        extractor = str(data.get("extractor_key") or data.get("extractor") or "").lower()
        provider = (
            "youtube"
            if "youtube" in extractor
            else "soundcloud"
            if "soundcloud" in extractor
            else extractor
        )
        if not provider or not data.get("id"):
            raise Problem(
                "missing-provenance", "Downloader did not identify the selected recording."
            )
        acquired_id = identity(provider, str(data["id"]))
        if source.get("acquisition_id") and source["acquisition_id"] != acquired_id:
            raise Problem(
                "source-changed", "The recorded acquisition URL now resolves to another identity."
            )
        return {
            "id": acquired_id,
            "url": data.get("webpage_url") or url,
            # Flat playlist entries can contain only a provider ID. Resolve the
            # recording before download so Rekordbox receives real tags.
            "title": data.get("track") or data.get("title") or "",
            "artist": data.get("artist") or data.get("uploader") or data.get("creator") or "",
            "duration": data.get("duration"),
            "downloader": "spotdl+yt-dlp" if source["provider"] == "spotify" else "yt-dlp",
            "settings": {"format": "bestaudio/best", "audio_format": "mp3", "audio_quality": "0"},
        }

    def metadata(self, source):
        url = source.get("acquisition_url") or source["url"]
        data = json.loads(
            run(
                [
                    tool(self.library, "yt-dlp"),
                    "--ignore-config",
                    "--no-playlist",
                    "--ignore-no-formats-error",
                    "--dump-single-json",
                    "--skip-download",
                    "--no-warnings",
                    "--",
                    url,
                ]
            )
        )
        if str(data.get("id")) != str(source["provider_id"]):
            raise Problem(
                "source-changed", "Metadata URL resolves to a different provider identity."
            )
        return {
            "title": data.get("track") or data.get("title") or "",
            "artist": data.get("artist") or data.get("uploader") or data.get("creator") or "",
            "duration": data.get("duration"),
            "url": data.get("webpage_url") or url,
        }

    def download(self, acquisition, destination: Path):
        destination.mkdir(parents=True, exist_ok=True)
        output = run(
            [
                tool(self.library, "yt-dlp"),
                "--ignore-config",
                "--no-playlist",
                "--no-warnings",
                "--retries",
                "2",
                "--fragment-retries",
                "2",
                "--abort-on-unavailable-fragments",
                "-f",
                "bestaudio/best",
                "-x",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "0",
                "--ffmpeg-location",
                tool(self.library, "ffmpeg"),
                "--print",
                "after_move:filepath",
                "-o",
                str(destination / "audio.%(ext)s"),
                "--",
                acquisition["url"],
            ],
            timeout=600,
        )
        candidates = [Path(line) for line in output.splitlines() if Path(line).is_file()]
        if len(candidates) != 1 or not candidates[0].resolve().is_relative_to(
            destination.resolve()
        ):
            raise Problem("download-failed", "Downloader did not produce exactly one audio file.")
        return candidates[0]
