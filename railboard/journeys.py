"""Journey filtering and time helpers for the 'next train to X' pages."""
from __future__ import annotations

from datetime import datetime, timedelta

from .api import Departure


def filter_to_target(
    departures: list[Departure], target_crs: str, match: str = "any"
) -> list[Departure]:
    """Departures serving target_crs, keeping API order (already time-sorted).

    match="any"         -> destination is target OR train calls at target
    match="destination" -> only trains terminating at target
    """
    target_crs = target_crs.upper()
    if match == "destination":
        return [d for d in departures if d.destination_crs.upper() == target_crs]
    return [d for d in departures if d.calls_at(target_crs)]


def next_service_to(
    departures: list[Departure], target_crs: str, match: str = "any"
) -> Departure | None:
    matches = filter_to_target(departures, target_crs, match)
    return matches[0] if matches else None


def minutes_until(time_str: str, now: datetime | None = None) -> int | None:
    """Whole minutes from now until a 'HH:MM' local time, handling midnight rollover.

    Returns None if time_str isn't a valid HH:MM.
    """
    if not time_str or len(time_str) != 5 or time_str[2] != ":":
        return None
    try:
        hh, mm = int(time_str[:2]), int(time_str[3:])
    except ValueError:
        return None
    now = now or datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delta = target - now
    # If it looks like it's well in the past, assume it's tomorrow's board wrap.
    if delta < timedelta(minutes=-90):
        target += timedelta(days=1)
        delta = target - now
    return int(delta.total_seconds() // 60)


def countdown_text(dep: Departure, now: datetime | None = None) -> str:
    """'due' / 'N min' style countdown based on expected (or scheduled) time."""
    if dep.cancelled or dep.status == "Cancelled":
        return "cancelled"
    mins = minutes_until(dep.expected, now)
    if mins is None:
        return "--"
    if mins <= 0:
        return "due"
    if mins == 1:
        return "1 min"
    return f"{mins} min"
