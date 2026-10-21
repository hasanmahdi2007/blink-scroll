"""Entry point: ``python -m blinkscroll [--debug] [--record [CSV]] [--config FILE]``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")  # silence MediaPipe/TFLite start-up chatter
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

from .config import DATA_DIR, DEFAULT_CONFIG_PATH  # noqa: E402

log = logging.getLogger("blinkscroll")
LOG_PATH = DATA_DIR / "blinkscroll.log"


def _default_recording() -> Path:
    return DATA_DIR / "recordings" / time.strftime("scores-%Y%m%d-%H%M%S.csv")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="blinkscroll", description="Hands-free blink scrolling")
    p.add_argument("--debug", action="store_true",
                   help="show the preview window and log every blink and trigger")
    p.add_argument("--record", type=Path, metavar="CSV", nargs="?", const=_default_recording(),
                   help="save the numeric score time series (no images) to CSV")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config file (TOML)")
    p.add_argument("--stats", action="store_true", help="log CPU use and FPS every 10 s")
    p.add_argument("--paused", action="store_true", help="start paused (camera off)")
    p.add_argument("--autostart", choices=["on", "off"],
                   help="enable/disable starting with Windows, then exit")
    p.add_argument("--tune", action="store_true",
                   help="guided 3-minute tuning recording (quit the running app first)")
    p.add_argument("--replay", type=Path, metavar="CSV",
                   help="replay a recording with the current settings and print the gestures")
    p.add_argument("--duration", type=float, default=0.0, help=argparse.SUPPRESS)  # measuring
    p.add_argument("--force-low-power", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw.exe (no console)
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    if debug:
        logging.getLogger("blinkscroll").setLevel(logging.DEBUG)


def _stats_loop(app, stop: threading.Event) -> None:  # noqa: ANN001
    import psutil

    proc = psutil.Process()
    proc.cpu_percent(None)
    ncpu = psutil.cpu_count() or 1
    while not stop.wait(10.0):
        cpu = proc.cpu_percent(None)
        p = app.pipeline
        log.info("stats: CPU %.1f%% of one core (%.2f%% of total), FPS %.1f, state %s",
                 cpu, cpu / ncpu, p.fps.fps, p.state)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.debug)
    from . import system

    if args.autostart:
        system.set_autostart(args.autostart == "on")
        return 0
    if args.tune:
        from .guided import main as tune
        return tune()
    if args.replay:
        from .replay import main as replay
        replay([str(args.replay), "--config", str(args.config)])
        return 0

    instance = system.SingleInstance()
    if instance.already_running:
        log.error("Blink Scroll is already running (see the tray icon).")
        return 1
    system.enable_dpi_awareness()

    from .app import App  # heavy imports (mediapipe, cv2) after the cheap checks

    app = App(args.config, debug=args.debug, record=args.record, duration=args.duration,
              start_paused=args.paused)
    app.pipeline.force_low_power = args.force_low_power
    stop_stats = threading.Event()
    if args.stats:
        threading.Thread(target=_stats_loop, args=(app, stop_stats), daemon=True).start()
    try:
        app.run()
    finally:
        stop_stats.set()
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
