"""Floating on/off control docked to a screen edge (Tk event loop, Win32 layered window).

* Collapsed it is a round button with a coloured status ring. It auto-hides, leaving a small
  sliver at the screen edge, and slides out when the mouse touches it.
* On hover it expands into a pill: status text, Pause/Resume, Recalibrate and Settings (menu).
* Click the round button: on/off. Drag it to move it along the edge, or onto the other half of
  the screen to switch sides. Right-click anywhere on it: menu.
* While a held gesture scrolls, the icon becomes an arrow. Calibration instructions appear in
  the pill itself.

Drawing is in ``look.py`` (Pillow, anti-aliased); ``layered.py`` puts it on screen with
per-pixel alpha. Event-driven: no timers run while nothing changes.
"""

from __future__ import annotations

import logging
import time
import tkinter as tk
from typing import Callable

from ..config import WidgetConfig
from . import STATE_LABELS
from .layered import LayeredWindow
from .look import DISC, PAD, Look, PanelItem, Zones, render, render_panel, window_size
from .tray import MenuActions

log = logging.getLogger(__name__)

_MARGIN = 8  # gap to the screen edge when shown (logical px)
_FRAME_MS = 15
_SLIDE_S = 0.16
_EXPAND_S = 0.14
_FLASH_S = 0.45

_TITLES: dict[str, tuple[str, str]] = {  # state -> (title, default subtitle)
    "starting": ("Starting…", "Opening the camera"),
    "calibrating": ("Calibrating", "Look at the screen"),
    "active": ("Active", ""),
    "gated": ("Not facing the screen", "Gestures are ignored"),
    "no-face": ("No face", "Watching for you"),
    "low-power": ("No face", "Low-power mode"),
    "paused": ("Off", "Camera off · click to turn on"),
    "camera-error": ("Camera unavailable", "Retrying…"),
}


def _ease(x: float) -> float:
    return 1 - (1 - x) ** 3


