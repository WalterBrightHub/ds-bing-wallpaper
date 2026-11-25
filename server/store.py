"""Persistent store: wallpaper index + app state, kept in two JSON files.

A single process owns these files (the web server), so a re-entrant lock is
enough. Writes go through a temp file + os.replace so a crash mid-write can
never leave a truncated index behind.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

_LOCK = threading.RLock()

# Fields owned by the user, never by a remote metadata refresh.
LOCAL_ONLY_FIELDS = ("favorite", "favoriteAt", "lastShownAt")

# `mode` is the single source of truth:
#   "latest"    — keep switching to Bing's newest image
#   "favorites" — rotate through the user's favourites
#   "off"       — pin whatever is on the desktop right now
DEFAULT_SETTINGS: Dict[str, Any] = {
    "mode": "latest",
    "lastActiveMode": "latest",            # what to resume when leaving "off"
    "favoriteIntervalMinutes": config.DEFAULT_FAVORITE_INTERVAL,
    "retentionDays": config.RETENTION_DAYS,
}

DEFAULT_STATE: Dict[str, Any] = {
    "version": 1,
    "settings": dict(DEFAULT_SETTINGS),
    "currentId": None,
    "currentSetAt": None,
    "currentSource": None,
    "selectedId": None,
    "pausedReason": None,
    "lastCheckAt": None,
    "lastCheckResult": None,
    "lastError": None,
    "nextCheckAt": None,
    "nextRotateAt": None,
    "lastRotateAt": None,
    "lastRotateResult": None,
    "updatedAt": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


# ---------------------------------------------------------------- state


def load_state() -> Dict[str, Any]:
    with _LOCK:
        raw = _read_json(config.STATE_PATH, None)
        state = json.loads(json.dumps(DEFAULT_STATE))
        if isinstance(raw, dict):
            settings = raw.get("settings")
            if isinstance(settings, dict):
                migrated = dict(settings)
                # 旧版本用 autoUpdate(bool) + mode(latest|favorites) 两个字段，
                # 升级后统一成三态 mode，避免“收藏轮换开着、自动切换也开着”。
                raw_mode = migrated.get("mode")
                legacy_auto = migrated.get("autoUpdate")
                if raw_mode not in ("off", "latest", "favorites"):
                    raw_mode = "off" if legacy_auto is False else "latest"
                elif legacy_auto is False:
                    raw_mode = "off"
                migrated["mode"] = raw_mode
                if raw_mode != "off":
                    migrated.setdefault("lastActiveMode", raw_mode)
                state["settings"].update(
                    {k: v for k, v in migrated.items() if k in DEFAULT_SETTINGS}
                )
            for key in state:
                if key in ("version", "settings"):
                    continue
                if key in raw:
                    state[key] = raw[key]
        try:
            state["settings"]["retentionDays"] = int(
                state["settings"].get("retentionDays", config.RETENTION_DAYS)
            )
        except (TypeError, ValueError):
            state["settings"]["retentionDays"] = config.RETENTION_DAYS

        if state["settings"].get("mode") not in ("off", "latest", "favorites"):
            state["settings"]["mode"] = "latest"
        if state["settings"].get("lastActiveMode") not in ("latest", "favorites"):
            state["settings"]["lastActiveMode"] = (
                state["settings"]["mode"]
                if state["settings"]["mode"] in ("latest", "favorites")
                else "latest"
            )
        try:
            interval = int(state["settings"].get("favoriteIntervalMinutes"))
        except (TypeError, ValueError):
            interval = config.DEFAULT_FAVORITE_INTERVAL
        if interval not in config.FAVORITE_INTERVALS:
            interval = config.DEFAULT_FAVORITE_INTERVAL
        state["settings"]["favoriteIntervalMinutes"] = interval
        return state


def save_state(state: Dict[str, Any]) -> None:
    with _LOCK:
        _atomic_write_json(config.STATE_PATH, state)


def update_state(**changes: Any) -> Dict[str, Any]:
    with _LOCK:
        state = load_state()
        state.update(changes)
        state["updatedAt"] = _now_iso()
        save_state(state)
        return state


# ---------------------------------------------------------------- index


def load_index() -> Dict[str, Any]:
    with _LOCK:
        raw = _read_json(config.INDEX_PATH, None)
        if not isinstance(raw, dict) or not isinstance(raw.get("images"), list):
            return {"version": 1, "images": []}
        images = [img for img in raw["images"] if isinstance(img, dict) and img.get("id")]
        return {"version": 1, "images": images}


def save_index(index: Dict[str, Any]) -> None:
    with _LOCK:
        index["updatedAt"] = _now_iso()
        _atomic_write_json(config.INDEX_PATH, index)


def list_images() -> List[Dict[str, Any]]:
    """Images sorted newest first."""
    images = load_index()["images"]
    return sorted(images, key=lambda img: (img.get("startdate") or "", img.get("id") or ""), reverse=True)


def get_image(image_id: str) -> Optional[Dict[str, Any]]:
    for image in load_index()["images"]:
        if image.get("id") == image_id:
            return image
    return None


def upsert_image(record: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a fetched record into the index, keeping local-only fields."""
    with _LOCK:
        index = load_index()
        images = index["images"]
        for position, existing in enumerate(images):
            if existing.get("id") == record["id"]:
                merged = dict(existing)
                for key, value in record.items():
                    # Local-only state must survive a metadata refresh.
                    if value is None or key in LOCAL_ONLY_FIELDS:
                        continue
                    merged[key] = value
                images[position] = merged
                save_index(index)
                return merged
        images.append(record)
        save_index(index)
        return record


