"""User-facing controls: global hotkeys, tray icon and the floating on/off circle."""

from __future__ import annotations

# State -> colour, shared by the tray icon and the floating circle.
STATE_COLORS: dict[str, str] = {
    "starting": "#3b82f6",
    "calibrating": "#3b82f6",  # blue
    "active": "#22c55e",  # green
    "gated": "#14b8a6",  # teal: face seen but not facing the screen
    "no-face": "#f59e0b",  # amber
    "low-power": "#f59e0b",
    "paused": "#6b7280",  # grey
    "camera-error": "#ef4444",  # red
}

STATE_LABELS: dict[str, str] = {
    "starting": "Starting...",
    "calibrating": "Calibrating",
    "active": "Active",
    "gated": "Active (not facing screen)",
    "no-face": "No face",
    "low-power": "No face (low-power)",
    "paused": "Paused (camera off)",
    "camera-error": "Camera error",
}
