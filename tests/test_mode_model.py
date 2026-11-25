"""三态模式（off / latest / favorites）的迁移与不变量。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import bing, config, service, store  # noqa: E402


class ModeModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-mode-"))
        self._orig_paths = (config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH)
        config.DATA_DIR = self.tmp
        config.WALLPAPER_DIR = self.tmp / "wallpapers"
        config.INDEX_PATH = self.tmp / "index.json"
        config.STATE_PATH = self.tmp / "state.json"
        config.ensure_dirs()
        self._orig_wall = service.wallpaper.set_wallpaper
        self._orig_fetch = bing.fetch_archive
        service.wallpaper.set_wallpaper = lambda path: (True, "ok")
        bing.fetch_archive = lambda **_kw: []   # 不联网

    def tearDown(self):
        service.wallpaper.set_wallpaper = self._orig_wall
        bing.fetch_archive = self._orig_fetch
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig_paths
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _legacy_state(self, settings):
        config.STATE_PATH.write_text(
            json.dumps({"version": 1, "settings": settings, "currentId": "img-x"}),
            encoding="utf-8",
        )

    def _add_image(self, image_id="img-x", favorite=False):
        record = {
            "id": image_id,
            "startdate": "20261120",
            "file": f"2026-11/{image_id}.jpg",
            "favorite": favorite,
        }
        path = config.WALLPAPER_DIR / record["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff")
        store.upsert_image(record)
        return record

    # -- 迁移 -----------------------------------------------------

    def test_legacy_paused_state_migrates_to_off(self):
        self._legacy_state({"autoUpdate": False, "mode": "favorites", "retentionDays": 10})
        state = store.load_state()
        self.assertEqual(state["settings"]["mode"], "off")
        self.assertFalse(service._enabled(state["settings"]))

    def test_legacy_enabled_without_mode_migrates_to_latest(self):
        self._legacy_state({"autoUpdate": True, "retentionDays": 10})
        self.assertEqual(store.load_state()["settings"]["mode"], "latest")

    def test_legacy_favorites_mode_migrates_to_favorites(self):
        self._legacy_state({"autoUpdate": True, "mode": "favorites", "favoriteIntervalMinutes": 60})
        state = store.load_state()
        self.assertEqual(state["settings"]["mode"], "favorites")
        self.assertEqual(state["settings"]["favoriteIntervalMinutes"], 60)

    def test_saved_state_has_no_legacy_auto_update_key(self):
        self._legacy_state({"autoUpdate": False, "mode": "favorites"})
        store.save_state(store.load_state())
        raw = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("autoUpdate", raw["settings"])
        self.assertEqual(raw["settings"]["mode"], "off")

    # -- 三态切换 -------------------------------------------------

    def test_off_pins_current_and_stops_rotation(self):
        self._add_image()
        state = store.load_state()
        state["currentId"] = "img-x"
        state["settings"]["mode"] = "favorites"
        store.save_state(state)

        result = service.set_mode("off")
        state = store.load_state()

        self.assertEqual(result["mode"], "off")
        self.assertEqual(state["settings"]["mode"], "off")
        self.assertEqual(state["selectedId"], "img-x")
        self.assertIsNone(state["nextRotateAt"])
        self.assertFalse(service.rotation_due())

    def test_favorites_mode_schedules_and_rotates(self):
        self._add_image(favorite=True)
        service.set_mode("favorites", 60)
        state = store.load_state()

        self.assertEqual(state["settings"]["mode"], "favorites")
        self.assertEqual(state["currentId"], "img-x")
        self.assertTrue(state["nextRotateAt"])
        self.assertTrue(service._enabled(state["settings"]))

    def test_auto_update_compat_maps_to_mode(self):
        self._add_image(favorite=True)
        service.set_mode("favorites", 60)

        state = service.set_auto_update(False)
        self.assertEqual(state["settings"]["mode"], "off")
        self.assertEqual(state["settings"]["lastActiveMode"], "favorites")

        state = service.set_auto_update(True)
        self.assertEqual(state["settings"]["mode"], "favorites", "恢复时应回到上一次启用的模式")

    def test_mode_and_enabled_can_never_disagree(self):
        self._add_image()
        for mode in ("off", "latest", "favorites"):
            service.set_mode(mode)
            enabled = service._enabled(store.load_state()["settings"])
            self.assertEqual(enabled, mode != "off")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            service.set_mode("paused")


if __name__ == "__main__":
    unittest.main()
