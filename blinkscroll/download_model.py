"""One-time download of the pretrained FaceLandmarker model (the app's only network access)."""

from __future__ import annotations

import logging
import sys
import urllib.request

from .config import DEFAULT_MODEL_PATH

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

log = logging.getLogger(__name__)


def ensure_model() -> None:
    if DEFAULT_MODEL_PATH.exists():
        log.info("Model already present at %s", DEFAULT_MODEL_PATH)
        return
    DEFAULT_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEFAULT_MODEL_PATH.with_suffix(".part")
    log.info("Downloading %s", MODEL_URL)
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.replace(DEFAULT_MODEL_PATH)
    log.info("Saved model to %s", DEFAULT_MODEL_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        ensure_model()
    except OSError as exc:
        log.error("Download failed: %s", exc)
        sys.exit(1)
