from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from blinkscroll.config import Config, load_config, save_config
from blinkscroll.gestures import GestureEngine, Trigger
from blinkscroll.pose import euler_from_matrix
from blinkscroll.recorder import CsvRecorder, read_samples

from .synth import Seq


def test_recording_replays_to_the_same_gestures(tmp_path: Path) -> None:
    seq = Seq().calibration().open(1).blink(0.12).open(0.15).blink(0.12).open(2).blink().open(1)
    path = tmp_path / "rec.csv"
    cfg = Config()
    cfg.gestures.double_blink.enabled = True
    cfg.gestures.wink.enabled = cfg.gestures.long_blink.enabled = False
    engine = GestureEngine(cfg, expected_fps=20)
    recorder = CsvRecorder(path)
    live = []
    for s in seq.samples:
        frame, events = engine.update(s)
        live += [e for e in events if isinstance(e, Trigger)]
        recorder.write(s, frame, engine.thresholds, "x", [])
    recorder.close()

    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert "raw_left" in header and "image" not in header  # numbers only

    replay_engine = GestureEngine(cfg, expected_fps=20)
    replayed = []
    for s in read_samples(path):
        replayed += [e for e in replay_engine.update(s)[1] if isinstance(e, Trigger)]
    assert [t.gesture for t in replayed] == [t.gesture for t in live] == ["double_blink"]


def test_config_round_trip_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = load_config(path)  # writes defaults
    assert path.exists()
    assert cfg == Config()
    cfg.scroll.amount = 8.0
    cfg.gestures.wink.enabled = True
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_config_tolerates_unknown_keys_and_bad_types(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[scroll]\namount = 3\nbogus = 1\n[detection]\ncooldown_s = "fast"\n',
                    encoding="utf-8")
    cfg = load_config(path)
    assert cfg.scroll.amount == 3.0  # int accepted for float
    assert cfg.detection.cooldown_s == Config().detection.cooldown_s  # bad type -> default


@pytest.mark.parametrize("axis,expected", [("x", (0, 30, 0)), ("y", (30, 0, 0)), ("z", (0, 0, 30))])
def test_euler_from_matrix(axis: str, expected: tuple[float, float, float]) -> None:
    a = np.radians(30)
    c, s = np.cos(a), np.sin(a)
    r = {"x": [[1, 0, 0], [0, c, -s], [0, s, c]],
         "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
         "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]
    m = np.eye(4)
    m[:3, :3] = np.array(r) * 1.7  # uniform scale must not matter
    assert euler_from_matrix(m) == pytest.approx(expected, abs=1e-6)
