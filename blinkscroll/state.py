"""Small persisted runtime state (not settings): last calibration and circle position."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR
from .eyes import Thresholds

log = logging.getLogger(__name__)

STATE_PATH = PROJECT_DIR / "state.json"


class AppState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable %s: %s", path, exc)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----- calibration

    def calibration(self) -> tuple[Thresholds, tuple[float, float, float]] | None:
        c = self.data.get("calibration")
        if not c:
            return None
        try:
            th = Thresholds(
                open_level=tuple(c["open_level"]), closed_level=tuple(c["closed_level"]),
                closed_thr=tuple(c["closed_thr"]), open_thr=tuple(c["open_thr"]),
                method=c["method"],
            )
            return th, tuple(c["neutral"])  # type: ignore[return-value]
        except (KeyError, TypeError):
            return None

    def set_calibration(self, th: Thresholds, neutral: tuple[float, float, float]) -> None:
        self.data["calibration"] = {
            "open_level": th.open_level, "closed_level": th.closed_level,
            "closed_thr": th.closed_thr, "open_thr": th.open_thr, "method": th.method,
            "neutral": neutral,
        }
        self.save()