def mark_favorite(image_id: str, favorite: bool) -> Optional[Dict[str, Any]]:
    with _LOCK:
        index = load_index()
        for image in index["images"]:
            if image.get("id") == image_id:
                image["favorite"] = bool(favorite)
                image["favoriteAt"] = _now_iso() if favorite else None
                save_index(index)
                return image
        return None


def touch_shown(image_id: str) -> None:
    with _LOCK:
        index = load_index()
        changed = False
        for image in index["images"]:
            if image.get("id") == image_id:
                image["lastShownAt"] = _now_iso()
                changed = True
        if changed:
            save_index(index)


def file_path(record: Dict[str, Any]) -> Path:
    """Absolute path of a downloaded wallpaper."""
    rel = record.get("file") or ""
    return config.WALLPAPER_DIR / rel


def thumb_path(record: Dict[str, Any]) -> Path:
    rel = record.get("thumb") or ""
    return config.WALLPAPER_DIR / rel


def preview_path(record: Dict[str, Any]) -> Path:
    rel = record.get("preview") or ""
    return config.WALLPAPER_DIR / rel


def prune(keep_ids: Optional[List[str]] = None, retention_days: int = config.RETENTION_DAYS) -> List[str]:
    """Drop wallpapers older than the retention window that are not protected.

    Protected: favourites, the current wallpaper, and anything in keep_ids.
    Returns the ids that were removed from the index. Files are deleted too.
    """
    protected = {i for i in (keep_ids or []) if i}
    cutoff = (datetime.now().astimezone() - timedelta(days=retention_days)).strftime("%Y%m%d")

    with _LOCK:
        index = load_index()
        keep: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        for image in index["images"]:
            startdate = str(image.get("startdate") or "")
            image_id = image.get("id") or ""
            is_old = bool(startdate) and startdate < cutoff
            if is_old and not image.get("favorite") and image_id not in protected:
                removed.append(image)
            else:
                keep.append(image)
        if not removed:
            return []
        index["images"] = keep
        save_index(index)

    for image in removed:
        for path in (file_path(image), thumb_path(image)):
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
    return [image.get("id") for image in removed]
