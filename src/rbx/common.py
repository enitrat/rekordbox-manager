from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class Problem(Exception):
    def __init__(self, code: str, message: str, *, action: str = "", details=None):
        super().__init__(message)
        self.code, self.message, self.action, self.details = code, message, action, details

    def as_dict(self):
        return {
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "details": self.details,
        }


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rbx-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Problem("busy", "Another rbx operation is using this library.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def redact(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization|cookie|token|secret|password)[=: ]+[^\s,;]+", r"\1=[redacted]", value
    )
    return re.sub(r"https?://[^\s\"']+", "[remote URL]", value)


def run(argv, *, timeout=300, payload=None, env=None) -> str:
    try:
        result = subprocess.run(
            [str(a) for a in argv],
            input=(json.dumps(payload) if payload is not None else None),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise Problem(
            "dependency-missing",
            f"Executable not found: {argv[0]}",
            action="Run rbx setup --install.",
        ) from None
    except subprocess.TimeoutExpired:
        raise Problem(
            "timeout",
            f"{Path(argv[0]).name} exceeded {timeout} seconds.",
            action="Retry when your connection is available.",
        ) from None
    if result.returncode:
        message = redact((result.stderr or result.stdout)[-3000:])
        code = "tool-failed"
        lowered = message.lower()
        for token, category in [
            ("drm", "drm-protected"),
            ("preview", "preview-only"),
            ("sign in", "authentication"),
            ("401", "authentication"),
            ("403", "authentication"),
            ("unavailable", "unavailable"),
            ("ffmpeg", "conversion-failed"),
            ("timed out", "timeout"),
            ("connection", "network-failed"),
        ]:
            if token in lowered:
                code = category
                break
        raise Problem(
            code,
            f"{Path(argv[0]).name}: {message.strip()}",
            action="Run rbx doctor; retry or resolve the reported issue.",
        )
    return result.stdout


def checked_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or Path(relative).is_absolute():
        raise Problem("invalid-path", "A stored path escapes its library or snapshot.")
    return path
