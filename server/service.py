"""Business logic: fetch, download, prune, and decide what is on the desktop.

One lock serialises syncs so an hourly tick and a "refresh now" click cannot
race each other into downloading the same file twice or setting two wallpapers.
"""
from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import bing, config, store, wallpaper

_SYNC_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _cutoff_startdate(retention_days: int) -> str:
    return (_now() - timedelta(days=retention_days)).strftime("%Y%m%d")


# ------------------------------------------------------------ settings


def set_auto_update(enabled: bool) -> Dict[str, Any]:
    state = store.load_state()
    state["settings"]["autoUpdate"] = bool(enabled)
    if enabled:
        # Auto mode takes over: forget the pinned choice.
        state["selectedId"] = None
        state["pausedReason"] = None
    else:
        # Pausing keeps whatever is on the desktop right now as the pinned pick.
        state["pausedReason"] = "user"
        if not state.get("selectedId") and state.get("currentId"):
            state["selectedId"] = state["currentId"]
    state["updatedAt"] = _now_iso()
    store.save_state(state)
    return state


def toggle_favorite(image_id: str) -> Dict[str, Any]:
    image = store.get_image(image_id)
    if not image:
        raise KeyError(image_id)
    updated = store.mark_favorite(image_id, not image.get("favorite"))
    return updated or image


# ------------------------------------------------------------ sync


def _download_missing(entries: List[Dict[str, Any]], retention_days: int) -> Dict[str, Any]:
    """Ensure every in-window archive entry exists locally. Returns a summary."""
    cutoff = _cutoff_startdate(retention_days)
    added: List[str] = []
    failed: List[str] = []

    for entry in entries:
        try:
            record = bing.build_record(entry)
        except bing.BingError:
            continue
        if record["startdate"] < cutoff:
            continue

        existing = store.get_image(record["id"])
        if existing and store.file_path(existing).is_file():
            # Backfill derivatives for images downloaded before previews existed,
            # and keep metadata fresh (title/copyright wording can change).
            if not (store.thumb_path(existing).is_file() and store.preview_path(existing).is_file()):
                bing.make_derivatives(existing)
            store.upsert_image(record)
            continue

        try:
            extra = bing.download_image(record)
        except bing.BingError as exc:
            failed.append(f"{record['id']}: {exc}")
            store.upsert_image(record)
            continue

        record.update(extra)
        bing.make_derivatives(record)
        store.upsert_image(record)
        added.append(record["id"])

    return {"downloaded": added, "failed": failed}


def _apply(image: Dict[str, Any], reason: str) -> Dict[str, Any]:
    path = store.file_path(image)
    ok, message = wallpaper.set_wallpaper(path)
    state = store.load_state()
    if ok:
        state["currentId"] = image["id"]
        state["currentSetAt"] = _now_iso()
        state["currentSource"] = reason
        state["lastError"] = None
        store.touch_shown(image["id"])
    else:
        state["lastError"] = f"设置壁纸失败: {message}"
    state["updatedAt"] = _now_iso()
    store.save_state(state)
    return {"ok": ok, "message": message, "applied": image["id"] if ok else None}


