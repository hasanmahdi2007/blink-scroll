from __future__ import annotations

import pytest

from blinkscroll.app import gesture_hint
from blinkscroll.config import Config
from blinkscroll.controls.look import Look, PanelItem, panel_size, render, render_panel, window_size


@pytest.mark.parametrize("edge", ["left", "right"])
@pytest.mark.parametrize("scale", [1.0, 1.25, 2.0])
def test_render_sizes_and_zones(edge: str, scale: float) -> None:
    img, zones = render(Look("active", "Active", "hint", edge=edge, expand=1.0), scale)
    assert img.size == window_size(scale)
    assert img.mode == "RGBA"
    assert set(zones.buttons) == {"toggle", "recal", "menu"}
    # the round button sits at the screen-edge end of the pill
    if edge == "right":
        assert zones.main[1] > max(b[1] for b in zones.buttons.values())
    else:
        assert zones.main[0] < min(b[0] for b in zones.buttons.values())
    for name, (a, b) in zones.buttons.items():
        assert zones.at((a + b) // 2) == name
    assert zones.at(sum(zones.main) // 2) == "main"


def test_collapsed_has_no_buttons_and_corners_are_transparent() -> None:
    img, zones = render(Look("paused", "Off", "", paused=True), 1.25)
    assert zones.buttons == {}
    assert img.getpixel((0, 0))[3] == 0  # click-through outside the shape
    cx = sum(zones.main) // 2
    assert img.getpixel((cx, img.height // 2))[3] > 200  # solid in the middle


def test_render_is_deterministic_for_caching() -> None:
    look = Look("active", "Active", "x", expand=0.5, flash=0.3)
    assert render(look, 1.25)[0].tobytes() == render(look, 1.25)[0].tobytes()


def test_panel_rows_and_size() -> None:
    items = (PanelItem("a", "One", "power"), PanelItem(""), PanelItem("b", "Two", switch=True))
    img, zones = render_panel(items, "b", 1.25)
    assert img.size == panel_size(items, 1.25)
    assert list(zones) == ["a", "b"]
    assert zones["a"][1] <= zones["b"][0]  # separator between them


def test_gesture_hint_reflects_config() -> None:
    cfg = Config()
    cfg.gestures.double_blink.enabled = False
    cfg.gestures.wink.enabled = True
    cfg.gestures.wink.left_direction, cfg.gestures.wink.right_direction = "down", "up"
    cfg.gestures.long_blink.enabled = True
    cfg.gestures.long_blink.direction = "up"
    assert gesture_hint(cfg) == "Left wink ↓ · Right wink ↑ · Long blink ↑"
