"""Entry point: ``python -m blinkscroll [--debug] [--record FILE] [--config FILE]``."""

from __future__ import annotations

import argparse
import logging
import os
import signal

os.environ.setdefault("GLOG_minloglevel", "2")  # silence MediaPipe/TFLite start-up chatter
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
import threading
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .pipeline import Pipeline

log = logging.getLogger("blinkscroll")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="blinkscroll", description="Hands-free blink scrolling")
    p.add_argument("--debug", action="store_true", help="show the preview window and log events")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config file (TOML)")
    p.add_argument("--stats", action="store_true", help="log CPU use and FPS every 10 s")
    p.add_argument("--duration", type=float, default=0.0, help=argparse.SUPPRESS)  # for measuring
    return p.parse_args(argv)


def _stats_loop(pipeline: Pipeline, stop: threading.Event) -> None:
    import psutil

    proc = psutil.Process()
    proc.cpu_percent(None)
    ncpu = psutil.cpu_count() or 1
    while not stop.wait(10.0):
        cpu = proc.cpu_percent(None)
        log.info("stats: CPU %.1f%% of one core (%.1f%% of total), FPS %.1f",
                 cpu, cpu / ncpu, pipeline.fps.fps)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    config = load_config(args.config)
    stop = threading.Event()
    pipeline = Pipeline(config, debug=args.debug)
    pipeline.on_debug_closed = stop.set
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    pipeline.start()
    if args.stats:
        threading.Thread(target=_stats_loop, args=(pipeline, stop), daemon=True).start()
    if args.duration > 0:
        threading.Timer(args.duration, stop.set).start()
    log.info("Blink Scroll running. Ctrl+C to quit.")
    try:
        while not stop.wait(0.25):  # short waits keep Ctrl+C responsive on Windows
            pass
    finally:
        log.info("Shutting down")
        pipeline.stop()


if __name__ == "__main__":
    main()
