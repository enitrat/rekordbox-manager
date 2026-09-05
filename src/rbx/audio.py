from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from .common import Problem, digest, now, run
from .dependencies import tool

AUDIO_SUFFIXES = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a"}


def inspect(library, path: Path, expected_duration=None):
    if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
        raise Problem("unsuitable-audio", f"Not a supported audio file: {path.name}")
    metadata = json.loads(
        run(
            [
                tool(library, "ffprobe"),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ]
        )
    )
    streams = [s for s in metadata.get("streams", []) if s.get("codec_type") == "audio"]
    duration = float(metadata.get("format", {}).get("duration", 0))
    if not streams or duration <= 0:
        raise Problem("unsuitable-audio", "File has no readable audio or duration.")
    if expected_duration and duration < float(expected_duration) * 0.8:
        raise Problem("preview-only", "Audio is substantially shorter than the expected recording.")
    decoded = run(
        [
            tool(library, "ffmpeg"),
            "-v",
            "error",
            "-xerror",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )
    del decoded
    tags = {k.lower(): v for k, v in metadata.get("format", {}).get("tags", {}).items()}
    return {
        "duration": duration,
        "title": tags.get("title") or path.stem,
        "artist": tags.get("artist") or "",
        "sha256": digest(path),
    }


def store_audio(
    library, path: Path, *, title=None, artist=None, expected_duration=None, rb_id=None
):
    metadata = inspect(library, path, expected_duration)
    existing = library.one("SELECT * FROM tracks WHERE sha256=?", (metadata["sha256"],))
    if existing:
        destination = library.root / existing["path"]
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        return existing
    track_id = str(uuid.uuid4())
    relative = Path("audio") / metadata["sha256"][:2] / (metadata["sha256"] + path.suffix.lower())
    destination = library.root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".pending")
    shutil.copy2(path, temporary)
    if digest(temporary) != metadata["sha256"]:
        raise Problem("file-changed", "Audio changed while importing; retry.")
    temporary.replace(destination)
    library.db.execute(
        "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?)",
        (
            track_id,
            metadata["sha256"],
            str(relative),
            title or metadata["title"],
            artist or metadata["artist"],
            metadata["duration"],
            rb_id,
            "unknown",
            now(),
        ),
    )
    library.db.commit()
    return library.one("SELECT * FROM tracks WHERE id=?", (track_id,))
