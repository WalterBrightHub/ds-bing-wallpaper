"""Hourly background refresh loop.

Runs inside the web server process. `trigger_now()` makes the loop wake up
immediately, which is how the "立即刷新" button works without a second thread.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from . import config, service, store


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"[{stamp}] {message}\n"
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with config.LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    print(line.rstrip(), flush=True)


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
                "running": self._running,
                "nextRunAt": self._next_run.isoformat(timespec="seconds") if self._next_run else None,
                "lastResult": self._last_result,
            }

    # -- loop -----------------------------------------------------

    def run(self) -> None:
        # Wait for the startup kick, then let _tick() own the hourly cadence.
        self._wake.wait()
        while not self._stop.is_set():
            self._wake.clear()
            self._tick()

    def _tick(self) -> None:
        with self._lock:
            self._running = True
            first = self._first_run
            self._first_run = False
        try:
            result = service.sync(trigger="startup" if first else "scheduled")
            log(f"sync trigger={'startup' if first else 'scheduled'} result={result}")
        except Exception as exc:  # keep the loop alive no matter what
            log(f"sync crashed: {exc!r}")
            result = {"ok": False, "error": str(exc)}
        finally:
            with self._lock:
                self._running = False
                self._last_result = result
                self._next_run = datetime.now().astimezone() + timedelta(seconds=self.interval)

        # Sleep until the next hour, but wake early when triggered.
        deadline = time.monotonic() + self.interval
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._wake.wait(timeout=min(remaining, 30)):
                self._wake.clear()
                break
