"""Application controller: wires pipeline, hotkeys, tray icon and floating circle together.

Threads: main = Tk event loop (circle + calibration label); ``pipeline`` = processing;
``camera`` = capture; pynput hotkey listener; pystray tray loop; ``scroller``. Other threads never
touch Tk: they post callables to ``self._calls``, which the main thread drains every 150 ms.
"""

from __future__ import annotations

import logging
import queue
import signal
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

from . import system
from .config import Config, load_config
from .controls.hotkeys import Hotkeys
from .controls.tray import MenuActions, TrayIcon
from .controls.widget import FloatingWidget
from .eyes import Calibrated
from .gestures import EngineEvent, HoldEnded, Trigger
from .pipeline import Pipeline, State
from .state import AppState

log = logging.getLogger(__name__)

POLL_MS = 150
LOCK_CHECK_MS = 2000
_ARROW = {"up": "↑", "down": "↓"}


def gesture_hint(config: Config) -> str:
    """Short reminder of the enabled gestures, e.g. 'Left wink ↓ · Long blink ↑'."""
    g = config.gestures
    parts = []
    if g.wink.enabled:
        parts.append(f"Left wink {_ARROW.get(g.wink.left_direction, '')}")
        parts.append(f"Right wink {_ARROW.get(g.wink.right_direction, '')}")
    if g.long_blink.enabled:
        parts.append(f"Long blink {_ARROW.get(g.long_blink.direction, '')}")
    if g.double_blink.enabled:
        parts.append(f"Double blink {_ARROW.get(g.double_blink.direction, '')}")
    return " · ".join(parts) or "No gestures enabled"


