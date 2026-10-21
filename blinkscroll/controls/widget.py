"""Floating on/off circle docked to a screen edge (Tk, runs on the main thread).

* Auto-hides: slides mostly off-screen, leaving a ``peek_px`` sliver; slides out when the mouse
  touches it (Enter/Leave events, so no polling while idle).
* Left-click toggles on/off, drag moves it along the edge (drop it on the other half of the screen
  to switch edges), right-click opens the menu.
* Colour = state (same as the tray icon). A short pulse confirms each scroll trigger, and a small
  label next to it shows calibration instructions.
"""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Callable

from ..config import WidgetConfig
from . import STATE_COLORS, STATE_LABELS
from .tray import MenuActions

log = logging.getLogger(__name__)

_KEY = "#010203"  # transparent colour key
_MARGIN = 6  # gap to the screen edge when fully shown
_SLIDE_STEPS = 6
_SLIDE_MS = 14


class FloatingWidget:
    def __init__(self, root: tk.Tk, config: WidgetConfig, actions: MenuActions,
                 on_moved: Callable[[str, float], None]) -> None:
        self._root = root
        self._cfg = config
        self._a = actions
        self._on_moved = on_moved
        self._state = "starting"
        self._message = ""
        self._shown = True  # slid out (vs. peeking)
        self._visible = False  # window mapped at all
        self._hover = False
        self._hide_job: str | None = None
        self._slide_job: str | None = None
        self._drag_start: tuple[int, int, int, int] | None = None
        self._dragging = False

        self._scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.attributes("-transparentcolor", _KEY)
        self._win.configure(bg=_KEY)
        self._canvas = tk.Canvas(self._win, highlightthickness=0, bd=0, bg=_KEY, cursor="hand2")
        self._canvas.pack()
        self._toast = tk.Toplevel(root)
        self._toast.overrideredirect(True)
        self._toast.attributes("-topmost", True)
        self._toast_label = tk.Label(self._toast, bg="#111827", fg="white", padx=10, pady=6,
                                     font=("Segoe UI", 10))
        self._toast_label.pack()
        self._toast.withdraw()
        self._menu = tk.Menu(root, tearoff=False)

        c = self._canvas
        c.bind("<Enter>", self._on_enter)
        c.bind("<Leave>", self._on_leave)
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<ButtonPress-3>", self._on_menu)
        self._x = 0
        self._y = 0
        self.apply_config(config)
        self._win.withdraw()

    # ------------------------------------------------------------------ public

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        if not self._visible:
            self._visible = True
            self._win.deiconify()
            self._win.attributes("-topmost", True)
            self._slide(shown=True, animate=False)
            self._schedule_hide(2.5)

    def hide(self) -> None:
        if self._visible:
            self._visible = False
            self._win.withdraw()
            self._toast.withdraw()

    def apply_config(self, config: WidgetConfig) -> None:
        self._cfg = config
        size = self._px(config.size)
        self._canvas.configure(width=size, height=size)
        self._redraw()
        self._place(self._target_x(self._shown), self._edge_y())

    def set_state(self, state: str, message: str = "") -> None:
        self._state, self._message = state, message
        self._redraw()
        if state == "calibrating" and message:
            self._slide(shown=True)  # stay out while giving instructions
            self._show_toast(message)
        else:
            self._toast.withdraw()
            if state == "calibrating":
                return
            if not self._hover:
                self._schedule_hide(1.0)

    def pulse(self) -> None:
        """Brief flash to confirm a scroll, without sliding the circle out."""
        self._redraw(color="#ffffff")
        self._root.after(160, self._redraw)

    def destroy(self) -> None:
        for w in (self._toast, self._win):
            try:
                w.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------ drawing

    def _redraw(self, color: str | None = None) -> None:
        c = self._canvas
        c.delete("all")
        s = self._px(self._cfg.size)
        fill = color or STATE_COLORS.get(self._state, "#6b7280")
        c.create_oval(1, 1, s - 1, s - 1, fill=fill, outline="#f9fafb", width=2)
        cx = cy = s / 2
        if self._state == "paused":
            bw, bh, gap = s * 0.1, s * 0.36, s * 0.08
            for x in (cx - gap - bw, cx + gap):
                c.create_rectangle(x, cy - bh / 2, x + bw, cy + bh / 2, fill="white", width=0)
        else:  # an eye
            w, h = s * 0.56, s * 0.3
            c.create_oval(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, fill="white", width=0)
            r = h * 0.38
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#111827", width=0)

    # ------------------------------------------------------------------ geometry

    def _px(self, logical: float) -> int:
        """Logical (100%-scale) pixels -> physical pixels; the process is DPI-aware."""
        return max(4, round(logical * self._scale))

    def _screen(self) -> tuple[int, int]:
        return self._root.winfo_screenwidth(), self._root.winfo_screenheight()

    def _target_x(self, shown: bool) -> int:
        sw, _ = self._screen()
        s = self._px(self._cfg.size)
        peek = min(max(self._px(self._cfg.peek_px), 4), s)
        if self._cfg.edge == "left":
            return self._px(_MARGIN) if shown else peek - s
        return sw - s - self._px(_MARGIN) if shown else sw - peek

    def _edge_y(self) -> int:
        _, sh = self._screen()
        s = self._px(self._cfg.size)
        y = int(self._cfg.y_fraction * sh - s / 2)
        return min(max(y, 0), sh - s)

    def _place(self, x: int, y: int) -> None:
        self._x, self._y = x, y
        self._win.geometry(f"+{x}+{y}")

    def _slide(self, shown: bool, animate: bool = True) -> None:
        self._shown = shown
        if self._slide_job:
            self._root.after_cancel(self._slide_job)
            self._slide_job = None
        target, y = self._target_x(shown), self._edge_y()
        if not animate:
            self._place(target, y)
            return
        start = self._x

        def step(i: int) -> None:
            frac = i / _SLIDE_STEPS
            ease = 1 - (1 - frac) ** 3
            self._place(round(start + (target - start) * ease), y)
            if i < _SLIDE_STEPS:
                self._slide_job = self._root.after(_SLIDE_MS, step, i + 1)
            else:
                self._slide_job = None
        step(1)

    def _schedule_hide(self, delay_s: float | None = None) -> None:
        if self._hide_job:
            self._root.after_cancel(self._hide_job)
        delay = self._cfg.hide_delay_s if delay_s is None else delay_s
        self._hide_job = self._root.after(int(delay * 1000), self._auto_hide)

    def _auto_hide(self) -> None:
        self._hide_job = None
        if self._hover or self._dragging or (self._state == "calibrating" and self._message):
            return
        self._slide(shown=False)

    def _show_toast(self, text: str) -> None:
        if not self._visible:
            return
        self._toast_label.configure(text=text)
        self._toast.update_idletasks()
        tw, th = self._toast.winfo_reqwidth(), self._toast.winfo_reqheight()
        s = self._px(self._cfg.size)
        x = self._target_x(True) - tw - 8 if self._cfg.edge == "right" else self._target_x(True) + s + 8
        y = self._edge_y() + (s - th) // 2
        self._toast.geometry(f"+{x}+{y}")
        self._toast.deiconify()
        self._toast.attributes("-topmost", True)

    # ------------------------------------------------------------------ mouse

    def _on_enter(self, _: tk.Event) -> None:
        self._hover = True
        if self._hide_job:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None
        if not self._shown:
            self._slide(shown=True)

    def _on_leave(self, _: tk.Event) -> None:
        self._hover = False
        if not self._dragging:
            self._schedule_hide()

    def _on_press(self, e: tk.Event) -> None:
        self._drag_start = (e.x_root, e.y_root, self._x, self._y)
        self._dragging = False

    def _on_drag(self, e: tk.Event) -> None:
        if self._drag_start is None:
            return
        x0, y0, wx, wy = self._drag_start
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self._dragging and abs(dx) + abs(dy) < 5:
            return
        self._dragging = True
        self._place(wx + dx, wy + dy)

    def _on_release(self, e: tk.Event) -> None:
        start, self._drag_start = self._drag_start, None
        if not self._dragging:
            if start is not None:
                self._a.toggle()
            return
        self._dragging = False
        sw, sh = self._screen()
        s = self._px(self._cfg.size)
        self._cfg.edge = "left" if e.x_root < sw / 2 else "right"
        self._cfg.y_fraction = min(max((self._y + s / 2) / sh, 0.0), 1.0)
        self._slide(shown=True)
        self._on_moved(self._cfg.edge, self._cfg.y_fraction)
        self._schedule_hide()

    def _on_menu(self, e: tk.Event) -> None:
        a = self._a
        m = self._menu
        m.delete(0, "end")
        m.add_command(label=STATE_LABELS.get(self._state, self._state), state="disabled")
        m.add_separator()
        m.add_command(label="Resume" if a.is_paused() else "Pause", command=a.toggle)
        m.add_command(label="Recalibrate", command=a.recalibrate)
        m.add_command(label="Hide circle (tray menu brings it back)", command=a.toggle_widget)
        m.add_separator()
        m.add_command(label="Reload config", command=a.reload_config)
        m.add_command(label="Open config file", command=a.open_config)
        self._autostart_var = tk.BooleanVar(value=a.autostart_enabled())  # keep a reference
        m.add_checkbutton(label="Start with Windows", command=a.toggle_autostart,
                          variable=self._autostart_var)
        m.add_separator()
        m.add_command(label="Quit", command=a.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
