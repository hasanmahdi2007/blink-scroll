"""MediaPipe FaceLandmarker wrapper: BGR frame -> Sample (blink scores + head pose).

Uses VIDEO running mode: inference runs synchronously on the processing thread, which only ever
picks up the newest camera frame, so there is no internal queue or callback thread to manage.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

from .pose import euler_from_matrix
from .samples import Sample

log = logging.getLogger(__name__)


class LandmarkExtractor:
    def __init__(self, model_path: Path, inference_width: int = 320) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model not found at {model_path}. Run: python -m blinkscroll.download_model"
            )
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._width = inference_width
        self._last_ts_ms = -1

    def process(self, image_bgr: np.ndarray, t: float) -> Sample:
        h, w = image_bgr.shape[:2]
        if w > self._width:
            scale = self._width / w
            image_bgr = cv2.resize(image_bgr, (self._width, round(h * scale)),
                                   interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        ts_ms = max(int(t * 1000), self._last_ts_ms + 1)  # must be strictly increasing
        self._last_ts_ms = ts_ms
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms
        )
        if not result.face_blendshapes:
            return Sample(t=t, face=False)
        scores = {c.category_name: c.score for c in result.face_blendshapes[0]}
        yaw = pitch = roll = 0.0
        if result.facial_transformation_matrixes:
            yaw, pitch, roll = euler_from_matrix(result.facial_transformation_matrixes[0])
        return Sample(
            t=t,
            face=True,
            left=scores.get("eyeBlinkLeft", 0.0),
            right=scores.get("eyeBlinkRight", 0.0),
            yaw=yaw,
            pitch=pitch,
            roll=roll,
            look_down=(scores.get("eyeLookDownLeft", 0.0) + scores.get("eyeLookDownRight", 0.0)) / 2,
        )

    def close(self) -> None:
        self._landmarker.close()
