"""Bing image archive client: metadata, download, thumbnails, naming.

How the image is discovered
---------------------------
`https://www.bing.com/?mkt=zh-CN` redirects to cn.bing.com and its HTML only
carries a low-res preview URL (`/th?id=..._tmb.jpg`). The same page loads its
wallpaper from the JSON endpoint below, which is what we use:

    https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8&mkt=zh-CN

Each entry exposes `urlbase` (`/th?id=OHR.NorthWindow_ZH-CN6734579353`) and the
full size is built by appending `_UHD.jpg&rf=LaDigue_UHD.jpg`
(3840x2160). `xml` and RSS formats also exist but JSON is the cleanest.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

OHR_RE = re.compile(r"OHR\.([A-Za-z0-9-]+)", re.IGNORECASE)
SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


class BingError(RuntimeError):
    pass


def _request(url: str, timeout: int = config.REQUEST_TIMEOUT) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "*/*",
            "Referer": "https://www.bing.com/",
        },
    )


def fetch_archive(n: int = 8, idx: int = 0, mkt: str = config.BING_MKT) -> List[Dict[str, Any]]:
    """Return raw archive entries, newest first."""
    query = urllib.parse.urlencode({"format": "js", "idx": idx, "n": n, "mkt": mkt})
    url = f"{config.ARCHIVE_URL}?{query}"
    try:
        with urllib.request.urlopen(_request(url), timeout=config.REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BingError(f"无法获取 Bing 图库信息: {exc}") from exc

    images = payload.get("images")
    if not isinstance(images, list) or not images:
        raise BingError("Bing 返回的图库信息为空")
    return images


def absolute_bing_url(value: object) -> Optional[str]:
    """Normalise a Bing-relative/absolute URL; reject anything that is not Bing.

    `copyrightlink` is an absolute URL and is the only link we render in the
    panel; keeping the normalisation here means a future relative field can be
    accepted without reopening an injection path.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/"):
        raw = "https://www.bing.com" + raw

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https":
        return None
    host = (parsed.netloc or "").lower()
    if host != "bing.com" and not host.endswith(".bing.com"):
        return None
    return raw


def ohr_token(entry: Dict[str, Any]) -> str:
    """`OHR.NorthWindow_ZH-CN6734579353` from urlbase, else from url."""
    urlbase = str(entry.get("urlbase") or "")
    match = re.search(r"[?&]id=([^&]+)", urlbase)
    if match:
        return match.group(1)
    url = str(entry.get("url") or "")
    match = re.search(r"[?&]id=([^&]+)", url)
    if match:
        return re.sub(r"_(?:UHD|1920x1200|1920x1080|\d+x\d+|tmb)\.jpg$", "", match.group(1))
    raise BingError("Bing 条目缺少图片 ID")


def slug_for(token: str) -> str:
    """`NorthWindow` -> `northwindow`; empty when no OHR name is present."""
    match = OHR_RE.search(token)
    if not match:
        return ""
    return SLUG_CLEAN_RE.sub("", match.group(1).lower())[:24]


def image_id(entry: Dict[str, Any]) -> str:
    token = ohr_token(entry)
    startdate = str(entry.get("startdate") or "").strip()
    if not re.fullmatch(r"\d{8}", startdate):
        raise BingError(f"Bing 条目日期异常: {startdate!r}")
    hsh = str(entry.get("hsh") or "").strip().lower()
    suffix = hsh[:8] if hsh else "00000000"
    slug = slug_for(token) or "image"
    return f"bing-{startdate}-{slug}-{suffix}"


def absolute_url(entry: Dict[str, Any], resolution: str = config.IMAGE_RESOLUTION) -> str:
    token = ohr_token(entry)
    return (
        f"https://www.bing.com/th?id={token}_{resolution}.jpg"
        f"&rf=LaDigue_{resolution}.jpg"
    )


def build_record(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata-only record (no file yet) for an archive entry."""
    new_id = image_id(entry)
    startdate = str(entry.get("startdate") or "")
    month_dir = f"{startdate[:4]}-{startdate[4:6]}"
    return {
        "id": new_id,
        "startdate": startdate,
        "title": str(entry.get("title") or "").strip(),
        "copyright": str(entry.get("copyright") or "").strip(),
        "copyrightlink": absolute_bing_url(entry.get("copyrightlink")) or "",
        "hsh": str(entry.get("hsh") or "").strip(),
        "source": "HPImageArchive",
        "url": absolute_url(entry),
        "file": f"{month_dir}/{new_id}.jpg",
        "thumb": f"{config.THUMB_SUBDIR}/{new_id}.jpg",
        "preview": f"{config.PREVIEW_SUBDIR}/{new_id}.jpg",
        "favorite": False,
    }


def _is_jpeg(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def download_image(record: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
    """Download the wallpaper to its canonical path; returns updated fields."""
    target = config.WALLPAPER_DIR / record["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    url = record["url"]

    last_error: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        tmp = target.with_suffix(".part")
        try:
            with urllib.request.urlopen(_request(url), timeout=config.REQUEST_TIMEOUT) as response:
                if response.status != 200:
                    raise BingError(f"HTTP {response.status}")
                data = response.read()
            if len(data) < 50_000:
                raise BingError(f"图片过小 ({len(data)} bytes)")
            tmp.write_bytes(data)
            if not _is_jpeg(tmp):
                tmp.unlink(missing_ok=True)
                raise BingError("下载内容不是 JPEG")
            tmp.replace(target)
            return {
                "bytes": len(data),
                "downloadedAt": _iso_now(),
                "width": 3840 if config.IMAGE_RESOLUTION == "UHD" else None,
                "height": 2160 if config.IMAGE_RESOLUTION == "UHD" else None,
            }
        except (urllib.error.URLError, OSError, BingError) as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < retries:
                import time

                time.sleep(1.5 * attempt)
    raise BingError(f"下载失败: {last_error}")


def _sips(input_path: Path, output_path: Path, width: int, quality: int) -> bool:
    sips = shutil.which("sips") or "/usr/bin/sips"
    if not Path(sips).exists():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sips, "-Z", str(width), "-s", "format", "jpeg",
             "-s", "formatOptions", str(quality), str(input_path), "--out", str(output_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return output_path.is_file()
    except (subprocess.SubprocessError, OSError):
        return False


def make_derivatives(record: Dict[str, Any]) -> bool:
    """Build the grid thumbnail (640px) and hero preview (1600px) with `sips`."""
    source = config.WALLPAPER_DIR / record["file"]
    if not source.is_file():
        return False
    ok = True
    for key, width, quality in (
        ("thumb", config.THUMB_WIDTH, 70),
        ("preview", config.PREVIEW_WIDTH, 80),
    ):
        rel = record.get(key)
        if not rel:
            continue
        target = config.WALLPAPER_DIR / rel
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            continue
        ok = _sips(source, target, width, quality) and ok
    return ok


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
