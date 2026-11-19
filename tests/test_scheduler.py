"""Wake detection and scheduler bookkeeping tests (no sleeping required)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config, scheduler  # noqa: E402


class DetectWakeTests(unittest.TestCase):
    def test_regular_poll_is_not_a_wake(self):
        # 30s of awake time: monotonic and wall clock advance together.
        self.assertEqual(scheduler.detect_wake(100.0, 1000.0, 130.0, 1030.0), 0.0)

    def test_two_hour_sleep_is_detected(self):
        # Monotonic advanced 2s (pre-suspend work), wall clock advanced 2h.
        drift = scheduler.detect_wake(100.0, 1000.0, 102.0, 1000.0 + 7200)
        self.assertAlmostEqual(drift, 7198.0)

    def test_short_nap_below_threshold_is_ignored(self):
        drift = config.WAKE_DRIFT_THRESHOLD - 1
        self.assertEqual(scheduler.detect_wake(0.0, 0.0, 10.0, 10.0 + drift), 0.0)

    def test_exactly_threshold_counts_as_wake(self):
        drift = config.WAKE_DRIFT_THRESHOLD
        got = scheduler.detect_wake(0.0, 0.0, 10.0, 10.0 + drift)
        self.assertEqual(got, float(drift))

    def test_backward_clock_change_is_not_a_wake(self):
        # NTP stepped the clock backwards; nothing to do.
        self.assertEqual(scheduler.detect_wake(100.0, 1000.0, 130.0, 900.0), 0.0)

    def test_monotonic_jump_ahead_is_not_a_wake(self):
        # Defensive: monotonic running ahead yields negative drift.
        self.assertEqual(scheduler.detect_wake(100.0, 1000.0, 500.0, 1030.0), 0.0)

    def test_threshold_is_configurable(self):
        self.assertEqual(scheduler.detect_wake(0.0, 0.0, 1.0, 31.0, threshold=10), 30.0)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_wake_fields(self):
        sched = scheduler.Scheduler(interval=3600)
        snap = sched.snapshot()
        self.assertEqual(snap["intervalSeconds"], 3600)
        self.assertEqual(snap["pollSeconds"], config.POLL_SECONDS)
        self.assertIsNone(snap["lastWakeAt"])
        self.assertEqual(snap["wakeSyncs"], 0)
        self.assertIn("nextRunAt", snap)
        self.assertIn("lastResult", snap)

    def test_poll_interval_is_short_enough(self):
        # Wake latency is bounded by POLL_SECONDS; keep it well under a minute.
        self.assertLessEqual(config.POLL_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
