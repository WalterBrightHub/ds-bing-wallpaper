"""收藏轮换模式的调度与切换逻辑。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import bing, config, service, store  # noqa: E402


class FavoritesModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-fav-"))
        self._orig_paths = (config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH)
        config.DATA_DIR = self.tmp
        config.WALLPAPER_DIR = self.tmp / "wallpapers"
        config.INDEX_PATH = self.tmp / "index.json"
        config.STATE_PATH = self.tmp / "state.json"
        config.ensure_dirs()

        self._orig = (
            bing.fetch_archive, bing.download_image, bing.make_derivatives,
            service.wallpaper.set_wallpaper, service.wallpaper.get_wallpaper,
        )
        self.applied = []
        service.wallpaper.set_wallpaper = lambda path: (self.applied.append(str(path)), (True, "ok"))[1]
        # 让 _same_on_desktop 认为桌面已经是目标图，避免额外的恢复操作
        service.wallpaper.get_wallpaper = lambda: ""

    def tearDown(self):
        (bing.fetch_archive, bing.download_image, bing.make_derivatives,
         service.wallpaper.set_wallpaper, service.wallpaper.get_wallpaper) = self._orig
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig_paths
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -------------------------------------------------

    def _add(self, image_id: str, startdate: str, favorite: bool = False, last_shown=None):
        record = {
            "id": image_id,
            "startdate": startdate,
            "file": f"{startdate[:4]}-{startdate[4:6]}/{image_id}.jpg",
            "thumb": f"thumbs/{image_id}.jpg",
            "preview": f"previews/{image_id}.jpg",
            "favorite": favorite,
            "lastShownAt": last_shown,
            "title": image_id,
        }
        path = config.WALLPAPER_DIR / record["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
        store.upsert_image(record)
        return record

    def _state(self):
        return store.load_state()

    # -- tests ---------------------------------------------------

    def test_set_mode_favorites_applies_oldest_favorite(self):
        self._add("img-a", "20261101", favorite=True)
        self._add("img-b", "20261102", favorite=True, last_shown="2026-11-20T10:00:00+08:00")

        result = service.set_mode("favorites", 60)

        state = self._state()
        self.assertEqual(state["settings"]["mode"], "favorites")
        self.assertEqual(state["settings"]["mode"], "favorites")
        self.assertEqual(state["settings"]["favoriteIntervalMinutes"], 60)
        self.assertEqual(state["currentId"], "img-a")
        self.assertTrue(result["rotate"]["ok"])
        # 下次轮换约在一小时后
        delta = (datetime.fromisoformat(state["nextRotateAt"]) - datetime.now().astimezone()).total_seconds()
        self.assertAlmostEqual(delta, 3600, delta=60)

    def test_rotation_cycles_between_favorites(self):
        self._add("img-a", "20261101", favorite=True)
        self._add("img-b", "20261102", favorite=True)
        service.set_mode("favorites", 30)
        self.assertEqual(self._state()["currentId"], "img-a")

        service.rotate_favorite()
        self.assertEqual(self._state()["currentId"], "img-b")

        service.rotate_favorite()
        self.assertEqual(self._state()["currentId"], "img-a")

    def test_disabled_mode_ignores_rotation(self):
        self._add("img-a", "20261101", favorite=True)
        state = self._state()
        state["settings"]["mode"] = "off"
        store.save_state(state)

        self.assertFalse(service.rotation_due())
        result = service.rotate_favorite()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "mode-disabled")

    def test_rotation_due_respects_clock(self):
        self._add("img-a", "20261101", favorite=True)
        state = self._state()
        state["settings"]["mode"] = "favorites"
        store.save_state(state)

        self.assertTrue(service.rotation_due())  # 没有排期时立即到期

        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
        future = (datetime.now().astimezone() + timedelta(hours=1)).isoformat(timespec="seconds")
        for value, expected in ((past, True), (future, False)):
            state = self._state()
            state["nextRotateAt"] = value
            store.save_state(state)
            self.assertEqual(service.rotation_due(), expected)

    def test_no_favorites_keeps_desktop_and_reschedules(self):
        service.set_mode("favorites", 60)
        state = self._state()
        self.assertIsNone(state["currentId"])
        self.assertEqual(self.applied, [])
        self.assertEqual(state["lastRotateResult"], "no-favorites")
        self.assertTrue(state["nextRotateAt"])

    def test_sync_in_favorites_mode_does_not_jump_to_latest(self):
        self._add("img-a", "20261101", favorite=True)
        service.set_mode("favorites", 60)

        today = datetime.now().strftime("%Y%m%d")
        bing.fetch_archive = lambda **_kw: [{
            "startdate": today,
            "urlbase": "/th?id=OHR.FreshScene_ZH-CN1",
            "hsh": "ffffffffffffffff",
            "title": "新图",
            "copyright": "x",
            "copyrightlink": "",
        }]
        def fake_download(record, retries=3):
            (config.WALLPAPER_DIR / record["file"]).write_bytes(b"\xff\xd8\xff")
            return {"bytes": 3, "downloadedAt": "2026-01-01T00:00:00+08:00"}

        bing.download_image = fake_download
        bing.make_derivatives = lambda record: True

        result = service.sync(trigger="scheduled")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "favorite-mode")
        self.assertEqual(self._state()["currentId"], "img-a")  # 没有被新图顶掉
        self.assertGreaterEqual(len(store.list_images()), 2)   # 但图库仍然下载了新图

    def test_interval_is_clamped_to_allowed_values(self):
        self._add("img-a", "20261101", favorite=True)
        service.set_mode("favorites", 45)
        self.assertIn(self._state()["settings"]["favoriteIntervalMinutes"], config.FAVORITE_INTERVALS)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            service.set_mode("random")


if __name__ == "__main__":
    unittest.main()
