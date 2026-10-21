"""Configuration: typed dataclasses with defaults, loaded from (and seeded into) a TOML file."""

from __future__ import annotations

import dataclasses
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import tomli_w

log = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.toml"
DEFAULT_MODEL_PATH = PROJECT_DIR / "models" / "face_landmarker.task"


@dataclass
class CameraConfig:
    index: int = 0
    backend: str = "dshow"  # "dshow", "msmf" or "any"
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass
class ProcessingConfig:
    fps: float = 20.0  # while an eye is closing / a gesture is held, and during calibration
    # While both eyes are simply open. The model costs ~13 ms per frame whatever the image size,
    # so frame rate is what decides CPU use. (At least 15 when double blink is enabled: quick
    # blinks need the frames.)
    idle_fps: float = 8.0
    boost_hold_s: float = 1.0  # stay at full rate this long after the eyes are open again
    inference_width: int = 320  # frames are downscaled to this width before inference
    low_power_after_s: float = 10.0  # no face for this long -> low-power mode
    low_power_fps: float = 4.0
    pause_when_locked: bool = True  # release the camera while Windows is locked
    model_path: str = ""  # empty -> models/face_landmarker.task in the project


@dataclass
class CalibrationConfig:
    open_seconds: float = 3.0  # phase 1: keep eyes open
    closed_seconds: float = 1.5  # phase 2: keep eyes closed (helps with glasses)
    use_closed_phase: bool = True
    reuse_saved: bool = True  # start with the last calibration (no beeps); recalibrate from menu
    # Thresholds as a fraction of the way from the open baseline to the closed level.
    closed_fraction: float = 0.6
    open_fraction: float = 0.3
    # Fallback when the closed phase is skipped or unusable: offsets above the open baseline.
    closed_offset: float = 0.25
    open_offset: float = 0.12
    min_contrast: float = 0.15  # closed level must exceed the open baseline by at least this
    adapt_rate: float = 0.002  # per-frame EMA rate for slow baseline adaptation (0 = off)


@dataclass
class DetectionConfig:
    smoothing_tau_s: float = 0.04  # EMA time constant; 0 = no smoothing
    min_closed_frames: int = 2  # closures shorter than this are noise
    cooldown_s: float = 1.0
    cooldown_after_hold_s: float = 0.4  # shorter: lets you wink again quickly after a hold
    # A held gesture ends only after the eye has been open this long without interruption,
    # so a one-frame dip in the score (glare, model wobble) does not stop the scroll.
    release_s: float = 0.15
    # While a long blink is held, both eyes only need to stay this far closed (it needs
    # long_blink.min_closure to start).
    hold_closure: float = 0.5
    swap_eyes: bool = False  # set true if left/right winks come out reversed


@dataclass
class DoubleBlinkConfig:
    enabled: bool = True
    window_s: float = 0.7  # 2nd blink must end within this long after the 1st one started
    max_blink_s: float = 0.45  # longer closures are not part of a double blink
    # Each blink must close the eyes at least this far from the open to the closed level;
    # deliberate blinks close fully, many natural ones only partly.
    min_closure: float = 0.7
    direction: str = "down"


@dataclass
class LongBlinkConfig:
    enabled: bool = False
    min_duration_s: float = 0.6  # continuous firm closure; natural blinks are ~0.1-0.35 s
    # Both eyes must be at least this far from open to closed. Looking down at the keyboard
    # lowers the lids to ~55-75% for seconds; a real long blink reaches ~100%.
    min_closure: float = 0.85
    direction: str = "down"


@dataclass
class WinkConfig:
    enabled: bool = False
    # The eye that must stay open must be at most this far above its open baseline.
    open_eye_margin: float = 0.1
    # Winks close the eye less than full blinks, so they use their own (lower) thresholds,
    # as a fraction of the way from the open to the closed level. Safe because the other eye
    # must stay open at the same time.
    closed_fraction: float = 0.4
    release_fraction: float = 0.25  # a held wink ends when the eye opens below this
    min_duration_s: float = 0.25
    left_direction: str = "up"  # winking the left eye
    right_direction: str = "down"  # winking the right eye


