from __future__ import annotations

import dataclasses

import pytest

from blinkscroll.config import CalibrationConfig, Config
from blinkscroll.eyes import Thresholds
from blinkscroll.gestures import GestureEngine, Trigger

from .synth import CLOSED, OPEN, Seq


def engine(**gestures: bool) -> GestureEngine:
    cfg = Config()
    g = cfg.gestures
    g.double_blink.enabled = gestures.get("double", True)
    g.long_blink.enabled = gestures.get("long", False)
    g.wink.enabled = gestures.get("wink", False)
    e = GestureEngine(cfg, expected_fps=20)
    e.set_thresholds(Thresholds.compute(OPEN, CLOSED, CalibrationConfig()))
    return e


def triggers(e: GestureEngine, seq: Seq) -> list[Trigger]:
    out: list[Trigger] = []
    for s in seq.samples:
        out += [ev for ev in e.update(s)[1] if isinstance(ev, Trigger)]
    return out


# ----------------------------------------------------------------- natural blinking


def test_one_natural_blink_does_not_trigger() -> None:
    assert triggers(engine(), Seq().open(1).blink(0.15).open(2)) == []


def test_two_natural_blinks_1_5s_apart_do_not_trigger() -> None:
    seq = Seq().open(1).blink(0.15).open(1.35).blink(0.15).open(2)
    assert triggers(engine(), seq) == []


def test_a_minute_of_natural_blinking_does_not_trigger() -> None:
    seq = Seq().open(1)
    gaps = [3.1, 2.4, 4.0, 1.2, 3.6, 2.9, 1.5, 5.0, 2.2, 3.3, 1.1, 2.7, 4.4, 1.8, 3.0, 2.5]
    for gap in gaps:  # 16 blinks/min, some close together
        seq.blink(0.12).open(gap)
    e = engine()
    assert triggers(e, seq) == []
    assert e.blink_count == len(gaps)


def test_partial_blinks_close_together_do_not_trigger() -> None:
    """Natural pairs are often partial closures; a double blink needs full closures."""
    th = Thresholds.compute(OPEN, CLOSED, CalibrationConfig())
    partial = tuple(c + 0.03 for c in th.closed_thr)  # just over the threshold, not full
    seq = Seq().open(1)
    for _ in range(2):
        seq.frame(*partial).frame(*partial).frame(*partial).open(0.2)
    seq.open(1)
    e = engine()
    assert triggers(e, seq) == []
    assert e.blink_count == 2


# ----------------------------------------------------------------- double blink


def test_fast_double_blink_triggers_scroll_down() -> None:
    seq = Seq().open(1).blink(0.12).open(0.15).blink(0.12).open(1)
    result = triggers(engine(), seq)
    assert [(t.gesture, t.direction) for t in result] == [("double_blink", "down")]


def test_double_blink_just_outside_window_does_not_trigger() -> None:
    # 1st start -> 2nd end = 0.2 + 0.5 + 0.2 + ramps > 0.7 s
    seq = Seq().open(1).blink(0.2).open(0.5).blink(0.2).open(1)
    assert triggers(engine(), seq) == []


def test_cooldown_prevents_double_firing() -> None:
    # Triple blink: the 2nd+3rd must not form another double blink within the cooldown.
    seq = Seq().open(1)
    for _ in range(3):
        seq.blink(0.1).open(0.15)
    seq.open(1)
    assert len(triggers(engine(), seq)) == 1


def test_two_double_blinks_after_cooldown_both_trigger() -> None:
    seq = Seq().open(1).blink(0.1).open(0.15).blink(0.1).open(1.5)
    seq.blink(0.1).open(0.15).blink(0.1).open(1)
    assert len(triggers(engine(), seq)) == 2


def test_disabled_double_blink_never_triggers() -> None:
    seq = Seq().open(1).blink(0.12).open(0.15).blink(0.12).open(1)
    assert triggers(engine(double=False), seq) == []


# ----------------------------------------------------------------- long blink


