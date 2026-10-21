from __future__ import annotations

import pytest

from blinkscroll.config import CalibrationConfig, Config
from blinkscroll.eyes import Blink, Calibrated, Ema, EyeTracker, Thresholds

from .synth import CLOSED, OPEN, Seq


def run(tracker: EyeTracker, seq: Seq) -> list:
    events = []
    for s in seq.samples:
        events += tracker.update(s)[1]
    return events


def calibrated_tracker(config: Config | None = None) -> EyeTracker:
    tracker = EyeTracker(config or Config(), expected_fps=20)
    tracker.set_thresholds(Thresholds.compute(OPEN, CLOSED, CalibrationConfig()))
    return tracker


def blinks(events: list) -> list[Blink]:
    return [e for e in events if isinstance(e, Blink)]


def test_ema_is_frame_rate_independent() -> None:
    a, b = Ema(0.05), Ema(0.05)
    a.update(0.0, 0.0)
    b.update(0.0, 0.0)
    for i in range(1, 11):  # 100 fps
        a.update(1.0, i * 0.01)
    for i in range(1, 3):  # 20 fps
        b.update(1.0, i * 0.05)
    assert a.value == pytest.approx(b.value, abs=0.02)


def test_two_phase_calibration_sets_per_eye_thresholds() -> None:
    tracker = EyeTracker(Config(), expected_fps=20)
    events = run(tracker, Seq().calibration())
    cal = [e for e in events if isinstance(e, Calibrated)]
    assert len(cal) == 1
    th = cal[0].thresholds
    assert th.method == "two-phase"
    assert th.open_level == pytest.approx(OPEN, abs=0.01)
    assert th.closed_level == pytest.approx(CLOSED, abs=0.02)
    # 60% / 30% of the way from open to closed, per eye
    assert th.closed_thr[0] == pytest.approx(0.04 + 0.6 * 0.52, abs=0.02)
    assert th.closed_thr[1] == pytest.approx(0.04 + 0.6 * 0.41, abs=0.02)
    assert all(o < c for o, c in zip(th.open_thr, th.closed_thr))
    # Reopening the eyes after the calibration's closed phase is not a blink.
    assert blinks(events) == []


def test_calibration_falls_back_to_offsets_if_eyes_not_closed() -> None:
    tracker = EyeTracker(Config(), expected_fps=20)
    events = run(tracker, Seq().open(6.0))
    th = next(e for e in events if isinstance(e, Calibrated)).thresholds
    assert th.method == "offset"
    assert th.closed_thr == pytest.approx((0.04 + 0.25, 0.04 + 0.25), abs=0.01)


def test_calibration_waits_for_face() -> None:
    tracker = EyeTracker(Config(), expected_fps=20)
    events = run(tracker, Seq().no_face(10))
    assert not any(isinstance(e, Calibrated) for e in events)
    assert tracker.calibrating
    # ...and calibrates normally once the user sits down
    events = run(tracker, Seq(t=10).calibration())
    assert any(isinstance(e, Calibrated) for e in events)


def test_calibration_restarts_if_face_mostly_missing() -> None:
    tracker = EyeTracker(Config(), expected_fps=20)
    seq = Seq().open(0.3).no_face(3.0)  # face seen briefly, then gone for the rest
    events = run(tracker, seq)
    assert not any(isinstance(e, Calibrated) for e in events)
    assert tracker.calibrating


def test_natural_blink_counted_once() -> None:
    tracker = calibrated_tracker()
    events = run(tracker, Seq().open(1).blink(0.15).open(1))
    assert len(blinks(events)) == 1
    assert tracker.blink_count == 1


def test_single_frame_spike_is_ignored() -> None:
    tracker = calibrated_tracker()
    events = run(tracker, Seq().open(1).frame(0.9, 0.9).open(1))
    assert blinks(events) == []


def test_one_eye_only_is_not_a_blink() -> None:
    tracker = calibrated_tracker()
    events = run(tracker, Seq().open(1).wink("left").open(1))
    assert blinks(events) == []


def test_face_lost_mid_blink_drops_it() -> None:
    tracker = calibrated_tracker()
    seq = Seq().open(1).closed(0.2).no_face(0.5).open(1)
    assert blinks(run(tracker, seq)) == []


def test_hysteresis_holds_between_thresholds() -> None:
    tracker = calibrated_tracker()
    th = tracker.thresholds
    assert th is not None
    between = tuple((o + c) / 2 for o, c in zip(th.open_thr, th.closed_thr))
    seq = Seq().open(1).closed(0.2)
    for _ in range(5):  # hovering between thresholds keeps the eyes "closed"
        seq.frame(*between)
    seq.open(1)
    events = run(tracker, seq)
    assert len(blinks(events)) == 1
    assert blinks(events)[0].duration > 0.4
