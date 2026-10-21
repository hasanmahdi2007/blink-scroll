"""Gesture engine (pure logic, no camera or OS code, so it is fully unit-testable).

Sample -> EyeTracker (smoothing, calibration, hysteresis, blinks) -> this state machine:

* gate     : gestures only count while facing the screen (head pose relative to the neutral pose
             measured during calibration, and gaze not looking down at the keyboard). The gate is
             evaluated on frames with the eyes open and frozen while they are closed, because pose
             and gaze estimates are unreliable with closed lids.
* double   : two full blinks, the 2nd ending within ``window_s`` of the 1st starting.
* long     : both eyes closed for ``min_duration_s`` (fires while still closed).
* wink     : one eye closed, the other clearly open, for ``min_duration_s``.
* cooldown : after any trigger, everything that starts within ``cooldown_s`` is ignored.
* baseline : the open-eye baseline adapts slowly to lighting changes.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from .config import Config
from .eyes import Blink, Calibrated, Event, EyeFrame, EyeTracker, Pair, Phase, Thresholds
from .samples import Sample

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trigger:
    t: float
    gesture: str  # "double_blink", "long_blink", "wink_left", "wink_right"
    direction: str  # "up" or "down"


@dataclass(frozen=True)
class GateChanged:
    reason: str  # "" when gestures are allowed again


EngineEvent = Event | Trigger | GateChanged


class GestureEngine:
    ADAPT_EVERY = 10  # frames between threshold refreshes during baseline adaptation
    MAX_DRIFT = 0.1  # the baseline may drift at most this far from the calibrated value

    def __init__(self, config: Config, expected_fps: float | None = None) -> None:
        self.cfg = config
        self.tracker = EyeTracker(config, expected_fps)
        self.neutral: tuple[float, float, float] = (0.0, 0.0, 0.0)  # yaw, pitch, roll
        self._pose_samples: list[tuple[float, float, float]] = []
        self.gate_reason = "calibrating"
        self.trigger_count = 0
        self._cooldown_until = float("-inf")
        self._calibrated_open: Pair | None = None
        self._adapt_open: list[float] | None = None
        self._adapt_frames = 0
        self._clear()

    # ------------------------------------------------------------------ control

    def recalibrate(self) -> None:
        self._pose_samples.clear()
        self.tracker.recalibrate()
        self._clear()

    def set_thresholds(self, thresholds: Thresholds,
                       neutral: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.tracker.set_thresholds(thresholds)
        self.neutral = neutral
        self._on_calibrated(thresholds)

    def apply_config(self, config: Config) -> None:
        self.cfg = config
        self.tracker.apply_config(config)
        if self.tracker.thresholds is not None:
            self._on_calibrated(self.tracker.thresholds)

    @property
    def thresholds(self) -> Thresholds | None:
        return self.tracker.thresholds

    @property
    def blink_count(self) -> int:
        return self.tracker.blink_count

    # ------------------------------------------------------------------ per frame

    def update(self, s: Sample) -> tuple[EyeFrame, list[EngineEvent]]:
        frame, eye_events = self.tracker.update(s)
        out: list[EngineEvent] = list(eye_events)

        cal = self.tracker.calibrator
        if cal is not None and cal.phase is Phase.OPEN and s.face:
            self._pose_samples.append((s.yaw, s.pitch, s.roll))
        for ev in eye_events:
            if isinstance(ev, Calibrated):
                if self._pose_samples:
                    self.neutral = tuple(  # type: ignore[assignment]
                        statistics.median(p[i] for p in self._pose_samples) for i in range(3))
                    self._pose_samples.clear()
                log.info("Neutral head pose: yaw %+.1f pitch %+.1f roll %+.1f", *self.neutral)
                self._on_calibrated(ev.thresholds)

        if not frame.calibrated:
            return frame, out
        if not s.face:
            self._clear()
            self._set_gate("no face", out)
            return frame, out

        # Blinks are judged with the gate as it was while the eyes were still open.
        gate_ok = self.gate_reason == ""
        for ev in eye_events:
            if isinstance(ev, Blink) and gate_ok and ev.start >= self._cooldown_until:
                self._on_blink(ev, s.t, out)

        both_open = not frame.closed[0] and not frame.closed[1]
        if both_open:
            self._set_gate(self._check_gate(s), out)
            gate_ok = self.gate_reason == ""
            if gate_ok:
                self._adapt_baseline(frame)

        if not gate_ok:
            self._clear()
            return frame, out
        if s.t >= self._cooldown_until:
            self._check_long_blink(s.t, out)
            self._check_wink(frame, out)
        if self._pending is not None and s.t - self._pending.start > self.cfg.gestures.double_blink.window_s:
            self._pending = None
        return frame, out

    # ------------------------------------------------------------------ gestures

    def _on_blink(self, ev: Blink, now: float, out: list[EngineEvent]) -> None:
        if self._long_fired_at is not None and ev.start <= self._long_fired_at:
            return  # the release of a long blink that already fired
        g = self.cfg.gestures.double_blink
        if not g.enabled:
            return
        if ev.duration > g.max_blink_s or not self._full_closure(ev, g.min_closure):
            log.debug("Blink ignored for double-blink (%.0f ms, peak L%.2f R%.2f)",
                      ev.duration * 1000, *ev.peak)
            self._pending = None
            return
        if self._pending is not None and ev.end - self._pending.start <= g.window_s:
            self._pending = None
            self._fire(now, "double_blink", g.direction, out)
        else:
            self._pending = ev

    def _check_long_blink(self, t: float, out: list[EngineEvent]) -> None:
        g = self.cfg.gestures.long_blink
        since = self.tracker.both_closed_since
        if not g.enabled or since is None or since < self._cooldown_until:
            return
        if self._long_fired_at is None or self._long_fired_at < since:
            if t - since >= g.min_duration_s:
                self._long_fired_at = t
                self._pending = None
                self._fire(t, "long_blink", g.direction, out)

    def _check_wink(self, frame: EyeFrame, out: list[EngineEvent]) -> None:
        g = self.cfg.gestures.wink
        th = self.tracker.thresholds
        if not g.enabled or th is None:
            return
        left, right = frame.closed
        if left == right:  # both open, or both closed (a blink, not a wink)
            self._wink_eye = None
            self._wink_fired = False
            return
        eye = 0 if left else 1
        other = 1 - eye
        if frame.smooth[other] > th.open_level[other] + g.open_eye_margin:
            self._wink_eye = None  # the other eye is squinting too: not a clear wink
            return
        if self._wink_eye != eye:
            self._wink_eye, self._wink_since, self._wink_fired = eye, frame.t, False
        elif not self._wink_fired and frame.t - self._wink_since >= g.min_duration_s:
            self._wink_fired = True
            self._pending = None
            name, direction = (("wink_left", g.left_direction) if eye == 0
                               else ("wink_right", g.right_direction))
            self._fire(frame.t, name, direction, out)

    def _fire(self, t: float, gesture: str, direction: str, out: list[EngineEvent]) -> None:
        self.trigger_count += 1
        self._cooldown_until = t + self.cfg.detection.cooldown_s
        out.append(Trigger(t, gesture, direction))
        log.debug("TRIGGER #%d %s -> scroll %s", self.trigger_count, gesture, direction)

    def _full_closure(self, ev: Blink, fraction: float) -> bool:
        th = self.tracker.thresholds
        assert th is not None
        return all(ev.peak[i] >= th.open_level[i] + fraction * (th.closed_level[i] - th.open_level[i])
                   for i in range(2))

    def _clear(self) -> None:
        self._pending: Blink | None = None
        self._long_fired_at: float | None = None
        self._wink_eye: int | None = None
        self._wink_since = 0.0
        self._wink_fired = False

    # ------------------------------------------------------------------ gating

    def _check_gate(self, s: Sample) -> str:
        hp = self.cfg.head_pose
        if not hp.enabled:
            return ""
        yaw, pitch, roll = (s.yaw - self.neutral[0], s.pitch - self.neutral[1],
                            s.roll - self.neutral[2])
        if abs(yaw) > hp.max_yaw_deg:
            return "head turned"
        if abs(pitch) > hp.max_pitch_deg:
            return "head tilted up/down"
        if abs(roll) > hp.max_roll_deg:
            return "head tilted sideways"
        if s.look_down > hp.max_look_down:
            return "looking down"
        return ""

    def _set_gate(self, reason: str, out: list[EngineEvent]) -> None:
        if reason != self.gate_reason:
            self.gate_reason = reason
            out.append(GateChanged(reason))
            log.debug("Gate: %s", reason or "facing screen")

    # ------------------------------------------------------------------ baseline

    def _on_calibrated(self, th: Thresholds) -> None:
        self._calibrated_open = th.open_level
        self._adapt_open = list(th.open_level)
        self._adapt_frames = 0
        self.gate_reason = ""
        self._clear()

    def _adapt_baseline(self, frame: EyeFrame) -> None:
        rate = self.cfg.calibration.adapt_rate
        th = self.tracker.thresholds
        if rate <= 0 or th is None or self._adapt_open is None or self._calibrated_open is None:
            return
        if not all(frame.smooth[i] < th.open_thr[i] for i in range(2)):
            return
        for i in range(2):
            v = self._adapt_open[i] + rate * (frame.smooth[i] - self._adapt_open[i])
            lo = self._calibrated_open[i] - self.MAX_DRIFT
            hi = min(self._calibrated_open[i] + self.MAX_DRIFT,
                     th.closed_level[i] - self.cfg.calibration.min_contrast)
            self._adapt_open[i] = min(max(v, lo, 0.0), hi)
        self._adapt_frames += 1
        if self._adapt_frames % self.ADAPT_EVERY == 0:
            self.tracker.thresholds = th.with_open_level(
                (self._adapt_open[0], self._adapt_open[1]), self.cfg.calibration)
