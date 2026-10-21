"""Plain data passed from the landmark extractor to the gesture engine (no camera/OS deps)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    t: float  # seconds, monotonic
    face: bool  # a face was detected in this frame
    left: float = 0.0  # eyeBlinkLeft, 0 = open, 1 = closed
    right: float = 0.0  # eyeBlinkRight
    yaw: float = 0.0  # degrees
    pitch: float = 0.0
    roll: float = 0.0
