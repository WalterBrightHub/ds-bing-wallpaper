"""Paths, constants and small helpers for DS Bing Wallpaper.

Standard library only: the whole app runs on the macOS system Python 3.9.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_NAME = "DS Bing Wallpaper"
APP_NAME_ZH = "DS 必应壁纸"
VERSION = "1.1.0"
BUNDLE_ID = "com.deepseek.dsbingwallpaper"

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PACKAGE_ROOT / "web"
BIN_DIR = PACKAGE_ROOT / "bin"
WALLPAPERCTL = BIN_DIR / "wallpaperctl"

DATA_DIR = Path(
    os.environ.get("DSBW_DATA_DIR")
    or (Path.home() / "Library" / "Application Support" / "DSBingWallpaper")
).expanduser()
WALLPAPER_DIR = DATA_DIR / "wallpapers"
THUMB_SUBDIR = "thumbs"
PREVIEW_SUBDIR = "previews"
INDEX_PATH = DATA_DIR / "index.json"
STATE_PATH = DATA_DIR / "state.json"
LOG_PATH = DATA_DIR / "app.log"

DEFAULT_PORT = int(os.environ.get("DSBW_PORT") or 8791)

# Bing serves a fresh image daily; we keep a rolling 10 day window.
RETENTION_DAYS = 10
ARCHIVE_URL = "https://www.bing.com/HPImageArchive.aspx"
BING_MKT = "zh-CN"
IMAGE_RESOLUTION = "UHD"  # 3840x2160; "1920x1200" also works
THUMB_WIDTH = 640
PREVIEW_WIDTH = 1600
REQUEST_TIMEOUT = 25
HOUR_SECONDS = 3600
# How often the sleeping loop wakes to check for a system resume. Keeps the
# post-wake detection latency small (a few seconds) at negligible cost.
POLL_SECONDS = 30
# Wall-clock drift (beyond monotonic time) that means the machine was asleep.
WAKE_DRIFT_THRESHOLD = 90
# Retry delay after a failed sync (e.g. no network right after waking up).
RETRY_SECONDS = 300

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for path in (DATA_DIR, WALLPAPER_DIR, WALLPAPER_DIR / THUMB_SUBDIR, WALLPAPER_DIR / PREVIEW_SUBDIR):
        path.mkdir(parents=True, exist_ok=True)


def find_python() -> str:
    """Resolve the interpreter to use from launcher scripts."""
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return "python3"
