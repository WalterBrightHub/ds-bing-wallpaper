"""Hourly background refresh loop.

Runs inside the web server process. Two kinds of early wake-up are handled:

* `trigger_now()` — an explicit nudge from the API (kept for completeness).
* System resume — macOS stops `time.monotonic()` while asleep, so a wall-clock
  jump that monotonic cannot explain means the machine was suspended. When that
  happens the loop syncs immediately instead of waiting out the remainder of the
  hour, which is what makes "wake the laptop, get today's wallpaper" work.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from . import config, service


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"[{stamp}] {message}\n"
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with config.LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    try:
        print(line.rstrip(), flush=True)
    except (OSError, ValueError):
        pass


def detect_wake(
    mono_prev: float,
    wall_prev: float,
    mono_now: float,
    wall_now: float,
    threshold: float = config.WAKE_DRIFT_THRESHOLD,
) -> float:
    """Seconds of wall-clock time that monotonic time did not account for.

    On macOS `time.monotonic()` freezes while the system sleeps, so after a
    resume the wall clock has run ahead. Small drift (NTP corrections, slow
    poll loops) stays below the threshold; a suspend/resume exceeds it.

    Returns the drift when it looks like a wake, otherwise 0.0.
    """
    drift = (wall_now - wall_prev) - (mono_now - mono_prev)
    if drift >= threshold:
        return drift
    return 0.0


class Scheduler(threading.Thread):
    def __init__(self, interval: int = config.HOUR_SECONDS) -> None:
        super().__init__(name="dsbw-scheduler", daemon=True)
        self.interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._first_run = True
        self._running = False
        self._last_result: Optional[Dict[str, Any]] = None
        self._next_run: Optional[datetime] = None
        self._next_delay = interval
        self._last_wake_at: Optional[datetime] = None
        self._wake_syncs = 0
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------

    def start_soon(self, delay: float = 1.5) -> None:
        """Kick off the startup fetch shortly after the server binds."""
        if delay:
            threading.Timer(delay, self._wake.set).start()
        self.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_now(self) -> None:
        self._wake.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "intervalSeconds": self.interval,
                "pollSeconds": config.POLL_SECONDS,
                "running": self._running,
                "nextRunAt": self._next_run.isoformat(timespec="seconds") if self._next_run else None,
                "lastResult": self._last_result,
                "lastWakeAt": self._last_wake_at.isoformat(timespec="seconds") if self._last_wake_at else None,
                "wakeSyncs": self._wake_syncs,
            }

    # -- loop -----------------------------------------------------

    def run(self) -> None:
        # Wait for the startup kick, then let the loop own the cadence.
        self._wake.wait()
        trigger = "startup"
        while not self._stop.is_set():
            try:
                self._tick(trigger)
                trigger = self._await_next()
            except Exception as exc:  # a daemon thread must never die silently
                log(f"scheduler loop error: {exc!r} — retrying in 5s")
                time.sleep(5)
                trigger = "retry"
            if trigger == "stopped":
                break

    def _tick(self, trigger: str) -> None:
        self._wake.clear()
        with self._lock:
            self._running = True
            first = self._first_run
            self._first_run = False
        label = "startup" if first else trigger

        try:
            result = service.sync(trigger=label)
            log(f"sync trigger={label} result={result}")
        except Exception as exc:  # keep the loop alive no matter what
            log(f"sync crashed: {exc!r}")
            result = {"ok": False, "error": str(exc)}

        # A failed sync (offline right after wake, Bing hiccup) retries sooner
        # than a full hour so the daily switch is not postponed for long.
        delay = self.interval if result.get("ok") else config.RETRY_SECONDS
        with self._lock:
            self._running = False
            self._last_result = result
            self._next_delay = delay
            self._next_run = datetime.now().astimezone() + timedelta(seconds=delay)

    def _await_next(self) -> str:
        """Block until the next sync is due. Returns the trigger label."""
        deadline = time.monotonic() + self._next_delay
        mono_prev = time.monotonic()
        wall_prev = time.time()

        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "scheduled" if self._next_delay == self.interval else "retry"

            woke = self._wake.wait(timeout=min(remaining, config.POLL_SECONDS))

            mono_now = time.monotonic()
            wall_now = time.time()
            drift = detect_wake(mono_prev, wall_prev, mono_now, wall_now)
            mono_prev, wall_prev = mono_now, wall_now

            if drift:
                with self._lock:
                    self._last_wake_at = datetime.now().astimezone()
                    self._wake_syncs += 1
                log(f"system wake detected (clock drift {drift:.0f}s) — syncing now")
                return "wake"

            if woke:
                self._wake.clear()
                return "stopped" if self._stop.is_set() else "manual"

        return "stopped"
