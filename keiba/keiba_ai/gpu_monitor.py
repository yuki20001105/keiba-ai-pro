"""GPU/CPU usage monitor helpers for training loops."""

from __future__ import annotations

import threading
from typing import Callable


class GPUMonitor:
    """Background monitor that samples system usage periodically."""

    def __init__(self, get_snapshot: Callable[[], dict], on_snapshot: Callable[[str, dict], None], interval_sec: float = 1.0) -> None:
        self._get_snapshot = get_snapshot
        self._on_snapshot = on_snapshot
        self._interval_sec = float(interval_sec)
        self._stop_event = threading.Event()
        self._phase = "prepare"
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            snap = self._get_snapshot()
            self._on_snapshot(self._phase, snap)
            self._stop_event.wait(self._interval_sec)

    def set_phase(self, phase: str) -> None:
        self._phase = str(phase)

    def start(self) -> None:
        self._thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._phase = "finalize"
        self._stop_event.set()
        self._thread.join(timeout=float(join_timeout))
