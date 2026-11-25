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
    settings = state["settings"]
    settings["autoUpdate"] = bool(enabled)
    mode = settings.get("mode") or "latest"

    if enabled:
        # Auto mode takes over: forget the pinned choice.
        state["selectedId"] = None
        state["pausedReason"] = None
        if mode == "favorites":
            # Resume the rotation from now instead of firing immediately.
            minutes = int(settings.get("favoriteIntervalMinutes") or config.DEFAULT_FAVORITE_INTERVAL)
            state["nextRotateAt"] = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        else:
            state["nextRotateAt"] = None
    else:
        # Pausing keeps whatever is on the desktop right now as the pinned pick.
        state["pausedReason"] = "user"
        state["nextRotateAt"] = None
        if not state.get("selectedId") and state.get("currentId"):
            state["selectedId"] = state["currentId"]
    state["updatedAt"] = _now_iso()
    store.save_state(state)
    return state


def _clamp_interval(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return config.DEFAULT_FAVORITE_INTERVAL
    if minutes in config.FAVORITE_INTERVALS:
        return minutes
    return min(config.FAVORITE_INTERVALS, key=lambda option: abs(option - minutes))


def set_mode(mode: str, interval_minutes: Any = None) -> Dict[str, Any]:
    """Switch between following the newest Bing image and rotating favourites."""
    if mode not in ("latest", "favorites"):
        raise ValueError(f"未知模式: {mode}")

    state = store.load_state()
    settings = state["settings"]
    settings["mode"] = mode
    settings["autoUpdate"] = True
    if interval_minutes is not None:
        settings["favoriteIntervalMinutes"] = _clamp_interval(interval_minutes)
    state["selectedId"] = None
    state["pausedReason"] = None
    state["updatedAt"] = _now_iso()

    if mode == "favorites":
        minutes = int(settings.get("favoriteIntervalMinutes") or config.DEFAULT_FAVORITE_INTERVAL)
        state["nextRotateAt"] = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        store.save_state(state)
        return {"mode": mode, "rotate": rotate_favorite(force=True), "state": store.load_state()}

    state["nextRotateAt"] = None
    store.save_state(state)
    return {"mode": mode, "sync": sync(trigger="mode-latest"), "state": store.load_state()}


def rotation_due() -> bool:
    """True when favourites rotation should run now."""
    state = store.load_state()
    settings = state["settings"]
    if not settings.get("autoUpdate") or (settings.get("mode") or "latest") != "favorites":
        return False
    due = state.get("nextRotateAt")
    if not due:
        return True
    try:
        return datetime.fromisoformat(due) <= _now()
    except (TypeError, ValueError):
        return True


def _favorite_images() -> List[Dict[str, Any]]:
    return [img for img in store.list_images() if img.get("favorite") and store.file_path(img).is_file()]


def rotate_favorite(force: bool = False) -> Dict[str, Any]:
    """Apply the least recently shown favourite and schedule the next turn."""
    state = store.load_state()
    settings = state["settings"]
    minutes = int(settings.get("favoriteIntervalMinutes") or config.DEFAULT_FAVORITE_INTERVAL)

    if not force and (not settings.get("autoUpdate") or (settings.get("mode") or "latest") != "favorites"):
        return {"ok": False, "reason": "mode-disabled"}

    favorites = _favorite_images()
    if not favorites:
        state["nextRotateAt"] = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        state["lastRotateResult"] = "no-favorites"
        store.save_state(state)
        return {"ok": False, "reason": "no-favorites", "intervalMinutes": minutes}

    # 从未展示过的优先，其次是最久没展示的，实现均匀轮换。
    favorites.sort(key=lambda img: (img.get("lastShownAt") or "", img.get("startdate") or "", img.get("id") or ""))
    target = favorites[0]
    current_id = state.get("currentId")

    if target["id"] == current_id:
        result = {"ok": True, "unchanged": True, "currentId": current_id}
    else:
        applied = _apply(target, "favorite-rotation")
        result = {"ok": applied["ok"], "currentId": target["id"], "error": applied.get("message")}

    state = store.load_state()
    state["nextRotateAt"] = (_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    state["lastRotateAt"] = _now_iso()
    state["lastRotateResult"] = "rotated" if result.get("ok") else "failed"
    store.save_state(state)
    result["intervalMinutes"] = minutes
    result["source"] = "favorites"
    return result


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

        mode = settings.get("mode") or "latest"
        action = "kept"
        if latest is None:
            action = "empty"
        elif settings.get("autoUpdate") and mode == "favorites":
            # 收藏轮换模式下，同步只负责图库；桌面由轮换逻辑维护。
            current = store.get_image(state.get("currentId") or "")
            if current and not _same_on_desktop(current):
                applied = _apply(current, "restore-favorite")
                action = "restored-favorite" if applied["ok"] else "restore-failed"
            else:
                action = "favorite-mode"
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
        "mode": state.get("settings", {}).get("mode", "latest"),
        "favoriteIntervalMinutes": state.get("settings", {}).get("favoriteIntervalMinutes", config.DEFAULT_FAVORITE_INTERVAL),
        "nextRotateAt": state.get("nextRotateAt"),
        "lastRotateAt": state.get("lastRotateAt"),
        "lastRotateResult": state.get("lastRotateResult"),
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
