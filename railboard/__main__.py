"""Entry point: wire config -> data poller -> screen rotation.

Examples:
    RDM_API_KEY=xxx python -m railboard                       # real OLED
    python -m railboard --display simulate --mock --once      # render PNGs, no key
    python -m railboard --display emulator                    # desktop preview
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading

from .config import ConfigError, load_config
from .display import Display
from .manager import DataPoller, DataStore, ScreenManager


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="railboard", description="OLED train departure board")
    p.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    p.add_argument(
        "--display", choices=["real", "emulator", "simulate"], default="real",
        help="output backend (default: real OLED hardware)",
    )
    p.add_argument("--mock", action="store_true", help="use synthetic data (no API key needed)")
    p.add_argument("--once", action="store_true", help="show each page once then exit")
    p.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("railboard")

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("config error: %s", exc)
        return 2

    if not args.mock and not cfg.api_key:
        log.error("RDM_API_KEY not set. Export it, or run with --mock for demo data.")
        return 2

    try:
        display = Display(cfg, kind=args.display)
    except Exception as exc:
        log.error("failed to init display (%s backend): %s", args.display, exc)
        return 3

    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    poller = None
    if args.mock:
        from .mock import build_mock_store

        store = build_mock_store(cfg)
        log.info("running with MOCK data")
    else:
        store = DataStore()
        poller = DataPoller(cfg, store, stop)
        poller.start()

    manager = ScreenManager(cfg, store, display)
    log.info("starting rotation over %d page(s): %s", len(manager.pages), manager.pages)
    try:
        manager.run(stop, once=args.once)
    finally:
        stop.set()
        if poller is not None:
            poller.join(timeout=2)
        display.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
