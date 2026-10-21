"""Head pose (yaw, pitch, roll in degrees) from MediaPipe's facial transformation matrix."""

from __future__ import annotations

import math

import numpy as np


def euler_from_matrix(matrix: np.ndarray) -> tuple[float, float, float]:
    """Return (yaw, pitch, roll) in degrees from a 4x4 (or 3x3) transform.

    Facing the camera gives roughly (0, 0, 0). Yaw is turning left/right, pitch is nodding
    up/down, roll is tilting the head toward a shoulder. The rotation part is normalised first
    because MediaPipe's matrix can carry a uniform scale.
    """
    r = np.asarray(matrix, dtype=float)[:3, :3]
    norms = np.linalg.norm(r, axis=0)
    norms[norms == 0] = 1.0
    r = r / norms
    sy = math.hypot(r[0, 0], r[1, 0])
    if sy > 1e-6:
        pitch = math.atan2(r[2, 1], r[2, 2])
        yaw = math.atan2(-r[2, 0], sy)
        roll = math.atan2(r[1, 0], r[0, 0])
    else:  # gimbal lock, irrelevant for a face looking at a screen but keep it finite
        pitch = math.atan2(-r[1, 2], r[1, 1])
        yaw = math.atan2(-r[2, 0], sy)
        roll = 0.0
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)
