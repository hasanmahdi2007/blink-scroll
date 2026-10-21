from __future__ import annotations

import time

import pytest

from blinkscroll.actions import Scroller
from blinkscroll.config import CalibrationConfig, Config, ScrollConfig
from blinkscroll.eyes import Calibrated, EyeTracker, Thresholds
from blinkscroll.gestures import GestureEngine, HoldEnded, Trigger

from .synth import CLOSED, OPEN, Seq


def engine(hold: bool = True, swap: bool = False) -> GestureEngine:
    cfg = Config()
    cfg.gestures.double_blink.enabled = False
    cfg.gestures.long_blink.enabled = True
    cfg.gestures.wink.enabled = True
    cfg.gestures.wink.left_direction = "down"
    cfg.gestures.wink.right_direction = "up"
    cfg.scroll.hold_to_scroll = hold
    cfg.detection.swap_eyes = swap
    e = GestureEngine(cfg, expected_fps=20)
    e.set_thresholds(Thresholds.compute(OPEN, CLOSED, CalibrationConfig()))
    return e


def events(e: GestureEngine, seq: Seq) -> list:
    out = []
    for s in seq.samples:
        out += [ev for ev in e.update(s)[1] if isinstance(ev, (Trigger, HoldEnded))]
    return out


def test_wink_hold_scrolls_until_released() -> None:
    evs = events(engine(), Seq().open(1).wink("left", 2.0).open(1))
    assert [type(ev).__name__ for ev in evs] == ["Trigger", "HoldEnded"]
    start, end = evs
    assert (start.gesture, start.direction, start.hold) == ("wink_left", "down", True)
    assert end.duration == pytest.approx(2.0 - 0.25, abs=0.15)  # held time after recognition


def test_right_wink_holds_scroll_up() -> None:
    evs = events(engine(), Seq().open(1).wink("right", 1.0).open(1))
    assert (evs[0].gesture, evs[0].direction) == ("wink_right", "up")
    assert isinstance(evs[1], HoldEnded)


def test_long_blink_hold_ends_when_eyes_open() -> None:
    evs = events(engine(), Seq().open(1).closed(3.0).open(1))
    assert [type(ev).__name__ for ev in evs] == ["Trigger", "HoldEnded"]
    assert evs[1].duration == pytest.approx(3.0 - 0.6, abs=0.15)


def test_brief_dips_do_not_interrupt_a_held_wink() -> None:
    """Real log: holds ended after 0.1-0.4 s because of one-frame dips in the score."""
    seq = Seq().open(1).wink("left", 0.6)
    for _ in range(3):  # three single-frame dips to "open" during a 2 s hold
        seq.frame(*OPEN).wink("left", 0.4)
    seq.open(1)
    evs = events(engine(), seq)
    assert [type(ev).__name__ for ev in evs] == ["Trigger", "HoldEnded"]
    assert evs[1].duration > 1.5


def test_long_blink_hold_continues_while_partly_closed() -> None:
    partly = tuple(o + 0.6 * (c - o) for o, c in zip(OPEN, CLOSED))  # 60% closed
    seq = Seq().open(1).closed(1.0)
    for _ in range(30):
        seq.frame(*partly)
    seq.open(1)
    evs = events(engine(), seq)
    assert [type(ev).__name__ for ev in evs] == ["Trigger", "HoldEnded"]
    assert evs[1].duration > 1.5


def test_wink_again_soon_after_release() -> None:
    seq = Seq().open(1).wink("left", 1.0).open(0.6).wink("left", 1.0).open(1)
    assert [type(ev).__name__ for ev in events(engine(), seq)] == [
        "Trigger", "HoldEnded", "Trigger", "HoldEnded"]


def test_face_lost_ends_hold() -> None:
    evs = events(engine(), Seq().open(1).wink("left", 1.0).no_face(1.0).open(1))
    assert isinstance(evs[-1], HoldEnded)


def test_nothing_else_fires_while_holding_and_cooldown_after() -> None:
    seq = Seq().open(1).wink("left", 1.0).open(0.3).wink("left", 1.0).open(1)
    evs = events(engine(), seq)
    # the 2nd wink starts 0.3 s after release: inside the 1 s cooldown -> ignored
    assert [type(ev).__name__ for ev in evs] == ["Trigger", "HoldEnded"]


def test_hold_disabled_gives_one_shot_trigger() -> None:
    evs = events(engine(hold=False), Seq().open(1).wink("left", 2.0).open(1))
    assert len(evs) == 1 and not evs[0].hold


def test_swap_eyes() -> None:
    evs = events(engine(swap=True), Seq().open(1).wink("left", 1.0).open(1))
    assert evs[0].gesture == "wink_right"


def test_calibration_restarts_if_eyes_closed_in_open_phase() -> None:
    """Real recording: eyes were closed for most of the 'keep eyes open' phase."""
    tracker = EyeTracker(Config(), expected_fps=20)
    seq = Seq().open(0.3).closed(2.5).open(0.3)  # 3.1 s, mostly closed
    for s in seq.samples:
        evs = tracker.update(s)[1]
        assert not any(isinstance(ev, Calibrated) for ev in evs)
    assert tracker.calibrating


class _FakeMouse:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def scroll(self, dx: float, dy: float) -> None:
        self.calls.append(dy)


def test_scroller_hold_speed_ramps_and_stops() -> None:
    cfg = ScrollConfig(hold_start_notches_per_s=5, hold_max_notches_per_s=20, hold_ramp_s=1.0)
    s = Scroller(cfg)
    s._mouse = _FakeMouse()  # type: ignore[assignment]
    s.start_hold("down")
    time.sleep(1.5)
    s.stop_hold()
    time.sleep(0.2)
    n = len(s._mouse.calls)  # type: ignore[attr-defined]
    time.sleep(0.3)
    assert len(s._mouse.calls) == n  # type: ignore[attr-defined]  # really stopped
    # ~5/s ramping to 20/s over 1 s, then 0.5 s at 20/s: roughly 12 + 10 = 22 notches
    assert 12 <= n <= 32
    assert all(dy < 0 for dy in s._mouse.calls)  # type: ignore[attr-defined]
    s.close()


def test_scroller_safety_limit() -> None:
    cfg = ScrollConfig(hold_start_notches_per_s=20, hold_max_notches_per_s=20, max_hold_s=0.3)
    s = Scroller(cfg)
    s._mouse = _FakeMouse()  # type: ignore[assignment]
    s.start_hold("up")
    time.sleep(0.8)
    assert len(s._mouse.calls) <= 9  # type: ignore[attr-defined]
    s.stop_hold()
    s.scroll("down")  # still works after the safety stop
    time.sleep(0.4)
    assert s._mouse.calls[-1] < 0  # type: ignore[attr-defined]
    s.close()
