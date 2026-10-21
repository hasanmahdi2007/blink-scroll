"""Global hotkeys via pynput (pause/resume, recalibrate, quit)."""

from __future__ import annotations

import logging
from typing import Callable

from pynput import keyboard

from ..config import HotkeysConfig

log = logging.getLogger(__name__)


class Hotkeys:
    def __init__(self, on_toggle: Callable[[], None], on_recalibrate: Callable[[], None],
                 on_quit: Callable[[], None]) -> None:
        self._callbacks = {"toggle": on_toggle, "recalibrate": on_recalibrate, "quit": on_quit}
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self, config: HotkeysConfig) -> None:
        """(Re)register hotkeys. Callbacks run on the listener thread, so keep them quick."""
        self.stop()
        mapping: dict[str, Callable[[], None]] = {}
        names: list[str] = []
        for name, callback in self._callbacks.items():
            combo = getattr(config, name, "").strip()
            if not combo:
                continue
            try:
                keyboard.HotKey.parse(combo)
            except ValueError as exc:
                log.error("Invalid hotkey %s = %r: %s", name, combo, exc)
                continue
            mapping[combo] = callback
            names.append(f"{name} {combo}")
        if mapping:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
            log.info("Hotkeys: %s", ", ".join(names))

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
