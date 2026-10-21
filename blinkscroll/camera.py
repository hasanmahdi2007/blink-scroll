"""Threaded webcam capture that only ever keeps the newest frame.

The capture thread reads continuously at the camera's rate (``read()`` blocks until the next
frame, so this costs little) and overwrites a single slot; older frames are simply dropped, so
the consumer never sees a backlog. Note: a grab()-only loop is *not* cheaper on DirectShow, it
returns immediately when frames are not retrieved and spins a full core.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)

_BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    timestamp: float  # time.monotonic() when grabbed
    seq: int


class CameraCapture:
    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._seq = 0
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Open the camera and start the capture thread. Returns False if the camera failed."""
        if self.running:
            return True
        cap = self._open()
        if cap is None:
            return False
        self._cap = cap
        self._stop.clear()
        self._latest = None
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop capturing and release the device (turns the camera light off)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            log.info("Camera released")
        with self._cond:
            self._latest = None
            self._cond.notify_all()

    def read_latest(self, after_seq: int, timeout: float = 1.0) -> Frame | None:
        """Return a frame newer than ``after_seq``, waiting up to ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while (self._latest is None or self._latest.seq <= after_seq) and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._latest if self._latest is not None and self._latest.seq > after_seq else None

    def _open(self) -> cv2.VideoCapture | None:
        cfg = self._config
        preferred = _BACKENDS.get(cfg.backend.lower(), cv2.CAP_DSHOW)
        for backend in dict.fromkeys([preferred, cv2.CAP_MSMF, cv2.CAP_DSHOW]):
            t0 = time.perf_counter()
            cap = cv2.VideoCapture(cfg.index, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
            cap.set(cv2.CAP_PROP_FPS, cfg.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, _ = cap.read()
            if not ok:
                cap.release()
                continue
            log.info(
                "Camera %d opened with %s in %.2fs (%dx%d @ %.0f fps)",
                cfg.index, cap.getBackendName(), time.perf_counter() - t0,
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                cap.get(cv2.CAP_PROP_FPS),
            )
            self.error = None
            return cap
        self.error = f"Could not open camera {cfg.index}"
        log.error(self.error)
        return None

    def _run(self) -> None:
        assert self._cap is not None
        cap = self._cap
        failures = 0
        while not self._stop.is_set():
            ok, image = cap.read()
            if not ok:
                failures += 1
                if failures > 30:
                    self.error = "Camera stopped delivering frames"
                    log.error(self.error)
                    break
                time.sleep(0.05)
                continue
            failures = 0
            ts = time.monotonic()
            with self._cond:
                self._seq += 1
                self._latest = Frame(image=image, timestamp=ts, seq=self._seq)
                self._cond.notify_all()
