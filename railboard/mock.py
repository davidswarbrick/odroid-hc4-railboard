"""Synthetic boards for demo/testing (--mock), so the display works with no key.

Boards are tailored to the configured stations and journeys: each journey's origin
gets at least one service that reaches the journey target, so the 'next train' and
combo pages populate.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .api import Board, CallingPoint, Departure
from .config import Config
from .manager import DataStore

_DESTS = [
    ("Somewhere Central", "SWC"),
    ("Northtown", "NTN"),
    ("Riverside", "RVS"),
    ("Eastgate", "EGT"),
]


def _hhmm(now: datetime, plus_min: int) -> str:
    return (now + timedelta(minutes=plus_min)).strftime("%H:%M")


def mock_board(cfg: Config, crs: str, now: datetime) -> Board:
    deps: list[Departure] = []
    # A few generic services with varied status.
    offsets = [3, 9, 16, 24, 33]
    for i, off in enumerate(offsets):
        name, dcrs = _DESTS[i % len(_DESTS)]
        etd = "On time"
        cancelled = False
        if i == 1:
            etd = _hhmm(now, off + 4)  # delayed (expected later)
        if i == 3:
            etd, cancelled = "Cancelled", True
        deps.append(
            Departure(
                std=_hhmm(now, off),
                etd=etd,
                destination=name,
                destination_crs=dcrs,
                platform=str((i % 3) + 1),
                operator="Demo Trains",
                cancelled=cancelled,
                calling_points=[
                    CallingPoint("Midway", "MID", _hhmm(now, off + 5), "On time"),
                    CallingPoint(name, dcrs, _hhmm(now, off + 12), "On time"),
                ],
            )
        )

    # Guarantee each journey from this origin has a matching service.
    for j in cfg.get("journeys", []):
        if j.get("origin") != crs:
            continue
        target = j["target"]
        tname = j.get("target_name", target)
        deps.append(
            Departure(
                std=_hhmm(now, 6),
                etd="On time",
                destination=tname,
                destination_crs=target,
                platform="2",
                operator="Demo Trains",
                calling_points=[
                    CallingPoint("Firststop", "FST", _hhmm(now, 9), "On time"),
                    CallingPoint(tname, target, _hhmm(now, 14), "On time"),
                ],
            )
        )

    deps.sort(key=lambda d: d.std)
    return Board(
        crs=crs.upper(),
        location_name=cfg.station_name(crs),
        generated_at=now.isoformat(),
        departures=deps,
        messages=[],
    )


def build_mock_store(cfg: Config, now: datetime | None = None) -> DataStore:
    now = now or datetime.now()
    store = DataStore()
    for crs in cfg.station_crs_list():
        store.update(crs, mock_board(cfg, crs, now), None)
    return store