def test_long_blink_triggers_once_while_held() -> None:
    seq = Seq().open(1).closed(1.5).open(1)
    result = triggers(engine(long=True), seq)
    assert [t.gesture for t in result] == ["long_blink"]
    assert result[0].t - 1.0 == pytest.approx(0.65, abs=0.1)  # fires at ~0.6 s, not on release


def test_two_quick_blinks_with_partial_reopen_are_not_a_long_blink() -> None:
    """Real data: natural pairs often reopen only part way (above the open threshold), so the
    hysteresis sees one long closure. Long blink needs one *continuous firm* closure."""
    th = Thresholds.compute(OPEN, CLOSED, CalibrationConfig())
    half_open = tuple((o + c) / 2 for o, c in zip(th.open_thr, th.closed_thr))
    seq = Seq().open(1).closed(0.3)
    for _ in range(4):
        seq.frame(*half_open)  # partial reopen for 0.2 s
    seq.closed(0.3).open(1)
    assert triggers(engine(long=True), seq) == []


def test_lids_lowering_raising_gaze_down_does_not_block_a_long_blink() -> None:
    """eyeLookDown jumps as the lids start to close; the gate is taken from before that."""
    seq = Seq().open(1)
    seq.frame(0.25, 0.2, look_down=0.8).frame(0.35, 0.3, look_down=0.8)  # lids lowering
    seq.closed(1.0).open(1)
    assert [t.gesture for t in triggers(engine(long=True), seq)] == ["long_blink"]


def test_lids_lowered_by_looking_down_are_not_a_long_blink() -> None:
    """Real data: looking at the keyboard held the scores at ~74% / ~57% of the closed level
    for over a second while the eyes were open, and the gate had not caught up yet."""
    lowered = (OPEN[0] + 0.74 * (CLOSED[0] - OPEN[0]), OPEN[1] + 0.57 * (CLOSED[1] - OPEN[1]))
    seq = Seq().open(1)
    for _ in range(40):  # 2 s
        seq.frame(*lowered)
    seq.open(1)
    assert triggers(engine(long=True), seq) == []


def test_long_blink_while_looking_at_keyboard_is_ignored() -> None:
    seq = Seq().open(1)
    seq.open(1.0, look_down=0.7)  # reading the keyboard; blink scores rise a little too
    seq.closed(1.0, look_down=0.7).open(1)
    assert triggers(engine(long=True), seq) == []


def test_neutral_pose_follows_slow_posture_change() -> None:
    e = engine()
    seq = Seq()
    for i in range(20 * 60):  # leaning back slowly over a minute: pitch drifts to +20
        seq.frame(*OPEN, pitch=20.0 * i / (20 * 60))
    seq.open(0.5, pitch=20.0).blink(0.12, pitch=20.0).open(0.15, pitch=20.0)
    seq.blink(0.12, pitch=20.0).open(1, pitch=20.0)
    assert len(triggers(e, seq)) == 1
    assert e.neutral[1] > 10


def test_long_blink_disabled_by_default() -> None:
    assert triggers(engine(), Seq().open(1).closed(1.5).open(1)) == []


def test_long_blink_then_quick_blink_is_not_a_double() -> None:
    seq = Seq().open(1).closed(0.9).open(0.1).blink(0.1).open(1)
    result = triggers(engine(long=True), seq)
    assert [t.gesture for t in result] == ["long_blink"]


# ----------------------------------------------------------------- wink


def test_wink_left_scrolls_up_right_scrolls_down() -> None:
    e = engine(wink=True)
    assert [(t.gesture, t.direction) for t in triggers(e, Seq().open(1).wink("left").open(2))] == [
        ("wink_left", "up")]
    e = engine(wink=True)
    assert [(t.gesture, t.direction) for t in triggers(e, Seq().open(1).wink("right").open(2))] == [
        ("wink_right", "down")]


def test_short_wink_is_ignored() -> None:
    assert triggers(engine(wink=True), Seq().open(1).wink("left", 0.1).open(1)) == []


def test_wink_with_squinting_other_eye_is_ignored() -> None:
    seq = Seq().open(1)
    for _ in range(10):
        seq.frame(CLOSED[0], 0.25)  # other eye half closed: not a clear wink
    seq.open(1)
    assert triggers(engine(wink=True), seq) == []