def sync(trigger: str = "scheduled", force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch Bing metadata, top up the library, prune, then maybe switch.

    `trigger` is informational (startup / scheduled / manual).
    """
    if not _SYNC_LOCK.acquire(blocking=force_refresh):
        return {"ok": False, "skipped": "另一个同步正在进行", "trigger": trigger}
    try:
        state = store.load_state()
        settings = state["settings"]
        retention_days = int(settings.get("retentionDays") or config.RETENTION_DAYS)
        result: Dict[str, Any] = {"ok": True, "trigger": trigger, "at": _now_iso()}

        try:
            entries = bing.fetch_archive(n=8)
        except bing.BingError as exc:
            state["lastCheckAt"] = _now_iso()
            state["lastCheckResult"] = "error"
            state["lastError"] = str(exc)
            # The scheduler retries sooner than the hourly interval; keep the
            # reported next check in step with it.
            state["nextCheckAt"] = (
                _now() + timedelta(seconds=config.RETRY_SECONDS)
            ).isoformat(timespec="seconds")
            store.save_state(state)
            return {"ok": False, "error": str(exc), "trigger": trigger}

        download_summary = _download_missing(entries, retention_days)
        result.update(download_summary)

        removed = store.prune(
            keep_ids=[i for i in (state.get("currentId"), state.get("selectedId")) if i],
            retention_days=retention_days,
        )
        result["removed"] = removed

        images = store.list_images()
        available = [img for img in images if store.file_path(img).is_file()]
        latest = available[0] if available else None

        action = "kept"
        if latest is None:
            action = "empty"
        elif settings.get("autoUpdate"):
            current = store.get_image(state.get("currentId") or "")
            already = bool(current and current["id"] == latest["id"]) and _same_on_desktop(current)
            if not already:
                applied = _apply(latest, "auto")
                action = "switched" if applied["ok"] else "switch-failed"
            else:
                action = "unchanged"
        else:
            selected = store.get_image(state.get("selectedId") or "")
            if selected and not _same_on_desktop(selected):
                # Auto update is paused: keep the user's pick pinned.
                applied = _apply(selected, "restore-selected")
                action = "restored-selected" if applied["ok"] else "restore-failed"

        result["action"] = action

        state = store.load_state()
        state["lastCheckAt"] = _now_iso()
        state["lastCheckResult"] = "ok"
        state["nextCheckAt"] = (_now() + timedelta(seconds=config.HOUR_SECONDS)).isoformat(timespec="seconds")
        store.save_state(state)

        result["currentId"] = store.load_state().get("currentId")
        return result
    finally:
        _SYNC_LOCK.release()


def _same_on_desktop(image: Dict[str, Any]) -> bool:
    """True when the system wallpaper already points at this file.

    NSWorkspace can report the previous picture for a moment after a change,
    so retry briefly before deciding the desktop is out of sync.
    """
    target = store.file_path(image).resolve()
    for attempt in range(4):
        current = wallpaper.get_wallpaper()
        if not current:
            # Cannot read the current wallpaper; assume we are in sync so we do
            # not re-set it every hour.
            return True
        try:
            if Path(current).resolve() == target:
                return True
        except OSError:
            return False
        if attempt < 3:
            time.sleep(0.15)
    return False


def switch_to(image_id: str) -> Dict[str, Any]:
    """User picked a wallpaper: apply it and pause automatic updating."""
    image = store.get_image(image_id)
    if not image:
        raise KeyError(image_id)
    if not store.file_path(image).is_file():
        raise FileNotFoundError(image_id)

    applied = _apply(image, "user")
    state = store.load_state()
    if applied["ok"]:
        state["selectedId"] = image_id
        state["settings"]["autoUpdate"] = False
        state["pausedReason"] = "user-picked"
        state["updatedAt"] = _now_iso()
        store.save_state(state)
    return {"applied": applied, "state": store.load_state()}


def reveal_library() -> Dict[str, Any]:
    """Open the wallpaper library folder in Finder.

    Only ever opens the fixed library path, so there is no user-supplied input
    reaching the shell.
    """
    target = config.WALLPAPER_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["/usr/bin/open", str(target)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "path": str(target), "error": str(exc)}

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "path": str(target),
            "error": detail or f"open 退出码 {completed.returncode}",
        }
    return {"ok": True, "path": str(target)}


def refresh_now() -> Dict[str, Any]:
    return sync(trigger="manual", force_refresh=True)


# ------------------------------------------------------------ view model


def status() -> Dict[str, Any]:
    state = store.load_state()
    images = store.list_images()
    current_id = state.get("currentId")
    library: List[Dict[str, Any]] = []
    total_bytes = 0

    for image in images:
        path = store.file_path(image)
        thumb = store.thumb_path(image)
        exists = path.is_file()
        if exists:
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
        item = {
            "id": image.get("id"),
            "startdate": image.get("startdate"),
            "title": image.get("title") or "",
            "copyright": image.get("copyright") or "",
            "copyrightlink": bing.absolute_bing_url(image.get("copyrightlink")) or "",
            "quiz": bing.absolute_bing_url(image.get("quiz")) or "",
            "favorite": bool(image.get("favorite")),
            "available": exists,
            "hasThumb": thumb.is_file(),
            "hasPreview": store.preview_path(image).is_file(),
            "bytes": image.get("bytes"),
            "width": image.get("width"),
            "height": image.get("height"),
            "downloadedAt": image.get("downloadedAt"),
            "lastShownAt": image.get("lastShownAt"),
            "isCurrent": image.get("id") == current_id,
        }
        library.append(item)

    current_record = store.get_image(current_id) if current_id else None
    current_file = str(store.file_path(current_record)) if current_record else None
    reported = wallpaper.get_wallpaper()
    return {
        "app": {
            "name": config.APP_NAME,
            "nameZh": config.APP_NAME_ZH,
            "version": config.VERSION,
            "dataDir": str(config.DATA_DIR),
            "helper": wallpaper.helper_available(),
        },
        "settings": state.get("settings", {}),
        "autoUpdate": bool(state.get("settings", {}).get("autoUpdate", True)),
        "pausedReason": state.get("pausedReason"),
        "currentId": current_id,
        "selectedId": state.get("selectedId"),
        "currentSetAt": state.get("currentSetAt"),
        "currentSource": state.get("currentSource"),
        "desktopPath": current_file,
        "desktopReported": reported,
        "desktopMatches": _same_on_desktop(current_record) if current_record else False,
        "lastCheckAt": state.get("lastCheckAt"),
        "lastCheckResult": state.get("lastCheckResult"),
        "nextCheckAt": state.get("nextCheckAt"),
        "lastError": state.get("lastError"),
        "retentionDays": state.get("settings", {}).get("retentionDays", config.RETENTION_DAYS),
        "images": library,
        "stats": {
            "count": len(library),
            "favorites": sum(1 for i in library if i["favorite"]),
            "available": sum(1 for i in library if i["available"]),
            "totalBytes": total_bytes,
        },
        "screens": wallpaper.screen_info(),
        "serverTime": _now_iso(),
    }
