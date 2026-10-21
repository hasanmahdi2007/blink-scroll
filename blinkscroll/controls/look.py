"""Renders the floating control with Pillow (anti-aliased, soft shadow, Windows 11 icons).

Pure drawing code: given what to show, returns an RGBA image plus the hit zones of its buttons.
The window code in ``widget.py`` puts the image on screen with per-pixel alpha.

Collapsed it is a round button with a coloured status ring. Expanded (on hover, or while
calibrating) it grows into a pill: status text plus Pause/Resume, Recalibrate and Settings.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import STATE_COLORS

SS = 3  # supersampling factor for anti-aliasing

# Logical (100%-scale) layout, in pixels.
PAD = 12  # room for the shadow
DISC = 44  # collapsed diameter == pill height
TEXT_W = 232
BTN = 32
BTN_GAP = 2
END_GAP = 8
EXPANDED_W = DISC + TEXT_W + 3 * BTN + 2 * BTN_GAP + END_GAP

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points.
ICONS = {
    "eye": "\ue890", "eye_off": "\ued1a", "play": "\ue768", "pause": "\ue769",
    "refresh": "\ue72c", "settings": "\ue713", "down": "\ue74b", "up": "\ue74a",
    "warning": "\ue7ba", "sync": "\ue895", "power": "\ue7e8", "file": "\ue8e5",
    "close": "\ue711", "startup": "\ue7c4",
}

# Settings panel (logical px).
PANEL_W = 256
PANEL_ROW = 36
PANEL_SEP = 9
PANEL_PAD = 6


@dataclass(frozen=True)
class PanelItem:
    key: str  # "" for a separator
    label: str = ""
    icon: str = ""
    switch: bool | None = None  # None = plain action; True/False = on/off switch
    danger: bool = False

_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


@functools.lru_cache(maxsize=32)
def _font(kind: str, px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = {
        "icon": ["SegoeIcons.ttf", "segmdl2.ttf"],
        "title": ["seguisb.ttf", "segoeui.ttf"],
        "text": ["segoeui.ttf"],
    }[kind]
    for name in names:
        path = _FONTS_DIR / name
        if path.exists():
            return ImageFont.truetype(str(path), px)
    return ImageFont.load_default()


def has_icon_font() -> bool:
    return any((_FONTS_DIR / n).exists() for n in ("SegoeIcons.ttf", "segmdl2.ttf"))


@dataclass(frozen=True)
class Look:
    state: str  # key of STATE_COLORS
    title: str
    subtitle: str
    edge: str = "right"  # which screen edge the widget is docked to
    expand: float = 0.0  # 0 = round button, 1 = full pill (animated in between)
    hover: str | None = None  # zone under the mouse: "main", "toggle", "recal", "menu"
    paused: bool = False
    scrolling: str | None = None  # "up"/"down" while a hold-scroll is running
    flash: float = 0.0  # 0..1, brief highlight after a trigger


@dataclass(frozen=True)
class Zones:
    """Hit zones as (x0, x1) in physical window pixels (all span the full pill height)."""
    main: tuple[int, int]
    buttons: dict[str, tuple[int, int]]
    body: tuple[int, int]

    def at(self, x: int) -> str | None:
        for name, (a, b) in self.buttons.items():
            if a <= x < b:
                return name
        if self.main[0] <= x < self.main[1]:
            return "main"
        if self.body[0] <= x < self.body[1]:
            return "body"
        return None


def window_size(scale: float) -> tuple[int, int]:
    return round((EXPANDED_W + 2 * PAD) * scale), round((DISC + 2 * PAD) * scale)


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "â€¦", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "â€¦"


def _glyph(draw: ImageDraw.ImageDraw, cx: float, cy: float, name: str, px: int,
           fill: tuple[int, int, int, int]) -> None:
    if has_icon_font():
        draw.text((cx, cy), ICONS[name], font=_font("icon", px), fill=fill, anchor="mm")
        return
    r = px * 0.35  # fallback: simple shapes
    if name == "pause":
        draw.rectangle((cx - r, cy - r, cx - r * 0.3, cy + r), fill=fill)
        draw.rectangle((cx + r * 0.3, cy - r, cx + r, cy + r), fill=fill)
    else:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=fill, width=max(1, px // 8))


def _blend(img: Image.Image, draw_fn) -> ImageDraw.ImageDraw:
    """Draw semi-transparent shapes on a separate layer and alpha-composite it (drawing
    directly would overwrite the alpha and punch see-through holes). Returns a fresh Draw."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    img.alpha_composite(layer)
    return ImageDraw.Draw(img)