class App:
    def __init__(self, config_path: Path, debug: bool = False, record: Path | None = None,
                 duration: float = 0.0, start_paused: bool = False) -> None:
        self.config_path = config_path
        self.config: Config = load_config(config_path)
        self.state_store = AppState()
        self._apply_saved_widget_position(self.config)
        self._calls: queue.Queue[Callable[[], None]] = queue.Queue()
        self._quitting = threading.Event()
        self._locked_pause = False  # we paused because Windows got locked
        self._duration = duration

        self.pipeline = Pipeline(self.config, debug=debug, record=record,
                                 start_paused=start_paused)
        saved = self.state_store.calibration() if self.config.calibration.reuse_saved else None
        if saved is not None:
            self.pipeline.engine.set_thresholds(*saved)
            log.info("Using saved calibration (Recalibrate from the menu if detection feels off)")
        self.pipeline.add_state_listener(lambda s, m: self.call(lambda: self._on_state(s, m)))
        self.pipeline.add_event_listener(self._on_event)
        self.pipeline.on_debug_closed = self.quit

        actions = MenuActions(
            toggle=lambda: self.call(self.toggle),
            recalibrate=lambda: self.call(self.recalibrate),
            toggle_widget=lambda: self.call(self.toggle_widget),
            reload_config=lambda: self.call(self.reload_config),
            open_config=lambda: self.call(self.open_config),
            toggle_autostart=lambda: self.call(self.toggle_autostart),
            quit=self.quit,
            is_paused=lambda: self.pipeline.paused,
            widget_visible=lambda: self.widget is not None and self.widget.visible,
            autostart_enabled=system.autostart_enabled,
        )
        self.root = tk.Tk()
        self.root.withdraw()  # the root is only an event loop; the circle is a Toplevel
        self.widget: FloatingWidget | None = FloatingWidget(
            self.root, self.config.widget, actions, self._on_widget_moved)
        self.widget.hint = gesture_hint(self.config)
        self.tray = TrayIcon(actions)
        self.hotkeys = Hotkeys(on_toggle=lambda: self.call(self.toggle),
                               on_recalibrate=lambda: self.call(self.recalibrate),
                               on_quit=self.quit)

    # ------------------------------------------------------------------ lifecycle

    def run(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        self.tray.start()
        self.hotkeys.start(self.config.hotkeys)
        if self.config.widget.enabled and self.widget is not None:
            self.widget.show()
        self._check_lock()  # before the camera opens; reschedules itself
        self.pipeline.start()
        if self._duration > 0:
            self.root.after(int(self._duration * 1000), self.quit)
        self.root.after(POLL_MS, self._poll)
        log.info("Blink Scroll running. Toggle %s, quit %s (or use the tray / circle).",
                 self.config.hotkeys.toggle, self.config.hotkeys.quit)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def call(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the main (Tk) thread. Safe from any thread."""
        self._calls.put(fn)

    def quit(self) -> None:
        """Safe from any thread."""
        self._quitting.set()

    def _poll(self) -> None:
        while True:
            try:
                fn = self._calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("UI action failed")
        if self._quitting.is_set():
            self.root.quit()
            return
        self.root.after(POLL_MS, self._poll)

    def _shutdown(self) -> None:
        log.info("Shutting down")
        self.hotkeys.stop()
        self.pipeline.stop()
        self.tray.stop()
        if self.widget is not None:
            self.widget.destroy()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        log.info("Bye")

    # ------------------------------------------------------------------ actions (main thread)

    def toggle(self) -> None:
        self._locked_pause = False
        self.pipeline.toggle()

    def recalibrate(self) -> None:
        log.info("Recalibrating")
        self.pipeline.recalibrate()

    def toggle_widget(self) -> None:
        if self.widget is None:
            return
        if self.widget.visible:
            self.widget.hide()
        else:
            self.widget.show()
        self.tray.set_state(self.pipeline.state)

    def reload_config(self) -> None:
        try:
            config = load_config(self.config_path)
        except Exception as exc:  # TOML syntax errors etc. keep the old config
            log.error("Config not reloaded: %s", exc)
            return
        self._apply_saved_widget_position(config)
        self.config = config
        self.pipeline.apply_config(config)
        self.hotkeys.start(config.hotkeys)
        if self.widget is not None:
            self.widget.hint = gesture_hint(config)
            self.widget.apply_config(config.widget)
            if config.widget.enabled and not self.widget.visible:
                self.widget.show()
            elif not config.widget.enabled and self.widget.visible:
                self.widget.hide()
        log.info("Config reloaded from %s", self.config_path)

    def open_config(self) -> None:
        system.open_file(self.config_path)

    def toggle_autostart(self) -> None:
        try:
            system.set_autostart(not system.autostart_enabled())
        except Exception as exc:
            log.error("Could not change autostart: %s", exc)

    # ------------------------------------------------------------------ callbacks

    def _on_state(self, state: str, message: str) -> None:
        shown = state
        if state == State.ACTIVE and message:
            shown = "gated"  # face seen but not facing the screen
        self.tray.set_state(shown, message)
        if self.widget is not None:
            self.widget.set_state(shown, message)

    def _on_event(self, ev: EngineEvent) -> None:
        """Called on the pipeline thread."""
        widget = self.widget
        if isinstance(ev, Trigger) and widget is not None:
            if ev.hold:
                self.call(lambda d=ev.direction: widget.set_scrolling(d))
            else:
                self.call(widget.pulse)
        elif isinstance(ev, HoldEnded) and widget is not None:
            self.call(lambda: widget.set_scrolling(None))
        elif isinstance(ev, Calibrated):
            self.state_store.set_calibration(ev.thresholds, self.pipeline.engine.neutral)

    def _on_widget_moved(self, edge: str, y_fraction: float) -> None:
        self.state_store.data["widget"] = {"edge": edge, "y_fraction": round(y_fraction, 4)}
        self.state_store.save()

    def _apply_saved_widget_position(self, config: Config) -> None:
        pos = self.state_store.data.get("widget")
        if isinstance(pos, dict):
            config.widget.edge = pos.get("edge", config.widget.edge)
            config.widget.y_fraction = float(pos.get("y_fraction", config.widget.y_fraction))

    def _check_lock(self) -> None:
        if self.config.processing.pause_when_locked:
            locked = system.is_session_locked()
            if locked and not self.pipeline.paused:
                log.info("Windows locked: pausing (camera off)")
                self._locked_pause = True
                self.pipeline.pause()
            elif not locked and self._locked_pause:
                log.info("Windows unlocked: resuming")
                self._locked_pause = False
                self.pipeline.resume()
        self.root.after(LOCK_CHECK_MS, self._check_lock)