@dataclass
class GesturesConfig:
    double_blink: DoubleBlinkConfig = field(default_factory=DoubleBlinkConfig)
    long_blink: LongBlinkConfig = field(default_factory=LongBlinkConfig)
    wink: WinkConfig = field(default_factory=WinkConfig)


@dataclass
class HeadPoseConfig:
    """Gestures only count while you face the screen. Angles are measured relative to your
    neutral pose recorded during calibration; gaze uses MediaPipe's eyeLookDown scores."""

    enabled: bool = True
    max_yaw_deg: float = 25.0  # turning left/right
    max_pitch_down_deg: float = 12.0  # head lowered toward the keyboard (pitch goes negative)
    max_pitch_up_deg: float = 25.0
    max_roll_deg: float = 45.0  # tilting toward a shoulder does not cause false blinks
    max_look_down: float = 0.55  # eyes looking down without moving the head (0..1)
    # The neutral pose (from calibration) follows slow posture changes with this time constant.
    neutral_adapt_s: float = 30.0


@dataclass
class ScrollConfig:
    amount: float = 5.0  # wheel notches per trigger (1 notch ~ 3 lines in most apps)
    steps: int = 5  # split into this many wheel events; > amount = sub-notch steps
    duration_s: float = 0.15
    # Hold-to-scroll: keep scrolling while a wink / long blink is held; longer hold = further.
    hold_to_scroll: bool = True
    hold_start_notches_per_s: float = 4.0
    hold_max_notches_per_s: float = 12.0
    hold_ramp_s: float = 2.0  # time to reach full speed
    max_hold_s: float = 20.0  # safety limit


@dataclass
class HotkeysConfig:
    toggle: str = "<ctrl>+<alt>+b"
    quit: str = "<ctrl>+<alt>+q"
    recalibrate: str = "<ctrl>+<alt>+c"


@dataclass
class WidgetConfig:
    enabled: bool = True
    size: int = 44
    edge: str = "right"  # "left" or "right"
    y_fraction: float = 0.5  # vertical position along the edge (remembered after dragging)
    peek_px: int = 10  # how much of the circle stays visible while hidden
    hide_delay_s: float = 1.5


@dataclass
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    gestures: GesturesConfig = field(default_factory=GesturesConfig)
    head_pose: HeadPoseConfig = field(default_factory=HeadPoseConfig)
    scroll: ScrollConfig = field(default_factory=ScrollConfig)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    widget: WidgetConfig = field(default_factory=WidgetConfig)

    @property
    def model_path(self) -> Path:
        return Path(self.processing.model_path) if self.processing.model_path else DEFAULT_MODEL_PATH


T = TypeVar("T")


def _from_dict(cls: type[T], data: dict[str, Any], section: str = "") -> T:
    """Build a (possibly nested) dataclass from a dict, ignoring unknown keys with a warning."""
    defaults = cls()
    kwargs: dict[str, Any] = {}
    names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    for key, value in data.items():
        where = f"{section}.{key}" if section else key
        if key not in names:
            log.warning("Unknown config key %r ignored", where)
            continue
        default = getattr(defaults, key)
        if dataclasses.is_dataclass(default):
            if isinstance(value, dict):
                kwargs[key] = _from_dict(type(default), value, where)
            else:
                log.warning("Config key %r should be a table; using defaults", where)
        elif isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            kwargs[key] = float(value)
        elif type(value) is not type(default):
            log.warning("Config key %r has wrong type (%s); using default %r",
                        where, type(value).__name__, default)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load the config file, writing a commented default one first if it does not exist."""
    if not path.exists():
        save_config(Config(), path)
        log.info("Wrote default config to %s", path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    return _from_dict(Config, data)


def save_config(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    text = tomli_w.dumps(dataclasses.asdict(config))
    header = (
        "# Blink Scroll settings. Edit, then choose 'Reload config' from the tray or circle menu.\n"
        "# Delete this file to restore defaults. See README.md for a tuning guide.\n\n"
    )
    path.write_text(header + text, encoding="utf-8")
