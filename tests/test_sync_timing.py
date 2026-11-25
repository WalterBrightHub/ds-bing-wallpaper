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
