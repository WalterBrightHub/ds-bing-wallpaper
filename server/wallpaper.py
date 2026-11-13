"""Thin wrapper around the native `wallpaperctl` helper.

`wallpaperctl` calls NSWorkspace.setDesktopImageURL, which works without the
Automation permission that `osascript`/System Events needs. `osascript` stays
as a fallback for the case where the helper cannot be compiled on this machine.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Tuple

from . import config


def _run(argv, timeout: int = 20) -> Tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, "", str(exc)


def helper_available() -> bool:
    return config.WALLPAPERCTL.is_file() and config.WALLPAPERCTL.stat().st_size > 0


def get_wallpaper() -> Optional[str]:
    if not helper_available():
        return None
    code, out, _ = _run([str(config.WALLPAPERCTL), "get"])
    if code == 0 and out:
        return out.splitlines()[0].strip() or None
    return None


def screen_info():
    if not helper_available():
        return []
    code, out, _ = _run([str(config.WALLPAPERCTL), "get", "--json"])
    if code != 0 or not out:
        return []
    import json

    try:
        return json.loads(out).get("screens", [])
    except ValueError:
        return []


def set_wallpaper(path: Path) -> Tuple[bool, str]:
    """Set `path` on every screen. Returns (ok, message)."""
    if not path.is_file():
        return False, f"文件不存在: {path}"

    if helper_available():
        code, out, err = _run([str(config.WALLPAPERCTL), "set", str(path)])
        if code == 0:
            return True, "ok"
        helper_error = err or out or f"exit {code}"
    else:
        helper_error = "wallpaperctl 未编译"

    # Fallback: AppleScript (requires Automation permission on first use).
    script = (
        'tell application "System Events" to set picture of every desktop to '
        + f'POSIX file "{path}"'
    )
    code, _, err = _run(["/usr/bin/osascript", "-e", script], timeout=30)
    if code == 0:
        return True, "ok (osascript)"

    return False, f"{helper_error}; osascript: {err}"
