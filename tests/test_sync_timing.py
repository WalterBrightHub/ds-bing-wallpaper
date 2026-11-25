"""Sync timing tests: failure retry deadline and success cadence."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import bing, config, service, store  # noqa: E402


class SyncTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-sync-"))
        self._orig_paths = (config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH)
        config.DATA_DIR = self.tmp
        config.WALLPAPER_DIR = self.tmp / "wallpapers"
        config.INDEX_PATH = self.tmp / "index.json"
        config.STATE_PATH = self.tmp / "state.json"
        config.ensure_dirs()
        self._orig_fetch = bing.fetch_archive

    def tearDown(self):
        bing.fetch_archive = self._orig_fetch
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig_paths
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _next_delta(self, state) -> float:
        next_at = datetime.fromisoformat(state["nextCheckAt"])
        return (next_at - datetime.now().astimezone()).total_seconds()

    def test_network_failure_schedules_retry_not_an_hour(self):
        def offline(**_kwargs):
            raise bing.BingError("网络不可用")

        bing.fetch_archive = offline
        result = service.sync(trigger="wake")

        self.assertFalse(result["ok"])
        state = store.load_state()
        self.assertEqual(state["lastCheckResult"], "error")
        self.assertIn("网络不可用", state["lastError"])
        self.assertTrue(state["lastCheckAt"])
        # Retry in ~5 minutes, not a full hour, and the reported time agrees.
        self.assertAlmostEqual(self._next_delta(state), config.RETRY_SECONDS, delta=30)
        self.assertLess(config.RETRY_SECONDS, config.HOUR_SECONDS)

    def test_success_schedules_next_hour(self):
        bing.fetch_archive = lambda **_kwargs: []
        result = service.sync(trigger="scheduled")

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "empty")
        state = store.load_state()
        self.assertEqual(state["lastCheckResult"], "ok")
        self.assertIsNone(state["lastError"])
        self.assertAlmostEqual(self._next_delta(state), config.HOUR_SECONDS, delta=30)

    def test_poll_window_bounds_wake_latency(self):
        # The sleeping loop checks for a resume every POLL_SECONDS, so the
        # worst-case delay between wake and sync is one poll window.
        self.assertLessEqual(config.POLL_SECONDS, 60)
        self.assertLess(config.WAKE_DRIFT_THRESHOLD, config.HOUR_SECONDS)


if __name__ == "__main__":
    unittest.main()


class RevealLibraryTests(unittest.TestCase):
    """`打开文件夹` must only ever open the fixed library path."""

    class _Result:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = stderr

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-reveal-"))
        self._orig_paths = (config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH)
        config.DATA_DIR = self.tmp
        config.WALLPAPER_DIR = self.tmp / "wallpapers"
        config.INDEX_PATH = self.tmp / "index.json"
        config.STATE_PATH = self.tmp / "state.json"
        config.ensure_dirs()
        self._orig_run = service.subprocess.run
        self.calls = []

    def tearDown(self):
        service.subprocess.run = self._orig_run
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig_paths
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_opens_the_library_folder(self):
        def fake_run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return self._Result()

        service.subprocess.run = fake_run
        result = service.reveal_library()

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], str(config.WALLPAPER_DIR))
        argv = self.calls[0][0]
        self.assertEqual(argv[0], "/usr/bin/open")
        self.assertEqual(argv[1], str(config.WALLPAPER_DIR))
        self.assertEqual(len(argv), 2)

    def test_surfaces_finder_failure(self):
        service.subprocess.run = lambda argv, **kw: self._Result(returncode=1, stderr="no such file")
        result = service.reveal_library()
        self.assertFalse(result["ok"])
        self.assertIn("no such file", result["error"])

    def test_creates_folder_when_missing(self):
        service.subprocess.run = lambda argv, **kw: self._Result()
        shutil.rmtree(config.WALLPAPER_DIR, ignore_errors=True)
        service.reveal_library()
        self.assertTrue(config.WALLPAPER_DIR.is_dir())


class PausedStillFetchesTests(unittest.TestCase):
    """自动更新只控制「是否切换壁纸」，抓取/下载必须照常进行。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-paused-"))
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

        today = datetime.now().strftime("%Y%m%d")
        self.entry = {
            "startdate": today,
            "urlbase": "/th?id=OHR.TestScene_ZH-CN1234567890",
            "hsh": "abcdef1234567890",
            "title": "测试壁纸",
            "copyright": "测试版权",
            "copyrightlink": "",
        }
        bing.fetch_archive = lambda **_kw: [self.entry]

        def fake_download(record, retries=3):
            path = config.WALLPAPER_DIR / record["file"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xd8\xff" + b"0" * 200)
            return {"bytes": 203, "downloadedAt": "2026-01-01T00:00:00+08:00"}

        bing.download_image = fake_download
        bing.make_derivatives = lambda record: True
        service.wallpaper.set_wallpaper = lambda path: (self.applied.append(str(path)), (True, "ok"))[1]
        service.wallpaper.get_wallpaper = lambda: ""

    def tearDown(self):
        (bing.fetch_archive, bing.download_image, bing.make_derivatives,
         service.wallpaper.set_wallpaper, service.wallpaper.get_wallpaper) = self._orig
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig_paths
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_auto(self, enabled: bool):
        state = store.load_state()
        state["settings"]["mode"] = "latest" if enabled else "off"
        store.save_state(state)

    def test_paused_still_downloads_and_keeps_desktop(self):
        self._set_auto(False)
        result = service.sync(trigger="scheduled")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["downloaded"]), 1, "暂停自动更新时也必须继续抓取下载")
        ids = [img["id"] for img in store.list_images()]
        self.assertEqual(len(ids), 1)
        self.assertTrue((config.WALLPAPER_DIR / f"2026-11/{ids[0]}.jpg").exists()
                        or any(config.WALLPAPER_DIR.rglob(f"{ids[0]}.jpg")))

        state = store.load_state()
        self.assertIsNone(state["currentId"], "暂停时不应切换桌面壁纸")
        self.assertEqual(self.applied, [], "暂停时不应调用设置壁纸")
        self.assertEqual(state["lastCheckResult"], "ok")
        self.assertTrue(state["lastCheckAt"])

    def test_auto_enabled_still_downloads_and_switches(self):
        self._set_auto(True)
        result = service.sync(trigger="scheduled")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["downloaded"]), 1)
        self.assertEqual(result["action"], "switched")
        self.assertEqual(len(self.applied), 1, "自动更新开启时应设置壁纸")
        self.assertIsNotNone(store.load_state()["currentId"])
