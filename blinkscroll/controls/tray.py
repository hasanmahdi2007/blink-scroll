"""System tray icon (pystray) whose colour shows the state, with the main menu."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from . import STATE_COLORS, STATE_LABELS
from .look import ICONS, _font, has_icon_font

log = logging.getLogger(__name__)


@dataclass
class MenuActions:
    toggle: Callable[[], None]
    recalibrate: Callable[[], None]
    toggle_widget: Callable[[], None]
    reload_config: Callable[[], None]
    open_config: Callable[[], None]
    toggle_autostart: Callable[[], None]
    quit: Callable[[], None]
    is_paused: Callable[[], bool]
    widget_visible: Callable[[], bool]
    autostart_enabled: Callable[[], bool]


def make_icon(color: str, size: int = 64, paused: bool = False) -> Image.Image:
    """Same style as the floating button: dark disc, coloured status ring, eye icon."""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, big - 2, big - 2), fill=(23, 25, 31, 255))
    ring = big * 0.09
    d.ellipse((ring, ring, big - ring, big - ring), outline=color, width=round(big * 0.1))
    cx = cy = big / 2
    if has_icon_font():
        glyph = ICONS["eye_off" if paused else "eye"]
        d.text((cx, cy + big * 0.02), glyph, font=_font("icon", round(big * 0.46)),
               fill=(255, 255, 255, 255), anchor="mm")
    else:
        w, h = big * 0.5, big * 0.28
        d.ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), fill="white")
    return img.resize((size, size), Image.LANCZOS)


class TrayIcon:
    def __init__(self, actions: MenuActions) -> None:
        self._a = actions
        self._icons = {state: make_icon(color, paused=state == "paused")
                       for state, color in STATE_COLORS.items()}
        self._state = "starting"
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: STATE_LABELS.get(self._state, self._state), None,
                             enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: "Resume" if self._a.is_paused() else "Pause",
                             self._wrap(actions.toggle), default=True),
            pystray.MenuItem("Recalibrate", self._wrap(actions.recalibrate)),
            pystray.MenuItem("Show floating circle", self._wrap(actions.toggle_widget),
                             checked=lambda _: self._a.widget_visible()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Reload config", self._wrap(actions.reload_config)),
            pystray.MenuItem("Open config file", self._wrap(actions.open_config)),
            pystray.MenuItem("Start with Windows", self._wrap(actions.toggle_autostart),
                             checked=lambda _: self._a.autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._wrap(actions.quit)),
        )
        self._icon = pystray.Icon("blinkscroll", self._icons["starting"], "Blink Scroll", menu)

    @staticmethod
    def _wrap(fn: Callable[[], None]) -> Callable[..., None]:
        def handler(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            fn()
        return handler

    def start(self) -> None:
        self._icon.run_detached()

    def set_state(self, state: str, message: str = "") -> None:
        if state == self._state and not message:
            return
        self._state = state
        self._icon.icon = self._icons.get(state, self._icons["starting"])
        label = STATE_LABELS.get(state, state)
        self._icon.title = f"Blink Scroll - {label}" + (f": {message}" if message else "")
        self._icon.update_menu()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # already stopped
            pass
