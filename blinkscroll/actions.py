"""Scrolling: a smooth one-shot scroll per trigger, or continuous scrolling while a gesture is held.

Wheel events go to the window under the mouse cursor (normal Windows behaviour). pynput sends
``dy * WHEEL_DELTA`` per event, so fractional notches (steps > amount) give finer steps in apps
that support high-resolution wheels (browsers, most modern apps). Some older apps ignore
sub-notch deltas, so the defaults use whole notches.

Hold mode: the scroll speed starts at ``hold_start_notches_per_s`` and ramps up to
``hold_max_notches_per_s`` over ``hold_ramp_s``, so a short hold moves a little and a long hold
moves a lot. It stops immediately on release and after ``max_hold_s`` at the latest.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from pynput.mouse import Controller

from .config import ScrollConfig

log = logging.getLogger(__name__)

_TAP, _HOLD, _STOP = "tap", "hold", "stop"


class Scroller:
    def __init__(self, config: ScrollConfig) -> None:
        self.config = config
        self._mouse = Controller()
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="scroller", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ public (non-blocking)

    def scroll(self, direction: str) -> None:
        """One smooth scroll of ``amount`` notches ("up" or "down")."""
        self._queue.put((_TAP, direction))

    def start_hold(self, direction: str) -> None:
        """Scroll continuously until :meth:`stop_hold`."""
        self._queue.put((_HOLD, direction))

    def stop_hold(self) -> None:
        self._queue.put((_STOP, ""))

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=1.0)

    # ------------------------------------------------------------------ worker

    def _run(self) -> None:
        pending: tuple[str, str] | None = None
        while True:
            cmd = pending if pending is not None else self._queue.get()
            pending = None
            if cmd is None:
                return
            kind, direction = cmd
            try:
                if kind == _TAP:
                    self._smooth_scroll(direction)
                elif kind == _HOLD:
                    pending = self._hold(direction)
                    if pending is not None and pending[0] == _STOP:
                        pending = None
            except Exception:
                log.exception("Scroll failed")

    def _wheel(self, sign: float, units: int) -> None:
        if units:
            # pynput truncates int(dy * 120); the +0.25 keeps float error from losing a unit.
            self._mouse.scroll(0, sign * (units + 0.25) / 120)

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
            self._wheel(sign, units)
            if i < steps:
                time.sleep(interval)
        log.debug("Scrolled %s %.2f notches in %d steps", direction, cfg.amount, steps)

    def _hold(self, direction: str) -> tuple[str, str] | None:
        """Scroll until a new command arrives; returns that command (None = shutdown)."""
        cfg = self.config
        sign = 1.0 if direction == "up" else -1.0
        start = time.monotonic()
        notches = 0
        log.debug("Hold-scroll %s started", direction)
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= cfg.max_hold_s:
                log.info("Hold-scroll stopped after the %.0f s safety limit", cfg.max_hold_s)
                return self._wait_for_command()
            ramp = min(elapsed / cfg.hold_ramp_s, 1.0) if cfg.hold_ramp_s > 0 else 1.0
            speed = cfg.hold_start_notches_per_s + ramp * (
                cfg.hold_max_notches_per_s - cfg.hold_start_notches_per_s)
            self._wheel(sign, 120)
            notches += 1
            try:
                cmd = self._queue.get(timeout=1.0 / max(speed, 0.5))
            except queue.Empty:
                continue
            log.debug("Hold-scroll %s: %d notches in %.2f s", direction, notches, elapsed)
            return cmd

    def _wait_for_command(self) -> tuple[str, str] | None:
        """After the safety stop, idle until the next command (normally this hold's stop)."""
        return self._queue.get()
