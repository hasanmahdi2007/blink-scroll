"""Eye-signal processing (pure logic, no camera or OS code).

Sample -> per-eye EMA smoothing -> calibrated per-eye thresholds -> hysteresis -> Blink events.

Calibration has two phases because blink scores vary a lot between people (glasses can cap a
fully closed eye at ~0.5): first the eyes stay open (baseline), then closed (closed level). Each
eye's thresholds sit a fixed fraction of the way between its own open and closed levels.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum

from .config import CalibrationConfig, Config, DetectionConfig
from .samples import Sample

log = logging.getLogger(__name__)

Pair = tuple[float, float]  # (left, right)


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class CalibrationPhase:
    phase: "Phase"
    message: str


@dataclass(frozen=True)
class Calibrated:
    thresholds: "Thresholds"


@dataclass(frozen=True)
class Blink:
    start: float  # first frame with both eyes closed
    end: float  # first frame with both eyes open again
    peak: Pair  # highest smoothed score per eye during the blink

    @property
    def duration(self) -> float:
        return self.end - self.start


Event = CalibrationPhase | Calibrated | Blink


# --------------------------------------------------------------------------- smoothing


class Ema:
    """Exponential moving average with a time constant, so it behaves the same at any fps."""

    def __init__(self, tau_s: float) -> None:
        self.tau = tau_s
        self.value: float | None = None
        self._t: float | None = None

    def update(self, x: float, t: float) -> float:
        if self.value is None or self._t is None or self.tau <= 0:
            self.value = x
        else:
            dt = max(t - self._t, 0.0)
            alpha = 1.0 - math.exp(-dt / self.tau)
            self.value += alpha * (x - self.value)
        self._t = t
        return self.value

    def reset(self) -> None:
        self.value = None
        self._t = None


# --------------------------------------------------------------------------- thresholds


@dataclass(frozen=True)
class Thresholds:
    open_level: Pair  # eyes-open baseline
    closed_level: Pair  # eyes-closed level (estimated when the closed phase was skipped)
    closed_thr: Pair  # score above this = eye closed
    open_thr: Pair  # score below this = eye open again (hysteresis)
    method: str  # "two-phase" or "offset" per eye, e.g. "two-phase/offset"

    @staticmethod
    def compute(open_level: Pair, closed_level: Pair | None, cfg: CalibrationConfig) -> Thresholds:
        closed_thr: list[float] = []
        open_thr: list[float] = []
        levels: list[float] = []
        methods: list[str] = []
        for i in range(2):
            base = open_level[i]
            closed = closed_level[i] if closed_level is not None else None
            if closed is not None and closed - base >= cfg.min_contrast:
                span = closed - base
                closed_thr.append(base + cfg.closed_fraction * span)
                open_thr.append(base + cfg.open_fraction * span)
                levels.append(closed)
                methods.append("two-phase")
            else:
                closed_thr.append(base + cfg.closed_offset)
                open_thr.append(base + cfg.open_offset)
                levels.append(base + cfg.closed_offset / max(cfg.closed_fraction, 1e-6))
                methods.append("offset")
        return Thresholds(
            open_level=(open_level[0], open_level[1]),
            closed_level=(levels[0], levels[1]),
            closed_thr=(closed_thr[0], closed_thr[1]),
            open_thr=(open_thr[0], open_thr[1]),
            method="/".join(dict.fromkeys(methods)),
        )

    def with_open_level(self, open_level: Pair, cfg: CalibrationConfig) -> Thresholds:
        """Same closed levels, new baseline (used by slow baseline adaptation)."""
        new = Thresholds.compute(open_level, self.closed_level, cfg)
        return Thresholds(new.open_level, self.closed_level, new.closed_thr, new.open_thr,
                          self.method)


# --------------------------------------------------------------------------- calibration


class Phase(Enum):
    OPEN = "open"  # keep eyes open, look at the screen
    CLOSE_LEADIN = "close-leadin"  # beep: close your eyes (reaction time, not measured)
    CLOSED = "closed"  # measuring the closed level
    DONE = "done"


@dataclass
class Calibrator:
    cfg: CalibrationConfig
    expected_fps: float
    phase: Phase = Phase.OPEN
    _phase_start: float | None = None
    _open: list[Pair] = field(default_factory=list)
    _closed: list[Pair] = field(default_factory=list)
    _open_level: Pair | None = None

    LEADIN_S = 0.6
    MIN_COVERAGE = 0.5  # need face samples for at least this share of the phase

    def start_message(self) -> CalibrationPhase:
        return CalibrationPhase(Phase.OPEN, "Calibrating: look at the screen with eyes open")

    def update(self, t: float, face: bool, smooth: Pair) -> list[Event]:
        if self.phase is Phase.DONE:
            return []
        if self._phase_start is None:
            self._phase_start = t
        elapsed = t - self._phase_start
        cfg = self.cfg

        if self.phase is Phase.OPEN:
            if not face and not self._open:
                self._phase_start = t  # wait (quietly) for a face before starting the clock
                return []
            if face:
                self._open.append(smooth)
            if elapsed >= cfg.open_seconds:
                if not self._enough(self._open, cfg.open_seconds):
                    return self._restart(t, "no face during calibration, retrying")
                self._open_level = _median_pair(self._open)
                if not cfg.use_closed_phase:
                    return self._finish(None)
                return self._enter(Phase.CLOSE_LEADIN, t, "Now close your eyes until the 2nd beep")
        elif self.phase is Phase.CLOSE_LEADIN:
            if elapsed >= self.LEADIN_S:
                return self._enter(Phase.CLOSED, t, "Keep eyes closed...")
        elif self.phase is Phase.CLOSED:
            if face:
                self._closed.append(smooth)
            if elapsed >= cfg.closed_seconds:
                closed = _median_pair(self._closed) if self._enough(
                    self._closed, cfg.closed_seconds) else None
                return self._finish(closed)
        return []

    def _enough(self, samples: list[Pair], seconds: float) -> bool:
        return len(samples) >= max(3, self.MIN_COVERAGE * seconds * self.expected_fps)

    def _enter(self, phase: Phase, t: float, message: str) -> list[Event]:
        self.phase = phase
        self._phase_start = t
        return [CalibrationPhase(phase, message)]

    def _restart(self, t: float, reason: str) -> list[Event]:
        log.warning("Calibration: %s", reason)
        self._open.clear()
        self._closed.clear()
        return self._enter(Phase.OPEN, t, "Calibrating: look at the screen with eyes open")

    def _finish(self, closed: Pair | None) -> list[Event]:
        assert self._open_level is not None
        th = Thresholds.compute(self._open_level, closed, self.cfg)
        self.phase = Phase.DONE
        return [CalibrationPhase(Phase.DONE, "Calibration done"), Calibrated(th)]


def _median_pair(samples: list[Pair]) -> Pair:
    return (statistics.median(s[0] for s in samples), statistics.median(s[1] for s in samples))


# --------------------------------------------------------------------------- tracker


@dataclass(frozen=True)
class EyeFrame:
    """Per-frame output for the gesture layer and the debug view."""

    t: float
    face: bool
    raw: Pair
    smooth: Pair
    closed: tuple[bool, bool]  # hysteresis state per eye
    calibrated: bool


class EyeTracker:
    """Smoothing + calibration + per-eye hysteresis + both-eye blink detection."""

    def __init__(self, config: Config, expected_fps: float | None = None) -> None:
        self._cfg = config
        self._fps = expected_fps or config.processing.fps
        self._ema = (Ema(config.detection.smoothing_tau_s), Ema(config.detection.smoothing_tau_s))
        self.thresholds: Thresholds | None = None
        self.calibrator: Calibrator | None = None
        self._pending: list[Event] = []
        self.blink_count = 0
        self._reset_eyes()
        self.recalibrate()

    # ----- control

    def recalibrate(self) -> None:
        self.calibrator = Calibrator(self._cfg.calibration, self._fps)
        self._pending.append(self.calibrator.start_message())
        self._reset_eyes()

    def set_thresholds(self, thresholds: Thresholds) -> None:
        """Skip calibration (tests, or a saved calibration)."""
        self.thresholds = thresholds
        self.calibrator = None
        self._reset_eyes()

    def apply_config(self, config: Config) -> None:
        self._cfg = config
        for e in self._ema:
            e.tau = config.detection.smoothing_tau_s
        if self.thresholds is not None and self.thresholds.method != "manual":
            # Keep the measured levels, re-derive thresholds with the new fractions/offsets.
            self.thresholds = Thresholds.compute(
                self.thresholds.open_level,
                self.thresholds.closed_level if "two-phase" in self.thresholds.method else None,
                config.calibration,
            )

    @property
    def calibrating(self) -> bool:
        return self.calibrator is not None

    # ----- per frame

    def update(self, s: Sample) -> tuple[EyeFrame, list[Event]]:
        events, self._pending = self._pending, []
        if not s.face:
            # Face lost: forget any closure in progress and the filter history.
            for e in self._ema:
                e.reset()
            self._reset_eyes()
            smooth: Pair = (0.0, 0.0)
        else:
            smooth = (self._ema[0].update(s.left, s.t), self._ema[1].update(s.right, s.t))

        if self.calibrator is not None:
            cal_events = self.calibrator.update(s.t, s.face, smooth)
            events += cal_events
            for ev in cal_events:
                if isinstance(ev, Calibrated):
                    self.thresholds = ev.thresholds
                    self.calibrator = None
                    self._reset_eyes()
                    log.info("Calibrated (%s): open L%.2f R%.2f, closed L%.2f R%.2f -> "
                             "closed_thr L%.2f R%.2f, open_thr L%.2f R%.2f",
                             ev.thresholds.method, *ev.thresholds.open_level,
                             *ev.thresholds.closed_level, *ev.thresholds.closed_thr,
                             *ev.thresholds.open_thr)
        elif s.face and self.thresholds is not None:
            events += self._detect(s.t, smooth)

        frame = EyeFrame(t=s.t, face=s.face, raw=(s.left, s.right), smooth=smooth,
                         closed=(self._closed[0], self._closed[1]),
                         calibrated=self.thresholds is not None and self.calibrator is None)
        return frame, events

    def _reset_eyes(self) -> None:
        self._closed = [False, False]
        self._armed = False  # both eyes must be seen open before a blink can start
        self._both_closed_since: float | None = None
        self._both_closed_frames = 0
        self._peak = [0.0, 0.0]

    def _detect(self, t: float, smooth: Pair) -> list[Event]:
        th = self.thresholds
        assert th is not None
        for i in range(2):
            if self._closed[i]:
                if smooth[i] < th.open_thr[i]:
                    self._closed[i] = False
            elif smooth[i] > th.closed_thr[i]:
                self._closed[i] = True

        if not self._armed:
            if not self._closed[0] and not self._closed[1]:
                self._armed = True
            return []

        events: list[Event] = []
        if self._closed[0] and self._closed[1]:
            if self._both_closed_since is None:
                self._both_closed_since = t
                self._both_closed_frames = 0
                self._peak = [0.0, 0.0]
            # Only frames genuinely above the closed threshold count toward the minimum, not
            # frames merely held "closed" by hysteresis (otherwise a 1-frame spike would pass).
            if smooth[0] > th.closed_thr[0] and smooth[1] > th.closed_thr[1]:
                self._both_closed_frames += 1
            self._peak = [max(self._peak[0], smooth[0]), max(self._peak[1], smooth[1])]
        elif self._both_closed_since is not None and not self._closed[0] and not self._closed[1]:
            if self._both_closed_frames >= self._cfg.detection.min_closed_frames:
                self.blink_count += 1
                events.append(Blink(self._both_closed_since, t, (self._peak[0], self._peak[1])))
            self._both_closed_since = None
        return events

    @property
    def both_closed_since(self) -> float | None:
        """Start time of the current both-eyes closure, if any (for long-blink detection)."""
        return self._both_closed_since