def render(look: Look, scale: float) -> tuple[Image.Image, Zones]:
    k = scale * SS  # logical px -> supersampled px
    win_w, win_h = window_size(scale)
    W, H = win_w * SS, win_h * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    pad, disc = PAD * k, DISC * k
    pill_w = disc + (EXPANDED_W * k - disc) * max(0.0, min(look.expand, 1.0))
    top, bottom = pad, pad + disc
    if look.edge == "right":  # disc at the right end, pill grows to the left
        x1 = W - pad
        x0 = x1 - pill_w
        disc_x0 = x1 - disc
    else:
        x0 = pad
        x1 = x0 + pill_w
        disc_x0 = x0
    radius = disc / 2

    # Soft shadow.
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0, top + 2 * k, x1, bottom + 2 * k), radius=radius, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5 * k)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, top, x1, bottom), radius=radius, fill=(23, 25, 31, 240))
    d = _blend(img, lambda b: b.rounded_rectangle((x0, top, x1, bottom), radius=radius,
                                                  outline=(255, 255, 255, 34),
                                                  width=max(1, round(1 * k))))

    # Status disc: coloured ring, soft glow when active, icon in the middle.
    color = STATE_COLORS.get(look.state, "#6b7280")
    cx, cy = disc_x0 + radius, top + radius
    ring_r = radius - 5 * k
    if look.state in ("active", "gated") or look.flash > 0:
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        g_alpha = int(70 + 120 * look.flash)
        ImageDraw.Draw(glow).ellipse((cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r),
                                     outline=_rgba(color, g_alpha), width=round(4 * k))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(3 * k)))
        d = ImageDraw.Draw(img)
    if look.hover == "main":  # blend (drawing directly would overwrite the alpha)
        hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(hl).ellipse((cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r),
                                   fill=(255, 255, 255, 26))
        img.alpha_composite(hl)
        d = ImageDraw.Draw(img)
    ring_w = round((2.5 + 1.5 * look.flash) * k)
    d.ellipse((cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r),
              outline=_rgba(color), width=ring_w)
    if look.scrolling:
        icon = look.scrolling
    elif look.state == "camera-error":
        icon = "warning"
    elif look.state == "calibrating":
        icon = "sync"
    elif look.paused:
        icon = "eye_off"
    else:
        icon = "eye"
    icon_fill = (255, 255, 255, 255) if not look.paused else (190, 194, 202, 255)
    _glyph(d, cx, cy + 0.5 * k, icon, round(17 * k), icon_fill)

    zones_btn: dict[str, tuple[int, int]] = {}
    inner_w = pill_w - disc
    if inner_w > 4 * k:
        # Contents of the expanded part, clipped to the current (animated) width.
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        btn = BTN * k
        order = ["toggle", "recal", "menu"]
        icons = {"toggle": "play" if look.paused else "pause", "recal": "refresh",
                 "menu": "settings"}
        if look.edge == "right":
            text_x0 = disc_x0 - TEXT_W * k
            bx = text_x0 - 3 * btn - 2 * BTN_GAP * k
            slots = [bx + i * (btn + BTN_GAP * k) for i in range(3)][::-1]
        else:
            text_x0 = disc_x0 + disc + 6 * k
            bx = text_x0 + TEXT_W * k - 6 * k
            slots = [bx + i * (btn + BTN_GAP * k) for i in range(3)]
        for name, sx in zip(order, slots):
            bcx = sx + btn / 2
            if look.hover == name:
                ld.ellipse((bcx - btn / 2 + 2 * k, cy - btn / 2 + 2 * k,
                            bcx + btn / 2 - 2 * k, cy + btn / 2 - 2 * k),
                           fill=(255, 255, 255, 30))
            _glyph(ld, bcx, cy + 0.5 * k, icons[name], round(14 * k), (229, 231, 235, 255))
            zones_btn[name] = (round(sx / SS), round((sx + btn) / SS))
        tx = text_x0 + 4 * k
        max_tw = TEXT_W * k - 12 * k
        title_font, sub_font = _font("title", round(13 * k)), _font("text", round(11 * k))
        ld.text((tx, cy - 1 * k), _ellipsize(ld, look.title, title_font, max_tw),
                font=title_font, fill=(243, 244, 246, 255), anchor="ls")
        ld.text((tx, cy + 3 * k), _ellipsize(ld, look.subtitle, sub_font, max_tw),
                font=sub_font, fill=(156, 163, 175, 255), anchor="lt")
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        if look.edge == "right":
            md.rectangle((x0 + 2 * k, 0, disc_x0, H), fill=255)
        else:
            md.rectangle((disc_x0 + disc, 0, x1 - 2 * k, H), fill=255)
        fade = min(1.0, max(0.0, (look.expand - 0.55) / 0.45))
        alpha = ImageChops.multiply(layer.getchannel("A"), mask)
        layer.putalpha(alpha.point(lambda a: int(a * fade)))
        img.alpha_composite(layer)

    out = img.resize((win_w, win_h), Image.LANCZOS)
    zones = Zones(
        main=(round(disc_x0 / SS), round((disc_x0 + disc) / SS)),
        buttons=zones_btn if look.expand >= 0.95 else {},
        body=(round(x0 / SS), round(x1 / SS)),
    )
    return out, zones


