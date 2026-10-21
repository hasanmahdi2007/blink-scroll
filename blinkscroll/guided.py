"""Guided tuning session: prompts you through natural reading, deliberate gestures, looking down
and turning your head, and records the labelled score time series (numbers only) to CSV.

    python -m blinkscroll.guided            (quit Blink Scroll first: it needs the camera)

Afterwards: python -m blinkscroll.replay <csv>  or send the CSV for tuning.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass

os.environ.setdefault("GLOG_minloglevel", "2")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from .camera import CameraCapture  # noqa: E402
from .config import DATA_DIR, load_config  # noqa: E402
from .gestures import GestureEngine  # noqa: E402
from .landmarks import LandmarkExtractor  # noqa: E402
from .pipeline import _beep  # noqa: E402
from .recorder import CsvRecorder  # noqa: E402
from .system import SingleInstance, enable_dpi_awareness  # noqa: E402

log = logging.getLogger("blinkscroll.guided")
WINDOW = "Blink Scroll - guided tuning (Esc to abort)"


@dataclass
class Step:
    label: str
    seconds: float
    text: str
    cue_every: float = 0.0  # beep + mark a cue this often
    cue_text: str = ""


STEPS = [
    Step("calibrate", 0, "Calibration: look at the screen. Close your eyes at the beep, "
                         "open at the 2nd beep."),
    Step("natural", 60, "Read something on screen normally. Blink naturally, don't think about it."),
    Step("double", 44, "At each beep: do ONE deliberate double blink, the way you'd like to scroll.",
         cue_every=4.0, cue_text="DOUBLE BLINK NOW"),
    Step("long", 24, "At each beep: close both eyes for about 1 second (long blink).",
         cue_every=4.0, cue_text="LONG BLINK NOW"),
    Step("wink", 24, "At each beep: wink. Alternate: LEFT eye, then RIGHT eye, ...",
         cue_every=4.0, cue_text="WINK NOW"),
    Step("keyboard", 15, "Look DOWN at your keyboard (eyes only and head). Blink normally."),
    Step("turn", 15, "Slowly turn your head LEFT and RIGHT, look away from the screen."),
    Step("screen", 10, "Look back at the screen normally."),
]


def _draw(image: np.ndarray, title: str, text: str, remaining: float, flash: bool) -> np.ndarray:
    h, w = 360, 900
    canvas = np.full((h, w, 3), 30, np.uint8)
    thumb = cv2.resize(cv2.flip(image, 1), (240, 180))
    canvas[20:200, w - 260:w - 20] = thumb
    color = (60, 230, 90) if flash else (0, 210, 255)
    cv2.putText(canvas, title, (24, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3, cv2.LINE_AA)
    words, line, y = text.split(), "", 120
    for word in words:  # simple word wrap
        if cv2.getTextSize(line + " " + word, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0] > w - 320:
            cv2.putText(canvas, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2,
                        cv2.LINE_AA)
            line, y = word, y + 34
        else:
            line = f"{line} {word}".strip()
    cv2.putText(canvas, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2,
                cv2.LINE_AA)
    cv2.putText(canvas, f"{remaining:4.0f} s", (24, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (200, 200, 200), 2, cv2.LINE_AA)
    return canvas


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if SingleInstance().already_running:
        log.error("Blink Scroll is running and holds the camera. Quit it first (Ctrl+Alt+Q).")
        return 1
    enable_dpi_awareness()
    config = load_config()
    config.calibration.reuse_saved = False
    camera = CameraCapture(config.camera)
    if not camera.start():
        log.error("Could not open the camera")
        return 1
    extractor = LandmarkExtractor(config.model_path, config.processing.inference_width)
    engine = GestureEngine(config)
    path = DATA_DIR / "recordings" / time.strftime("guided-%Y%m%d-%H%M%S.csv")
    recorder = CsvRecorder(path)
    seq, aborted = 0, False
    period = 1.0 / config.processing.fps
    try:
        for step in STEPS:
            log.info("== %s: %s", step.label, step.text)
            _beep((600, 80))
            start = time.monotonic()
            next_cue = start + 2.0 if step.cue_every else float("inf")
            cue_flash_until = 0.0
            cues = 0
            while True:
                now = time.monotonic()
                elapsed = now - start
                if step.label == "calibrate":
                    if not engine.tracker.calibrating and elapsed > 1.0:
                        break
                elif elapsed >= step.seconds:
                    break
                frame = camera.read_latest(seq, timeout=2.0)
                if frame is None:
                    continue
                seq = frame.seq
                sample = extractor.process(frame.image, frame.timestamp)
                eye, events = engine.update(sample)
                names = [type(e).__name__ for e in events]
                for e in events:
                    phase = getattr(e, "phase", None)
                    if phase is not None and phase.value == "close-leadin":
                        _beep((880, 150))
                    elif phase is not None and phase.value == "done":
                        _beep((660, 120), (990, 120))
                if now >= next_cue:
                    cues += 1
                    names.append(f"cue:{step.label}:{cues}")
                    _beep((1000, 120))
                    cue_flash_until = now + 1.0
                    next_cue += step.cue_every
                    if now + step.cue_every > start + step.seconds:
                        next_cue = float("inf")
                recorder.write(sample, eye, engine.thresholds, step.label, names)

                flashing = now < cue_flash_until
                title = step.cue_text if flashing else step.label.upper()
                remaining = max(step.seconds - elapsed, 0) if step.seconds else 0
                cv2.imshow(WINDOW, _draw(frame.image, title, step.text, remaining, flashing))
                if cv2.waitKey(1) & 0xFF == 27:
                    aborted = True
                    break
                time.sleep(max(0.0, period - (time.monotonic() - now)))
            if aborted:
                break
    finally:
        recorder.close()
        extractor.close()
        camera.stop()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
    _beep((990, 120), (660, 120))
    log.info("%s. Recording: %s", "Aborted" if aborted else "Done", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
