from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .common import Problem, redact, run


def tool(library, name):
    configured = library.config["tools"].get(name)
    managed = library.root / "tools" / "bin" / name
    found = configured or (str(managed) if managed.exists() else shutil.which(name))
    if not found:
        raise Problem(
            "dependency-missing", f"{name} is not installed.", action="Run rbx setup --install."
        )
    return found


def bridge(library, kind, payload):
    python = tool(library, f"{kind}-python")
    script = Path(__file__).with_name(f"{kind}_worker.py")
    import json

    try:
        response = json.loads(run([python, script], payload=payload, timeout=600))
    except ValueError:
        raise Problem(
            "invalid-tool-response", f"{kind} integration returned invalid JSON."
        ) from None
    if "error" in response:
        response["error"]["message"] = redact(response["error"]["message"])
        raise Problem(**response["error"])
    return response


def doctor(library):
    checks = []
    for name in ("yt-dlp", "spotdl", "ffmpeg", "ffprobe"):
        try:
            output = run(
                [tool(library, name), "-version" if name.startswith("ff") else "--version"],
                timeout=30,
            )
            checks.append({"tool": name, "ok": True, "version": output.splitlines()[0]})
        except Problem as exc:
            checks.append({"tool": name, "ok": False, **exc.as_dict()})
    for kind in ("spotify", "rekordbox"):
        try:
            result = bridge(library, kind, {"operation": "doctor"})
            checks.append({"tool": kind + "-python", "ok": True, **result})
        except (Problem, ValueError) as exc:
            checks.append({"tool": kind + "-python", "ok": False, "message": str(exc)})
    return checks


def install(library):
    uv = shutil.which("uv")
    if not uv:
        raise Problem(
            "dependency-missing",
            "uv is needed to provision isolated downloader tools.",
            action="Install uv from https://docs.astral.sh/uv/getting-started/installation/ and rerun setup --install.",
        )
    environment = library.root / "tools"
    if not (environment / "bin/python").exists():
        run([uv, "venv", "--python", "3.11", environment], timeout=600)
    python = environment / "bin/python"
    run(
        [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "spotdl==4.5.0",
            "yt-dlp>=2026.7.4",
            "pyrekordbox==0.4.4",
            "imageio-ffmpeg>=0.6,<0.7",
        ],
        timeout=600,
    )
    try:
        run([python, "-c", "import sqlcipher3"], timeout=30)
    except Problem:
        brew = shutil.which("brew")
        if sys.platform == "darwin" and brew:
            run([brew, "install", "sqlcipher"], timeout=600)
            prefix = run([brew, "--prefix", "sqlcipher"]).strip()
            env = {**os.environ, "LDFLAGS": f"-L{prefix}/lib", "CFLAGS": f"-I{prefix}/include"}
            run([uv, "pip", "install", "--python", python, "sqlcipher3"], timeout=600, env=env)
        else:
            run([uv, "pip", "install", "--python", python, "sqlcipher3-binary"], timeout=600)
    for name in ("spotdl", "yt-dlp"):
        library.config["tools"][name] = str(environment / "bin" / name)
    for name in ("spotify-python", "rekordbox-python"):
        library.config["tools"][name] = str(python)
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if not executable and shutil.which("brew"):
            run([shutil.which("brew"), "install", "ffmpeg"], timeout=600)
            executable = shutil.which(name)
        if executable:
            library.config["tools"][name] = executable
    library.save_config()
    return doctor(library)
