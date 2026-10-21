"""--debug preview window: camera thumbnail, live scores, a scrolling score plot and status text.

Drawn with OpenCV from the processing thread. Frames are only displayed, never stored.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

WINDOW = "Blink Scroll - debug (q: quit, c: recalibrate)"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_W, _H = 760, 480
_THUMB_W = 320
_PLOT_H = 150
_HISTORY = 240

# BGR colors
_LEFT = (80, 200, 255)
_RIGHT = (255, 160, 80)
_GREY = (130, 130, 130)
_WHITE = (235, 235, 235)


@dataclass
class DebugInfo:
    """What to show. Raw scores always; the rest is filled in as the pipeline grows."""

    raw_left: float = 0.0
    raw_right: float = 0.0
    smooth_left: float | None = None
    smooth_right: float | None = None
    closed_thr: tuple[float, float] | None = None  # per eye (left, right)
    open_thr: tuple[float, float] | None = None
    face: bool = False
    lines: list[str] = field(default_factory=list)
    flash: str = ""  # short event text (BLINK / TRIGGER) highlighted for a moment
    banner: str = ""  # persistent instruction, e.g. during calibration


class DebugView:
    def __init__(self) -> None:
        self._history: deque[tuple[float, float, float | None, float | None, bool]] = deque(
            maxlen=_HISTORY)
        self._flash = ""
        self._flash_frames = 0
        self.closed = False
        self.last_key = ""  # key pressed during the last update ("" if none)
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)

    def update(self, image: np.ndarray | None, info: DebugInfo) -> None:
        """Render one frame. Sets ``closed`` if the user pressed q or closed the window."""
        if self.closed:
            return
        self._history.append(
            (info.raw_left, info.raw_right, info.smooth_left, info.smooth_right, info.face))
        if info.flash:
            self._flash, self._flash_frames = info.flash, 8

        canvas = np.full((_H, _W, 3), 24, np.uint8)
        thumb_h = _H - _PLOT_H - 10
        if image is not None:
            h, w = image.shape[:2]
            scale = min(_THUMB_W / w, thumb_h / h)
            thumb = cv2.resize(cv2.flip(image, 1), (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)
            canvas[: thumb.shape[0], : thumb.shape[1]] = thumb

        x0 = _THUMB_W + 16
        self._bars(canvas, x0, 14, info)
        y = 120
        for line in info.lines:
            cv2.putText(canvas, line, (x0, y), _FONT, 0.5, _WHITE, 1, cv2.LINE_AA)
            y += 22
        if self._flash_frames > 0:
            cv2.putText(canvas, self._flash, (x0, thumb_h - 10), _FONT, 0.9, (90, 255, 120), 2,
                        cv2.LINE_AA)
            self._flash_frames -= 1
        self._plot(canvas, _H - _PLOT_H, info)
        if info.banner:
            (tw, th), _ = cv2.getTextSize(info.banner, _FONT, 0.6, 2)
            bx, by = max((_W - tw) // 2, 4), _H - _PLOT_H - 18
            cv2.rectangle(canvas, (bx - 8, by - th - 8), (bx + tw + 8, by + 8), (0, 0, 0), -1)
            cv2.putText(canvas, info.banner, (bx, by), _FONT, 0.6, (0, 220, 255), 2, cv2.LINE_AA)

        cv2.imshow(WINDOW, canvas)
        key = cv2.waitKey(1) & 0xFF
        self.last_key = chr(key) if key != 255 else ""
        if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            self.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                cv2.destroyWindow(WINDOW)  # fails if the user already closed it with X
                cv2.waitKey(1)
            except cv2.error:
                pass

    def _bars(self, canvas: np.ndarray, x0: int, y0: int, info: DebugInfo) -> None:
        width = _W - x0 - 20
        rows = [
            ("L raw", info.raw_left, _LEFT, 0),
            ("R raw", info.raw_right, _RIGHT, 1),
        ]
        if info.smooth_left is not None and info.smooth_right is not None:
            rows += [("L ema", info.smooth_left, _LEFT, 0), ("R ema", info.smooth_right, _RIGHT, 1)]
        for i, (label, value, color, eye) in enumerate(rows):
            y = y0 + i * 24
            cv2.putText(canvas, f"{label} {value:.2f}", (x0, y + 13), _FONT, 0.45, _WHITE, 1,
                        cv2.LINE_AA)
            bx = x0 + 100
            bw = width - 100
            cv2.rectangle(canvas, (bx, y), (bx + bw, y + 16), (60, 60, 60), -1)
            cv2.rectangle(canvas, (bx, y), (bx + int(bw * min(max(value, 0), 1)), y + 16), color, -1)
            for thr, col in ((info.closed_thr, (60, 60, 255)), (info.open_thr, (60, 255, 60))):
                if thr is not None:
                    tx = bx + int(bw * thr[eye])
                    cv2.line(canvas, (tx, y - 2), (tx, y + 18), col, 2)

    def _plot(self, canvas: np.ndarray, top: int, info: DebugInfo) -> None:
        h = _PLOT_H - 10
        cv2.rectangle(canvas, (0, top), (_W - 1, top + h), (40, 40, 40), -1)

        def ypos(v: float) -> int:
            return top + h - int(h * min(max(v, 0.0), 1.0))

        for thr, col in ((info.closed_thr, (60, 60, 255)), (info.open_thr, (60, 255, 60))):
            if thr is not None:
                for v in set(thr):
                    cv2.line(canvas, (0, ypos(v)), (_W, ypos(v)), col, 1)
        n = len(self._history)
        if n < 2:
            return
        step = _W / (_HISTORY - 1)
        for idx, color in ((0, _LEFT), (1, _RIGHT)):
            smoothed = self._history[-1][idx + 2] is not None
            raw_col = tuple(c // 2 for c in color) if smoothed else color
            self._polyline(canvas, [s[idx] for s in self._history], step, ypos, raw_col)
            if smoothed:
                vals = [s[idx + 2] if s[idx + 2] is not None else 0.0 for s in self._history]
                self._polyline(canvas, vals, step, ypos, color)
        for i, s in enumerate(self._history):  # mark frames without a face
            if not s[4]:
                x = int(i * step)
                cv2.line(canvas, (x, top + h - 3), (x, top + h), _GREY, 1)

    @staticmethod
    def _polyline(canvas: np.ndarray, values: list[float], step: float, ypos, color) -> None:
        pts = np.array([(int(i * step), ypos(v)) for i, v in enumerate(values)], np.int32)
        cv2.polylines(canvas, [pts], False, color, 1, cv2.LINE_AA)
