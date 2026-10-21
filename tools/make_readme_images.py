"""Regenerate the README images from the real widget renderer:
    .venv\\Scripts\\python.exe -m tools.make_readme_images
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from blinkscroll.controls.look import Look, PanelItem, render, render_panel

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SCALE = 1.5
HINT = "Left wink ↓ · Right wink ↑ · Long blink ↑"


def _background(w: int, h: int) -> Image.Image:
    """A soft desktop-like gradient."""
    top, bottom = (58, 76, 110), (24, 30, 46)
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line((0, y, w, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def _caption(img: Image.Image, xy: tuple[int, int], text: str) -> None:
    try:
        font = ImageFont.truetype("segoeui.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text(xy, text, font=font, fill=(226, 232, 240, 255), anchor="mt")


def states() -> None:
    looks = [
        (Look("active", "", ""), "Active"),
        (Look("active", "", "", scrolling="down", flash=1.0), "Scrolling"),
        (Look("gated", "", ""), "Not facing screen"),
        (Look("no-face", "", ""), "No face"),
        (Look("calibrating", "", ""), "Calibrating"),
        (Look("paused", "", "", paused=True), "Off (camera off)"),
    ]
    tiles = []
    for look, label in looks:
        img, zones = render(look, SCALE)
        cx = sum(zones.main) // 2
        half = (zones.main[1] - zones.main[0]) // 2 + 22
        tiles.append((img.crop((cx - half, 0, cx + half, img.height)), label))
    cell_w = 190
    pill, _ = render(Look("active", "Active", HINT, expand=1.0, hover="recal"), SCALE)
    w = max(cell_w * len(tiles), pill.width + 80)
    h = tiles[0][0].height + 50 + pill.height + 40
    bg = _background(w, h)
    for i, (tile, label) in enumerate(tiles):
        x = i * cell_w + (cell_w - tile.width) // 2
        bg.alpha_composite(tile, (x, 10))
        _caption(bg, (i * cell_w + cell_w // 2, tile.height + 8), label)
    bg.alpha_composite(pill, ((w - pill.width) // 2, tiles[0][0].height + 50))
    bg.convert("RGB").save(OUT / "button-states.png", optimize=True)


def panel() -> None:
    pill, _ = render(Look("active", "Active", HINT, expand=1.0, hover="menu"), SCALE)
    items = (
        PanelItem("toggle", "Turn off (camera off)", "power"),
        PanelItem("recal", "Recalibrate", "refresh"),
        PanelItem(""),
        PanelItem("autostart", "Start with Windows", "startup", switch=True),
        PanelItem("open", "Open settings file", "file"),
        PanelItem("reload", "Reload settings", "sync"),
        PanelItem("hide", "Hide this button", "eye_off"),
        PanelItem(""),
        PanelItem("quit", "Quit Blink Scroll", "close", danger=True),
    )
    menu, _ = render_panel(items, "autostart", SCALE)
    overlap = round(20 * SCALE)
    w, h = pill.width + 60, pill.height + menu.height - overlap + 40
    bg = _background(w, h)
    bg.alpha_composite(pill, (30, 20))
    bg.alpha_composite(menu, (30 + pill.width - menu.width, 20 + pill.height - overlap))
    bg.convert("RGB").save(OUT / "settings-panel.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    states()
    panel()
    print("wrote", *sorted(p.name for p in OUT.glob("*.png")))