class FloatingWidget:
    def __init__(self, root: tk.Tk, config: WidgetConfig, actions: MenuActions,
                 on_moved: Callable[[str, float], None]) -> None:
        self._root = root
        self._cfg = config
        self._a = actions
        self._on_moved = on_moved
        self._scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        self._w, self._h = window_size(self._scale)

        self._state = "starting"
        self._message = ""
        self.hint = ""  # e.g. "Wink left: down · long blink: up"
        self._scrolling: str | None = None
        self._visible = False
        self._hover_zone: str | None = None
        self._inside = False
        self._zones: Zones | None = None

        # Animated values: current and target.
        self._slide = 1.0  # 1 = fully out, 0 = peeking
        self._slide_to = 1.0
        self._expand = 0.0
        self._expand_to = 0.0
        self._flash = 0.0
        self._anim_job: str | None = None
        self._last_frame = 0.0
        self._hide_job: str | None = None
        self._drag: tuple[int, int, int, int] | None = None  # pointer x/y, window x/y
        self._dragging = False
        self._x = self._y = 0
        self._last_look: Look | None = None

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.geometry(f"{self._w}x{self._h}+0+0")
        self._win.withdraw()
        self._win.update_idletasks()
        self._layer = LayeredWindow(self._win.winfo_id())
        for seq, fn in (("<Enter>", self._on_enter), ("<Leave>", self._on_leave),
                        ("<Motion>", self._on_motion), ("<ButtonPress-1>", self._on_press),
                        ("<B1-Motion>", self._on_drag), ("<ButtonRelease-1>", self._on_release),
                        ("<ButtonPress-3>", self._on_menu)):
            self._win.bind(seq, fn)

        self._panel = tk.Toplevel(root)
        self._panel.overrideredirect(True)
        self._panel.withdraw()
        self._panel.update_idletasks()
        self._panel_layer = LayeredWindow(self._panel.winfo_id())
        self._panel_open = False
        self._panel_hover: str | None = None
        self._panel_zones: dict[str, tuple[int, int]] = {}
        self._inside_panel = False
        self._panel_close_job: str | None = None
        for seq, fn in (("<Enter>", self._on_panel_enter), ("<Leave>", self._on_panel_leave),
                        ("<Motion>", self._on_panel_motion),
                        ("<ButtonRelease-1>", self._on_panel_click)):
            self._panel.bind(seq, fn)

    # ------------------------------------------------------------------ public

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        if self._visible:
            return
        self._visible = True
        self._win.deiconify()
        self._layer.raise_to_top()
        self._slide = self._slide_to = 1.0
        self._draw(force=True)
        self._schedule_hide(2.5)

    def hide(self) -> None:
        if self._visible:
            self._close_panel()
            self._visible = False
            self._win.withdraw()

    def apply_config(self, config: WidgetConfig) -> None:
        self._cfg = config
        if self._visible:
            self._draw(force=True)

    def set_state(self, state: str, message: str = "") -> None:
        self._state, self._message = state, message
        if self._calibrating_message():
            self._slide_to, self._expand_to = 1.0, 1.0  # stay out, show the instruction
            self._cancel_hide()
        elif not self._inside:
            self._expand_to = 0.0
            self._schedule_hide(1.2)
        self._animate()

    def set_scrolling(self, direction: str | None) -> None:
        self._scrolling = direction
        if direction:
            self._flash = 1.0
        self._animate()

    def pulse(self) -> None:
        self._flash = 1.0
        self._animate()

    def destroy(self) -> None:
        for w in (self._panel, self._win):
            try:
                w.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ drawing

    def _px(self, logical: float) -> int:
        return round(logical * self._scale)

    def _calibrating_message(self) -> bool:
        return self._state == "calibrating" and bool(self._message)

    def _texts(self) -> tuple[str, str]:
        if self._scrolling:
            return f"Scrolling {self._scrolling}", "Open your eye to stop"
        title, sub = _TITLES.get(self._state, (STATE_LABELS.get(self._state, self._state), ""))
        if self._state == "active":
            sub = self.hint or "Watching for gestures"
        elif self._state == "gated" and self._message:
            sub = f"Gestures ignored: {self._message}"
        elif self._state in ("calibrating", "camera-error") and self._message:
            sub = self._message
        return title, sub

    def _look(self) -> Look:
        title, sub = self._texts()
        return Look(
            state=self._state, title=title, subtitle=sub, edge=self._cfg.edge,
            expand=round(_ease(self._expand), 3),
            hover=self._hover_zone if self._expand >= 0.95 or self._hover_zone == "main" else None,
            paused=self._a.is_paused(), scrolling=self._scrolling,
            flash=round(self._flash, 2),
        )

    def _position(self) -> tuple[int, int]:
        sw, sh = self._root.winfo_screenwidth(), self._root.winfo_screenheight()
        pad, disc = self._px(PAD), self._px(DISC)
        peek = min(max(self._px(self._cfg.peek_px), 4), disc)
        if self._cfg.edge == "left":
            shown_x, hidden_x = self._px(_MARGIN) - pad, peek - pad - disc
        else:
            shown_x = sw - self._px(_MARGIN) - (self._w - pad)
            hidden_x = sw - peek - (self._w - pad - disc)
        x = round(hidden_x + (shown_x - hidden_x) * _ease(self._slide))
        y = int(self._cfg.y_fraction * sh - self._h / 2)
        return x, min(max(y, -pad), sh - self._h + pad)

    def _draw(self, force: bool = False) -> None:
        if not self._visible:
            return
        look = self._look()
        if not self._dragging:
            self._x, self._y = self._position()
        if force or look != self._last_look:
            image, self._zones = render(look, self._scale)
            self._layer.show(image, self._x, self._y)
            self._last_look = look
        else:
            self._layer.move(self._x, self._y)

    # ------------------------------------------------------------------ animation

    def _animate(self) -> None:
        if self._anim_job is None and self._visible:
            self._last_frame = time.monotonic()
            self._anim_job = self._root.after(0, self._tick)

    def _tick(self) -> None:
        self._anim_job = None
        now = time.monotonic()
        dt, self._last_frame = now - self._last_frame, now

        def approach(value: float, target: float, seconds: float) -> float:
            step = dt / seconds
            return min(value + step, target) if value < target else max(value - step, target)

        self._slide = approach(self._slide, self._slide_to, _SLIDE_S)
        self._expand = approach(self._expand, self._expand_to, _EXPAND_S)
        if not self._scrolling:
            self._flash = max(0.0, self._flash - dt / _FLASH_S)
        self._draw()
        busy = (self._slide != self._slide_to or self._expand != self._expand_to
                or (self._flash > 0 and not self._scrolling))
        if busy:
            self._anim_job = self._root.after(_FRAME_MS, self._tick)

    def _schedule_hide(self, delay_s: float | None = None) -> None:
        self._cancel_hide()
        delay = self._cfg.hide_delay_s if delay_s is None else delay_s
        self._hide_job = self._root.after(int(delay * 1000), self._auto_hide)

    def _cancel_hide(self) -> None:
        if self._hide_job:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None

    def _auto_hide(self) -> None:
        self._hide_job = None
        if self._inside or self._dragging or self._panel_open or self._calibrating_message():
            return
        self._expand_to, self._slide_to = 0.0, 0.0
        self._animate()

    # ------------------------------------------------------------------ mouse

    def _zone(self, x: int) -> str | None:
        return self._zones.at(x) if self._zones else None

    def _on_enter(self, e: tk.Event) -> None:
        self._inside = True
        self._cancel_hide()
        if self._panel_close_job:
            self._root.after_cancel(self._panel_close_job)
            self._panel_close_job = None
        self._slide_to, self._expand_to = 1.0, 1.0
        self._hover_zone = self._zone(e.x)
        self._animate()

    def _on_leave(self, _: tk.Event) -> None:
        if self._dragging:
            return
        self._inside = False
        self._hover_zone = None
        if self._panel_open:
            self._schedule_panel_close()  # closes unless the mouse moves into the panel
            self._draw()
            return
        if not self._calibrating_message():
            self._expand_to = 0.0
            self._schedule_hide()
        self._animate()

    def _on_motion(self, e: tk.Event) -> None:
        zone = self._zone(e.x)
        if zone != self._hover_zone and not self._dragging:
            self._hover_zone = zone
            self._draw()

    def _on_press(self, e: tk.Event) -> None:
        self._drag = (e.x_root, e.y_root, self._x, self._y)
        self._press_zone = self._zone(e.x)
        self._dragging = False

    def _on_drag(self, e: tk.Event) -> None:
        if self._drag is None or self._press_zone != "main":
            return
        x0, y0, wx, wy = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self._dragging and abs(dx) + abs(dy) < 6:
            return
        if not self._dragging:
            self._dragging = True
            self._close_panel()
            self._expand = self._expand_to = 0.0
        self._x, self._y = wx + dx, wy + dy
        self._draw()

    def _on_release(self, e: tk.Event) -> None:
        start, self._drag = self._drag, None
        if self._dragging:
            self._dragging = False
            sw, sh = self._root.winfo_screenwidth(), self._root.winfo_screenheight()
            self._cfg.edge = "left" if e.x_root < sw / 2 else "right"
            self._cfg.y_fraction = min(max((self._y + self._h / 2) / sh, 0.03), 0.97)
            self._on_moved(self._cfg.edge, self._cfg.y_fraction)
            self._slide_to = 1.0
            self._expand_to = 1.0 if self._inside else 0.0
            self._last_look = None
            self._animate()
            return
        if start is None:
            return
        zone = self._zone(e.x)
        if zone in ("main", "body", "toggle"):
            self._a.toggle()
            self._flash = 0.6
        elif zone == "recal":
            self._a.recalibrate()
        elif zone == "menu":
            self._popup_menu(e.x_root, e.y_root)
        self._root.after(60, self._draw)  # reflect the new paused state

    def _on_menu(self, e: tk.Event) -> None:
        self._popup_menu(e.x_root, e.y_root)

    # ------------------------------------------------------------------ settings panel
    # A dark panel in the same style, opened from the gear button or by right-clicking.
    # Mouse-only (the window never takes focus): click a row, or move the mouse away.

    def _panel_items(self) -> tuple[PanelItem, ...]:
        a = self._a
        return (
            PanelItem("toggle", "Turn on" if a.is_paused() else "Turn off (camera off)",
                      "power"),
            PanelItem("recal", "Recalibrate", "refresh"),
            PanelItem(""),
            PanelItem("autostart", "Start with Windows", "startup",
                      switch=a.autostart_enabled()),
            PanelItem("open", "Open settings file", "file"),
            PanelItem("reload", "Reload settings", "sync"),
            PanelItem("hide", "Hide this button", "eye_off"),
            PanelItem(""),
            PanelItem("quit", "Quit Blink Scroll", "close", danger=True),
        )

    def _popup_menu(self, _x: int = 0, _y: int = 0) -> None:
        if self._panel_open:
            self._close_panel()
            return
        self._panel_open = True
        self._panel_hover = None
        self._cancel_hide()
        self._slide_to, self._expand_to = 1.0, 1.0
        self._animate()
        self._panel.deiconify()
        self._panel_layer.raise_to_top()
        self._draw_panel()

    def _draw_panel(self) -> None:
        items = self._panel_items()
        image, self._panel_zones = render_panel(items, self._panel_hover, self._scale)
        pw, ph = image.size
        sw, sh = self._root.winfo_screenwidth(), self._root.winfo_screenheight()
        x, y = self._position() if not self._dragging else (self._x, self._y)
        overlap = self._px(PAD) * 2 - self._px(4)
        px = x + self._w - pw if self._cfg.edge == "right" else x
        py = y + self._h - overlap
        if py + ph > sh:  # no room below: open above the pill
            py = y - ph + overlap
        self._panel.geometry(f"{pw}x{ph}+{px}+{py}")
        self._panel_layer.show(image, px, py)

    def _close_panel(self) -> None:
        if not self._panel_open:
            return
        self._panel_open = False
        self._panel_hover = None
        self._inside_panel = False  # no Leave event comes when it closes under the mouse
        self._panel.withdraw()
        if not self._inside:
            self._expand_to = 0.0
            self._schedule_hide()
            self._animate()

    def _panel_zone(self, y: int) -> str | None:
        for key, (y0, y1) in self._panel_zones.items():
            if y0 <= y < y1:
                return key
        return None

    def _on_panel_enter(self, _: tk.Event) -> None:
        self._inside_panel = True
        if self._panel_close_job:
            self._root.after_cancel(self._panel_close_job)
            self._panel_close_job = None

    def _on_panel_leave(self, _: tk.Event) -> None:
        self._inside_panel = False
        self._panel_hover = None
        if self._panel_open:
            self._draw_panel()
        self._schedule_panel_close()

    def _schedule_panel_close(self) -> None:
        if self._panel_close_job:
            self._root.after_cancel(self._panel_close_job)

        def close() -> None:
            self._panel_close_job = None
            if not self._inside_panel and not self._inside:
                self._close_panel()
        self._panel_close_job = self._root.after(600, close)

    def _on_panel_motion(self, e: tk.Event) -> None:
        zone = self._panel_zone(e.y)
        if zone != self._panel_hover:
            self._panel_hover = zone
            self._draw_panel()

    def _on_panel_click(self, e: tk.Event) -> None:
        key = self._panel_zone(e.y)
        a = self._a
        actions = {"toggle": a.toggle, "recal": a.recalibrate, "open": a.open_config,
                   "reload": a.reload_config, "hide": a.toggle_widget, "quit": a.quit}
        if key == "autostart":
            a.toggle_autostart()
            self._root.after(400, lambda: self._panel_open and self._draw_panel())
            return  # keep the panel open so the switch can be seen flipping
        self._close_panel()
        if key in actions:
            actions[key]()
            self._root.after(60, lambda: self._draw(force=True))
