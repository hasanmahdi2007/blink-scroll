"""Processing loop: newest camera frame -> landmarks -> gesture engine -> actions.

* Frame rate is capped; after ``low_power_after_s`` without a face it drops to ``low_power_fps``
  and returns to full rate as soon as a face is seen.
* Pausing stops the capture thread and releases the camera (the light turns off); the loop then
  blocks on an Event, so a paused app does no work at all.
* Other threads talk to the loop only through thread-safe requests (pause/resume/recalibrate/
  apply_config); everything else happens on the processing thread.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .actions import Scroller
from .camera import CameraCapture
from .config import Config
from .debug_view import DebugInfo, DebugView
from .eyes import Blink, Calibrated, CalibrationPhase, EyeFrame, Phase
from .gestures import EngineEvent, GateChanged, GestureEngine, Trigger
from .landmarks import LandmarkExtractor
from .recorder import CsvRecorder
from .samples import Sample

log = logging.getLogger(__name__)


class State:
    STARTING = "starting"
    CALIBRATING = "calibrating"
    ACTIVE = "active"
    NO_FACE = "no-face"
    LOW_POWER = "low-power"
    PAUSED = "paused"
    CAMERA_ERROR = "camera-error"


StateListener = Callable[[str, str], None]  # (state, message)
EventListener = Callable[[EngineEvent], None]


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


def _beep(*tones: tuple[int, int]) -> None:
    """Non-blocking audio cue (needed during the eyes-closed calibration phase)."""
    def play() -> None:
        try:
            import winsound
            for freq, ms in tones:
                winsound.Beep(freq, ms)
        except (ImportError, RuntimeError):
            pass
    threading.Thread(target=play, name="beep", daemon=True).start()


class Pipeline:
    def __init__(self, config: Config, debug: bool = False, record: Path | None = None,
                 start_paused: bool = False) -> None:
        self._config = config
        self._debug = debug
        self._record_path = record
        self._camera = CameraCapture(config.camera)
        self.engine = GestureEngine(config)
        self.scroller = Scroller(config.scroll)
        self._state_listeners: list[StateListener] = []
        self._event_listeners: list[EventListener] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = threading.Event()  # set = running, clear = paused
        if not start_paused:
            self._active.set()
        self._recalibrate = threading.Event()
        self._new_config: Config | None = None
        self._lock = threading.Lock()
        self.fps = FpsMeter()
        self.state = State.STARTING
        self.message = ""
        self.low_power = False
        self.force_low_power = False  # for measuring
        self.on_debug_closed: Callable[[], None] | None = None
        self._flash = ""
        self._last_face = time.monotonic()

    # ------------------------------------------------------------------ public, thread-safe

    def add_state_listener(self, listener: StateListener) -> None:
        self._state_listeners.append(listener)

    def add_event_listener(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

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
        self.scroller.close()

    def pause(self) -> None:
        if not self.paused:
            log.info("Paused: camera released")
            self._active.clear()
            self._set_state(State.PAUSED)

    def resume(self) -> None:
        if self.paused:
            log.info("Resumed")
            self._active.set()

    def toggle(self) -> None:
        self.resume() if self.paused else self.pause()

    def recalibrate(self) -> None:
        self._recalibrate.set()
        self.resume()

    def apply_config(self, config: Config) -> None:
        """Thread-safe: applied on the processing thread before the next frame (or on resume)."""
        with self._lock:
            self._new_config = config

    # ------------------------------------------------------------------ loop

    def _run(self) -> None:
        extractor: LandmarkExtractor | None = None
        view: DebugView | None = None
        recorder = CsvRecorder(self._record_path) if self._record_path else None
        seq = 0
        next_tick = 0.0
        try:
            extractor = LandmarkExtractor(self._config.model_path,
                                          self._config.processing.inference_width)
            view = DebugView() if self._debug else None
            while not self._stop.is_set():
                self._apply_pending_config()
                if self.paused:
                    self._camera.stop()
                    self.fps.reset()
                    self._set_state(State.PAUSED)
                    self._active.wait()
                    next_tick = 0.0
                    self._last_face = time.monotonic()  # resume at full rate
                    continue
                if not self._camera.running:
                    if not self._camera.start():
                        self._set_state(State.CAMERA_ERROR, self._camera.error or "")
                        self._stop.wait(3.0)  # camera busy/unplugged: retry slowly
                        continue
                    seq = 0
                if self._recalibrate.is_set():
                    self._recalibrate.clear()
                    self.engine.recalibrate()

                frame = self._camera.read_latest(seq, timeout=2.0)
                if frame is None:
                    continue
                seq = frame.seq
                sample = extractor.process(frame.image, frame.timestamp)
                self.fps.tick(time.monotonic())
                eye, events = self.engine.update(sample)
                names = [self._handle(ev) for ev in events]
                self._update_state(sample)
                if recorder is not None:
                    recorder.write(sample, eye, self.engine.thresholds, self.state,
                                   [n for n in names if n])

                if view is not None:
                    try:
                        view.update(frame.image, self._debug_info(sample, eye))
                    except Exception:  # a broken preview must never stop detection
                        log.exception("Debug window failed; continuing without it")
                        view.closed = True
                    if view.last_key == "c":
                        self.engine.recalibrate()
                    if view.closed:
                        view = None
                        if self.on_debug_closed:
                            self.on_debug_closed()

                # Cap the processing rate: sleep until the next slot (camera keeps draining).
                period = 1.0 / max(self._target_fps(), 0.5)
                now = time.monotonic()
                next_tick = max(next_tick + period, now) if next_tick else now + period
                delay = next_tick - now
                if delay > 0:
                    self._stop.wait(delay)
        except Exception:
            log.exception("Processing loop crashed")
            self._set_state(State.CAMERA_ERROR, "processing crashed, see log")
        finally:
            if view is not None:
                view.close()
            if recorder is not None:
                recorder.close()
            if extractor is not None:
                extractor.close()
            self._camera.stop()

    def _target_fps(self) -> float:
        p = self._config.processing
        return p.low_power_fps if self.low_power else p.fps

    def _apply_pending_config(self) -> None:
        with self._lock:
            config, self._new_config = self._new_config, None
        if config is None:
            return
        camera_changed = config.camera != self._config.camera
        self._config = config
        self.engine.apply_config(config)
        self.scroller.config = config.scroll
        if camera_changed:
            self._camera.stop()
            self._camera = CameraCapture(config.camera)
        log.info("Config applied%s", " (camera restarted)" if camera_changed else "")

    # ------------------------------------------------------------------ state + events

    def _update_state(self, sample: Sample) -> None:
        now = time.monotonic()
        if sample.face:
            self._last_face = now
            if self.low_power:
                self.low_power = False
                log.info("Face back: full rate (%.0f fps)", self._config.processing.fps)
        elif not self.low_power and now - self._last_face > self._config.processing.low_power_after_s:
            self.low_power = True
            log.info("No face for %.0f s: low-power mode (%.0f fps)",
                     self._config.processing.low_power_after_s, self._config.processing.low_power_fps)
        if self.force_low_power:
            self.low_power = True

        if self.engine.tracker.calibrating:
            self._set_state(State.CALIBRATING, self.message)
        elif self.low_power:
            self._set_state(State.LOW_POWER, "no face, low-power mode")
        elif not sample.face:
            self._set_state(State.NO_FACE, "no face")
        else:
            self._set_state(State.ACTIVE, self.engine.gate_reason)

    def _set_state(self, state: str, message: str = "") -> None:
        if state == self.state and message == self._last_message:
            return
        self.state = state
        self._last_message = message
        for listener in self._state_listeners:
            try:
                listener(state, message)
            except Exception:
                log.exception("State listener failed")

    _last_message = ""

    def _handle(self, ev: EngineEvent) -> str:
        """Act on an engine event; returns a short name for the recording."""
        name = ""
        if isinstance(ev, CalibrationPhase):
            self.message = "" if ev.phase is Phase.DONE else ev.message
            log.info(ev.message)
            if ev.phase is Phase.CLOSE_LEADIN:
                _beep((880, 150))
            elif ev.phase is Phase.DONE and self._config.calibration.use_closed_phase:
                _beep((660, 120), (990, 120))
            name = f"calibration:{ev.phase.value}"
        elif isinstance(ev, Calibrated):
            name = "calibrated"
        elif isinstance(ev, Blink):
            log.debug("Blink #%d: %.0f ms, peak L%.2f R%.2f", self.engine.blink_count,
                      ev.duration * 1000, *ev.peak)
            self._flash = f"BLINK #{self.engine.blink_count}"
            name = "blink"
        elif isinstance(ev, Trigger):
            log.info("Trigger: %s -> scroll %s", ev.gesture, ev.direction)
            self.scroller.scroll(ev.direction)
            self._flash = f"SCROLL {ev.direction.upper()} ({ev.gesture})"
            name = f"trigger:{ev.gesture}"
        elif isinstance(ev, GateChanged):
            name = f"gate:{ev.reason or 'ok'}"
        for listener in self._event_listeners:
            try:
                listener(ev)
            except Exception:
                log.exception("Event listener failed")
        return name

    def _debug_info(self, sample: Sample, eye: EyeFrame) -> DebugInfo:
        e = self.engine
        th = e.thresholds
        closed = "".join(("L" if eye.closed[0] else "-", "R" if eye.closed[1] else "-"))
        lines = [
            f"FPS {self.fps.fps:5.1f}   state: {self.state}",
            f"blinks: {e.blink_count}  triggers: {e.trigger_count}  closed: {closed}",
            f"gate: {e.gate_reason or 'OK (facing screen)'}",
        ]
        if sample.face:
            n = e.neutral
            lines.append(f"yaw {sample.yaw - n[0]:+5.1f} pitch {sample.pitch - n[1]:+5.1f} "
                         f"roll {sample.roll - n[2]:+5.1f}  gaze-down {sample.look_down:.2f}")
        else:
            lines.append("no face")
        if th is not None:
            lines.append(f"open lvl L{th.open_level[0]:.2f} R{th.open_level[1]:.2f}  "
                         f"closed lvl L{th.closed_level[0]:.2f} R{th.closed_level[1]:.2f}")
            lines.append(f"thr close L{th.closed_thr[0]:.2f} R{th.closed_thr[1]:.2f}"
                         f"  open L{th.open_thr[0]:.2f} R{th.open_thr[1]:.2f}  ({th.method})")
        flash, self._flash = self._flash, ""
        return DebugInfo(
            raw_left=sample.left, raw_right=sample.right,
            smooth_left=eye.smooth[0] if eye.face else None,
            smooth_right=eye.smooth[1] if eye.face else None,
            closed_thr=th.closed_thr if th else None, open_thr=th.open_thr if th else None,
            face=sample.face, lines=lines, flash=flash, banner=self.message,
        )
