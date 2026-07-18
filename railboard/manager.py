"""Data polling + screen rotation.

DataPoller runs in a background thread and keeps a thread-safe cache of boards.
ScreenManager renders the configured page rotation at a steady frame rate, reading
only the cache so the display never blocks on the network.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import screens, sysinfo
from .api import ApiError, Board, fetch_board
from .config import Config
from .display import Display
from .journeys import next_service_to

log = logging.getLogger("railboard")


@dataclass
class Entry:
    board: Board | None = None
    fetched_at: datetime | None = None
    error: str | None = None


class DataStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, Entry] = {}

    def update(self, crs: str, board: Board | None, error: str | None) -> None:
        with self._lock:
            entry = self._data.setdefault(crs, Entry())
            if board is not None:
                entry.board = board
                entry.fetched_at = datetime.now()
                entry.error = None
            else:
                entry.error = error

    def get(self, crs: str) -> Entry:
        with self._lock:
            return self._data.get(crs, Entry())


class DataPoller(threading.Thread):
    def __init__(self, cfg: Config, store: DataStore, stop: threading.Event):
        super().__init__(name="railboard-poller", daemon=True)
        self.cfg = cfg
        self.store = store
        self.stop = stop
        self.crs_list = cfg.station_crs_list()

    def run(self) -> None:
        interval = int(self.cfg.get("refresh_seconds", 60))
        while not self.stop.is_set():
            for crs in self.crs_list:
                if self.stop.is_set():
                    break
                try:
                    board = fetch_board(self.cfg["api"], self.cfg.api_key, crs)
                    self.store.update(crs, board, None)
                    log.debug("fetched %s: %d services", crs, len(board.departures))
                except ApiError as exc:
                    self.store.update(crs, None, str(exc))
                    log.warning("fetch %s failed: %s", crs, exc)
                except Exception as exc:  # never let the poller die
                    self.store.update(crs, None, f"unexpected: {exc}")
                    log.exception("unexpected error fetching %s", crs)
            self.stop.wait(interval)


def _stale_minutes(entry: Entry, now: datetime) -> int | None:
    if entry.fetched_at is None:
        return None
    return int((now - entry.fetched_at).total_seconds() // 60)


class ScreenManager:
    def __init__(self, cfg: Config, store: DataStore, display: Display):
        self.cfg = cfg
        self.store = store
        self.display = display
        self.fonts = screens.Fonts(cfg["display"])
        self.pages = list(cfg.get("pages", ["health"])) or ["health"]
        self.fps = int(cfg["display"].get("fps", 10))
        self.dwell = float(cfg["display"].get("dwell_seconds", 8))
        self._tick = 0

    # -- page rendering dispatch ----------------------------------------
    def _render_page(self, spec: str, now: datetime):
        size = (self.display.content_width, self.display.content_height)
        if spec == "health":
            health = sysinfo.gather(self.cfg.get("disk_paths"))
            return screens.render_health(size, self.fonts, health, now, self._tick, self.fps)

        kind, _, arg = spec.partition(":")
        if kind == "board":
            crs = arg.upper()
            entry = self.store.get(crs)
            return screens.render_board(
                size, self.fonts, entry.board, self.cfg.station_name(crs),
                now, self._tick, self.fps, _stale_minutes(entry, now),
            )
        if kind == "bigboard":
            crs = arg.upper()
            entry = self.store.get(crs)
            sub = float(self.cfg["display"].get("bigboard_sub_dwell", 3.5))
            return screens.render_bigboard(
                size, self.fonts, entry.board, self.cfg.station_name(crs),
                now, self._tick, self.fps, sub_dwell=sub,
                stale_min=_stale_minutes(entry, now),
            )
        if kind == "next":
            journey = self.cfg.journey(arg)
            if not journey:
                return screens.render_board(size, self.fonts, None, arg, now, self._tick, self.fps)
            entry = self.store.get(journey["origin"])
            dep = None
            if entry.board is not None:
                dep = next_service_to(entry.board.departures, journey["target"], journey.get("match", "any"))
            return screens.render_next_train(
                size, self.fonts, journey, dep, now, self._tick, self.fps,
                have_data=entry.board is not None,
            )
        if kind == "combo":
            entries = []
            for jid in arg.split(","):
                journey = self.cfg.journey(jid.strip())
                if not journey:
                    continue
                board = self.store.get(journey["origin"]).board
                dep = (
                    next_service_to(board.departures, journey["target"], journey.get("match", "any"))
                    if board else None
                )
                entries.append((journey, dep))
            return screens.render_combo(size, self.fonts, entries, now, self._tick, self.fps)

        # Unknown spec: show it so the misconfiguration is visible.
        return screens.render_board(size, self.fonts, None, f"?{spec}", now, self._tick, self.fps)

    # -- main loop -------------------------------------------------------
    def run(self, stop: threading.Event, once: bool = False) -> None:
        frame_interval = 1.0 / max(self.fps, 1)
        idx = 0
        while not stop.is_set():
            spec = self.pages[idx % len(self.pages)]
            self.display.next_cycle()  # advance burn-in orbit per page
            deadline = time.monotonic() + self.dwell
            while not stop.is_set() and time.monotonic() < deadline:
                now = datetime.now()
                if self.display.quiet_state(now) == "blank":
                    self.display.render(_blank(self.display), now)
                    stop.wait(1.0)
                    continue
                frame = self._render_page(spec, now)
                self.display.render(frame, now)
                self._tick += 1
                stop.wait(frame_interval)
            idx += 1
            if once and idx >= len(self.pages):
                break


def _blank(display: Display):
    from PIL import Image

    return Image.new("1", (display.width, display.height), 0)