def panel_size(items: tuple[PanelItem, ...], scale: float) -> tuple[int, int]:
    rows = sum(PANEL_SEP if not it.key else PANEL_ROW for it in items)
    return round((PANEL_W + 2 * PAD) * scale), round((rows + 2 * PANEL_PAD + 2 * PAD) * scale)


def render_panel(items: tuple[PanelItem, ...], hover: str | None,
                 scale: float) -> tuple[Image.Image, dict[str, tuple[int, int]]]:
    """Dark settings panel in the same style as the pill. Returns image and row zones
    (key -> (y0, y1) in physical window pixels)."""
    k = scale * SS
    win_w, win_h = panel_size(items, scale)
    W, H = win_w * SS, win_h * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pad = PAD * k
    x0, y0, x1, y1 = pad, pad, W - pad, H - pad
    radius = 12 * k

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 3 * k, x1, y1 + 3 * k), radius=radius,
                                             fill=(0, 0, 0, 120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6 * k)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(23, 25, 31, 245))
    d = _blend(img, lambda b: b.rounded_rectangle((x0, y0, x1, y1), radius=radius,
                                                  outline=(255, 255, 255, 34),
                                                  width=max(1, round(k))))

    zones: dict[str, tuple[int, int]] = {}
    font = _font("text", round(12.5 * k))
    y = y0 + PANEL_PAD * k
    for it in items:
        if not it.key:
            sy = y + PANEL_SEP * k / 2
            d = _blend(img, lambda b, sy=sy: b.line((x0 + 14 * k, sy, x1 - 14 * k, sy),
                                                    fill=(255, 255, 255, 24),
                                                    width=max(1, round(k))))
            y += PANEL_SEP * k
            continue
        row_h = PANEL_ROW * k
        if hover == it.key:
            hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(hl).rounded_rectangle(
                (x0 + 5 * k, y + 1 * k, x1 - 5 * k, y + row_h - 1 * k), radius=7 * k,
                fill=(255, 255, 255, 22))
            img.alpha_composite(hl)
            d = ImageDraw.Draw(img)
        cy = y + row_h / 2
        color = (248, 113, 113, 255) if it.danger else (229, 231, 235, 255)
        if it.icon:
            _glyph(d, x0 + 24 * k, cy + 0.5 * k, it.icon, round(14 * k), color)
        d.text((x0 + 44 * k, cy), it.label, font=font, fill=color, anchor="lm")
        if it.switch is not None:  # pill-shaped on/off switch
            sw_w, sw_h = 34 * k, 18 * k
            sx1 = x1 - 14 * k
            sx0 = sx1 - sw_w
            on = it.switch
            d.rounded_rectangle((sx0, cy - sw_h / 2, sx1, cy + sw_h / 2), radius=sw_h / 2,
                                fill=(34, 197, 94, 255) if on else (75, 85, 99, 255))
            knob = sw_h / 2 - 3 * k
            kx = sx1 - sw_h / 2 if on else sx0 + sw_h / 2
            d.ellipse((kx - knob, cy - knob, kx + knob, cy + knob), fill=(255, 255, 255, 255))
        zones[it.key] = (round(y / SS), round((y + row_h) / SS))
        y += row_h
    return img.resize((win_w, win_h), Image.LANCZOS), zones
