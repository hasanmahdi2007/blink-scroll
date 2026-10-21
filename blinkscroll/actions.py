"""Smooth scrolling: one trigger becomes several small wheel events spread over ~150 ms.

Wheel events go to the window under the mouse cursor (normal Windows behaviour). pynput sends
``dy * WHEEL_DELTA`` per event, so fractional notches (steps > amount) give finer steps in apps
that support high-resolution wheels (browsers, most modern apps). Some older apps ignore
sub-notch deltas, so the default is whole notches (steps == amount).
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from pynput.mouse import Controller

from .config import ScrollConfig

log = logging.getLogger(__name__)


class Scroller:
    def __init__(self, config: ScrollConfig) -> None:
        self.config = config
        self._mouse = Controller()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="scroller", daemon=True)
        self._thread.start()

    def scroll(self, direction: str) -> None:
        """Non-blocking: queue one smooth scroll ("up" or "down")."""
        self._queue.put(direction)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while (direction := self._queue.get()) is not None:
            try:
                self._smooth_scroll(direction)
            except Exception:
                log.exception("Scroll failed")

    def _smooth_scroll(self, direction: str) -> None:
        cfg = self.config
        steps = max(1, cfg.steps)
        sign = 1.0 if direction == "up" else -1.0  # Windows: positive wheel delta scrolls up
        # Distribute whole WHEEL_DELTA units (1/120 notch) so the total is exact.
        total_units = round(abs(cfg.amount) * 120)
        sent = 0
        interval = cfg.duration_s / steps
        for i in range(1, steps + 1):
            units = round(total_units * i / steps) - sent
            sent += units
            if units:
                # pynput truncates int(dy * 120); the +0.25 keeps float error from losing a unit.
                self._mouse.scroll(0, sign * (units + 0.25) / 120)
            if i < steps:
                time.sleep(interval)
        log.debug("Scrolled %s %.2f notches in %d steps", direction, cfg.amount, steps)
