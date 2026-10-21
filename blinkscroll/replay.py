"""Offline tuning: replay a --record CSV through the gesture engine with the current config.

    python -m blinkscroll.replay recordings/scores-....csv [--config config.toml]

Prints every blink and trigger with timing, so thresholds can be tuned without the camera.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .eyes import Blink, Calibrated
from .gestures import GateChanged, GestureEngine, Trigger
from .recorder import read_samples


def main() -> None:
    p = argparse.ArgumentParser(prog="blinkscroll.replay")
    p.add_argument("csv", type=Path)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    config = load_config(args.config)
    samples = list(read_samples(args.csv))
    if not samples:
        print("empty recording")
        return
    duration = samples[-1].t - samples[0].t
    fps = len(samples) / duration if duration > 0 else config.processing.fps
    engine = GestureEngine(config, expected_fps=fps)
    t0 = samples[0].t
    triggers = 0
    for s in samples:
        for ev in engine.update(s)[1]:
            t = s.t - t0
            if isinstance(ev, Calibrated):
                th = ev.thresholds
                print(f"{t:7.2f}s calibrated ({th.method}) open L{th.open_level[0]:.2f} "
                      f"R{th.open_level[1]:.2f} closed L{th.closed_level[0]:.2f} "
                      f"R{th.closed_level[1]:.2f}")
            elif isinstance(ev, Blink):
                print(f"{t:7.2f}s blink #{engine.blink_count}: {ev.duration * 1000:4.0f} ms, "
                      f"peak L{ev.peak[0]:.2f} R{ev.peak[1]:.2f}")
            elif isinstance(ev, Trigger):
                triggers += 1
                print(f"{t:7.2f}s >>> TRIGGER {ev.gesture} -> scroll {ev.direction}")
            elif isinstance(ev, GateChanged):
                print(f"{t:7.2f}s gate: {ev.reason or 'facing screen'}")
    face = sum(s.face for s in samples) / len(samples)
    print(f"\n{duration:.0f} s at {fps:.1f} fps, face {face:.0%}: "
          f"{engine.blink_count} blinks, {triggers} triggers")


if __name__ == "__main__":
    main()