def test_normal_blinks_do_not_trigger_winks() -> None:
    seq = Seq().open(1)
    for _ in range(5):
        seq.frame(0.4, 0.04).blink(0.12).open(1.5)  # one eye leads by a frame
    assert triggers(engine(wink=True), seq) == []


# ----------------------------------------------------------------- noise and face loss


def test_noise_spikes_do_not_trigger() -> None:
    seq = Seq().open(1)
    for _ in range(6):  # isolated single-frame spikes, 0.2 s apart
        seq.frame(0.9, 0.9).open(0.2)
    seq.open(1)
    e = engine()
    assert triggers(e, seq) == []
    assert e.blink_count == 0


def test_face_lost_mid_blink_does_not_trigger() -> None:
    seq = Seq().open(1).blink(0.1).open(0.1).closed(0.05).no_face(0.4).open(0.2).open(1)
    assert triggers(engine(), seq) == []


def test_face_lost_mid_long_blink_does_not_trigger() -> None:
    seq = Seq().open(1).closed(0.3).no_face(1.0).closed(0.3).open(1)
    assert triggers(engine(long=True), seq) == []


# ----------------------------------------------------------------- gating


def test_double_blink_while_looking_down_is_ignored() -> None:
    seq = Seq().open(1, pitch=0.0)
    kw = {"pitch": -25.0}
    seq.open(0.5, **kw).blink(0.12, **kw).open(0.15, **kw).blink(0.12, **kw).open(1, **kw)
    assert triggers(engine(), seq) == []


def test_double_blink_with_eyes_looking_down_is_ignored() -> None:
    e = engine()
    seq = Seq().open(1)
    samples = Seq(t=seq.t).open(0.5).blink(0.12).open(0.15).blink(0.12).open(1).samples
    seq.samples += [dataclasses.replace(s, look_down=0.7) for s in samples]
    assert triggers(e, seq) == []


def test_double_blink_with_head_turned_is_ignored() -> None:
    kw = {"yaw": 35.0}
    seq = Seq().open(1).open(0.5, **kw).blink(0.12, **kw).open(0.15, **kw).blink(0.12, **kw)
    assert triggers(engine(), seq.open(1, **kw)) == []


def test_pose_is_relative_to_calibrated_neutral() -> None:
    e = engine()
    e.set_thresholds(Thresholds.compute(OPEN, CLOSED, CalibrationConfig()), neutral=(0.0, 20.0, 0.0))
    kw = {"pitch": 22.0}  # user's normal posture has 20 deg pitch: this is facing the screen
    seq = Seq().open(1, **kw).blink(0.12, **kw).open(0.15, **kw).blink(0.12, **kw).open(1, **kw)
    assert len(triggers(e, seq)) == 1


def test_gesture_works_again_after_looking_back_up() -> None:
    down = {"pitch": -25.0}
    seq = Seq().open(1).open(2, **down).open(0.5)
    seq.blink(0.12).open(0.15).blink(0.12).open(1)
    assert len(triggers(engine(), seq)) == 1


# ----------------------------------------------------------------- calibration + baseline


def test_full_flow_calibration_then_double_blink() -> None:
    e = GestureEngine(Config(), expected_fps=20)
    seq = Seq().calibration().open(1).blink(0.12).open(0.15).blink(0.12).open(1)
    result = triggers(e, seq)
    assert [t.gesture for t in result] == ["double_blink"]


def test_baseline_adapts_slowly_to_lighting_change() -> None:
    e = engine()
    th0 = e.thresholds
    assert th0 is not None
    seq = Seq()
    for _ in range(60 * 20):  # one minute with the open level drifted to 0.10
        seq.frame(0.10, 0.10)
    triggers(e, seq)
    th1 = e.thresholds
    assert th1 is not None
    assert th0.open_level[0] < th1.open_level[0] <= 0.10 + 1e-6
    assert th1.closed_thr[0] > th0.closed_thr[0]
    # still detects a double blink afterwards
    assert len(triggers(e, Seq(t=seq.t).blink(0.12).open(0.15).blink(0.12).open(1))) == 1
