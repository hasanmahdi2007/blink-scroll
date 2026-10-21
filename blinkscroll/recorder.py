"""--record: numeric score time series to CSV (never images), plus a reader for replaying them."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import IO, Iterator

from .eyes import EyeFrame, Thresholds
from .samples import Sample

log = logging.getLogger(__name__)

COLUMNS = [
    "t", "face", "raw_left", "raw_right", "yaw", "pitch", "roll", "look_down",
    "smooth_left", "smooth_right", "closed_left", "closed_right",
    "closed_thr_left", "closed_thr_right", "open_thr_left", "open_thr_right",
    "state", "events",
]


class CsvRecorder:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file: IO[str] = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(COLUMNS)
        self._rows = 0
        log.info("Recording scores to %s", path)

    def write(self, sample: Sample, frame: EyeFrame, thresholds: Thresholds | None, state: str,
              events: list[str]) -> None:
        th = thresholds
        thr_cols = [f"{v:.4f}" for v in th.closed_thr + th.open_thr] if th else [""] * 4
        self._writer.writerow([
            f"{sample.t:.4f}", int(sample.face), f"{sample.left:.4f}", f"{sample.right:.4f}",
            f"{sample.yaw:.2f}", f"{sample.pitch:.2f}", f"{sample.roll:.2f}", f"{sample.look_down:.3f}",
            f"{frame.smooth[0]:.4f}", f"{frame.smooth[1]:.4f}",
            int(frame.closed[0]), int(frame.closed[1]),
            *thr_cols,
            state, ";".join(events),
        ])
        self._rows += 1
        if self._rows % 100 == 0:
            self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
            log.info("Saved %d rows to %s", self._rows, self.path)


def read_samples(path: Path) -> Iterator[Sample]:
    """Replay a recording as Samples (raw scores and pose only; derived columns are ignored)."""
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            yield Sample(
                t=float(row["t"]), face=row["face"] == "1",
                left=float(row["raw_left"]), right=float(row["raw_right"]),
                yaw=float(row["yaw"]), pitch=float(row["pitch"]), roll=float(row["roll"]),
                look_down=float(row.get("look_down") or 0.0),
            )
