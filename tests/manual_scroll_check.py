"""Manual/integration check that the Scroller really scrolls the window under the cursor.

Opens a Tk window full of text right under the mouse pointer, sends a scroll through the real
OS input path (pynput -> SendInput), and reports how far the text moved. Not collected by
pytest because it needs a desktop session: run ``python -m tests.manual_scroll_check``.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk

from pynput.mouse import Controller

from blinkscroll.actions import Scroller
from blinkscroll.config import ScrollConfig


def main() -> int:
    root = tk.Tk()
    root.title("Blink Scroll - scroll check")
    x, y = Controller().position
    root.geometry(f"420x300+{int(x) - 210}+{int(y) - 150}")
    root.attributes("-topmost", True)
    text = tk.Text(root, font=("Segoe UI", 10))
    text.pack(fill="both", expand=True)
    text.insert("end", "\n".join(f"line {i}" for i in range(2000)))
    root.update()
    time.sleep(0.3)
    root.update()
    deltas: list[int] = []
    text.bind("<MouseWheel>", lambda e: deltas.append(e.delta), add="+")
    px, py = root.winfo_pointerxy()
    print(f"pynput pointer {x},{y}  tk pointer {px},{py}  window {root.winfo_rootx()},"
          f"{root.winfo_rooty()} {root.winfo_width()}x{root.winfo_height()}  "
          f"under pointer: {root.winfo_containing(px, py)}")

    ok = True
    for label, cfg in (("whole notches", ScrollConfig(amount=5, steps=5)),
                       ("sub-notch steps", ScrollConfig(amount=5, steps=10))):
        scroller = Scroller(cfg)
        for direction in ("down", "up"):
            before = text.index("@0,0")
            t0 = time.perf_counter()
            scroller.scroll(direction)
            while time.perf_counter() - t0 < 0.6:  # let Tk process the wheel events
                root.update()
                time.sleep(0.01)
            after = text.index("@0,0")
            moved = int(float(after)) - int(float(before))
            good = moved > 0 if direction == "down" else moved < 0
            ok &= good
            print(f"{label:16s} {direction:4s}: top line {before} -> {after} "
                  f"({moved:+d} lines) {'OK' if good else 'FAIL'}  wheel deltas seen: {deltas}")
            deltas.clear()
        scroller.close()
    root.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
