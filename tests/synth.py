"""Synthetic score sequences for tests.

Levels mirror a real user with glasses: open ~0.04, fully closed ~0.56 (left) / ~0.45 (right).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blinkscroll.samples import Sample

FPS = 20.0
OPEN = (0.04, 0.04)
CLOSED = (0.56, 0.45)


@dataclass
class Seq:
    fps: float = FPS
    t: float = 0.0
    samples: list[Sample] = field(default_factory=list)

    def _add(self, n: int, left: float, right: float, face: bool = True,
             pitch: float = 0.0, yaw: float = 0.0, look_down: float = 0.0) -> Seq:
        for _ in range(n):
            self.samples.append(Sample(t=self.t, face=face, left=left, right=right,
                                       pitch=pitch, yaw=yaw, look_down=look_down))
            self.t += 1.0 / self.fps
        return self

    def frames(self, seconds: float) -> int:
        return max(1, round(seconds * self.fps))

    def open(self, seconds: float, **kw) -> Seq:
        return self._add(self.frames(seconds), *OPEN, **kw)

    def closed(self, seconds: float, **kw) -> Seq:
        return self._add(self.frames(seconds), *CLOSED, **kw)

    def blink(self, seconds: float = 0.15, **kw) -> Seq:
        """A blink with a one-frame ramp in and out, like real lids."""
        mid = (OPEN[0] + CLOSED[0]) / 2, (OPEN[1] + CLOSED[1]) / 2
        self._add(1, *mid, **kw)
        self.closed(seconds, **kw)
        return self._add(1, *mid, **kw)

    def wink(self, eye: str, seconds: float = 0.4) -> Seq:
        left = CLOSED[0] if eye == "left" else OPEN[0]
        right = CLOSED[1] if eye == "right" else OPEN[1]
        return self._add(self.frames(seconds), left, right)

    def no_face(self, seconds: float) -> Seq:
        return self._add(self.frames(seconds), 0.0, 0.0, face=False)

    def frame(self, left: float, right: float, **kw) -> Seq:
        return self._add(1, left, right, **kw)

    def calibration(self, open_s: float = 3.0, closed_s: float = 1.5) -> Seq:
        """What a cooperative user produces during the two-phase calibration."""
        self.open(open_s + 0.1)
        self.open(0.3)  # reaction time after the beep
        self.closed(0.3 + closed_s + 0.1)
        return self.open(0.5)
