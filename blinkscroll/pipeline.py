"""Processing loop: newest camera frame -> landmarks -> listeners, at a capped frame rate.

Pausing stops the camera thread and releases the device, then the loop blocks on an Event, so a
paused app uses no camera and no CPU.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .camera import CameraCapture
from .config import Config
from .debug_view import DebugInfo, DebugView
from .landmarks import LandmarkExtractor
from .samples import Sample

log = logging.getLogger(__name__)

SampleListener = Callable[[Sample], None]


class FpsMeter:
    def __init__(self, alpha: float = 0.1) -> None:
        self._alpha = alpha
        self._last: float | None = None
        self.fps = 0.0

    def tick(self, now: float) -> None:
        if self._last is not None and now > self._last:
            inst = 1.0 / (now - self._last)
            self.fps = inst if self.fps == 0 else self.fps + self._alpha * (inst - self.fps)
        self._last = now

    def reset(self) -> None:
        self._last = None
        self.fps = 0.0


class Pipeline:
    def __init__(self, config: Config, debug: bool = False) -> None:
        self._config = config
        self._debug = debug
        self._camera = CameraCapture(config.camera)
        self._listeners: list[SampleListener] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = threading.Event()  # set = running, clear = paused
        self._active.set()
        self.fps = FpsMeter()
        self.on_debug_closed: Callable[[], None] | None = None

    def add_listener(self, listener: SampleListener) -> None:
        self._listeners.append(listener)

    @property
    def paused(self) -> bool:
        return not self._active.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._active.set()  # wake the loop if paused
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._camera.stop()

    def pause(self) -> None:
        if not self.paused:
            log.info("Paused")
            self._active.clear()

    def resume(self) -> None:
        if self.paused:
            log.info("Resumed")
            self._active.set()

    def _run(self) -> None:
        extractor = LandmarkExtractor(self._config.model_path,
                                      self._config.processing.inference_width)
        view = DebugView() if self._debug else None
        seq = 0
        next_tick = 0.0
        try:
            while not self._stop.is_set():
                if self.paused:
                    self._camera.stop()
                    self.fps.reset()
                    self._active.wait()
                    continue
                if not self._camera.running and not self._camera.start():
                    self._stop.wait(2.0)  # camera busy/unplugged: retry slowly
                    continue

                frame = self._camera.read_latest(seq, timeout=1.0)
                if frame is None:
                    continue
                seq = frame.seq
                sample = extractor.process(frame.image, frame.timestamp)
                self.fps.tick(time.monotonic())
                for listener in self._listeners:
                    listener(sample)

                if view is not None:
                    view.update(frame.image, self._debug_info(sample))
                    if view.closed:
                        view = None
                        if self.on_debug_closed:
                            self.on_debug_closed()

                # Cap the processing rate: sleep until the next slot (camera keeps draining).
                period = 1.0 / max(self._config.processing.fps, 0.5)
                now = time.monotonic()
                next_tick = max(next_tick + period, now) if next_tick else now + period
                delay = next_tick - now
                if delay > 0:
                    self._stop.wait(delay)
        except Exception:
            log.exception("Processing loop crashed")
        finally:
            if view is not None:
                view.close()
            extractor.close()
            self._camera.stop()

    def _debug_info(self, sample: Sample) -> DebugInfo:
        lines = [f"FPS {self.fps.fps:5.1f}", "face: yes" if sample.face else "face: NO"]
        if sample.face:
            lines.append(f"yaw {sample.yaw:+5.1f}  pitch {sample.pitch:+5.1f}  roll {sample.roll:+5.1f}")
        return DebugInfo(raw_left=sample.left, raw_right=sample.right, face=sample.face, lines=lines)
