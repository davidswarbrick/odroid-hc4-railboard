"""Rail Data Marketplace (RDM) Live Departure Board REST client.

Endpoint shape (GetDepBoardWithDetails):
    GET {base_url}/{product_prefix}/LDBWS/api/{version}/{operation}/{CRS}?numRows=N
    header: x-apikey: <consumer key>

Returns JSON roughly matching the OpenLDBWS schema; we parse the bits we render.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests


class ApiError(RuntimeError):
    """Raised for any failure fetching or parsing a board (network, auth, shape)."""


@dataclass
class CallingPoint:
    name: str
    crs: str
    st: str = ""          # scheduled time "HH:MM"
    et: str = ""          # estimated: "On time" / "HH:MM" / "Cancelled" / ""


@dataclass
class Departure:
    std: str                          # scheduled departure "HH:MM"
    etd: str                          # "On time" / "Delayed" / "Cancelled" / "HH:MM"
    destination: str                  # destination location name
    destination_crs: str
    platform: str                     # "" if not allocated
    operator: str
    cancelled: bool = False
    calling_points: list[CallingPoint] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Short human status suitable for a board row."""
        if self.cancelled or self.etd.lower() == "cancelled":
            return "Cancelled"
        if not self.etd or self.etd.lower() == "on time":
            return "On time"
        # etd is either "Delayed" or a concrete time
        return self.etd

    @property
    def expected(self) -> str:
        """Best available departure time string (etd if it's a time, else std)."""
        if _looks_like_time(self.etd):
            return self.etd
        return self.std

    def calls_at(self, crs: str) -> bool:
        crs = crs.upper()
        if self.destination_crs.upper() == crs:
            return True
        return any(cp.crs.upper() == crs for cp in self.calling_points)


def _looks_like_time(s: str) -> bool:
    return len(s) == 5 and s[2] == ":" and s[:2].isdigit() and s[3:].isdigit()


@dataclass
class Board:
    crs: str
    location_name: str
    generated_at: str
    departures: list[Departure]
    messages: list[str] = field(default_factory=list)


def build_url(cfg_api: dict[str, Any], crs: str) -> str:
    base = cfg_api["base_url"].rstrip("/")
    prefix = cfg_api["product_prefix"].strip("/")
    version = cfg_api["version"]
    operation = cfg_api["operation"]
    return f"{base}/{prefix}/LDBWS/api/{version}/{operation}/{crs.upper()}"


def fetch_board(cfg_api: dict[str, Any], api_key: str, crs: str) -> Board:
    if not api_key:
        raise ApiError("no API key set (export RDM_API_KEY)")
    url = build_url(cfg_api, crs)
    headers = {"x-apikey": api_key, "Accept": "application/json"}
    params = {"numRows": cfg_api.get("num_rows", 10)}
    try:
        resp = requests.get(
            url, headers=headers, params=params, timeout=cfg_api.get("timeout", 10)
        )
    except requests.RequestException as exc:
        raise ApiError(f"network error for {crs}: {exc}") from exc

    if resp.status_code == 401 or resp.status_code == 403:
        raise ApiError(
            f"auth failed for {crs} ({resp.status_code}): check RDM_API_KEY / product subscription"
        )
    if resp.status_code == 404:
        raise ApiError(
            f"404 for {crs}: check api.product_prefix and CRS code — url was {url}"
        )
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code} for {crs}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise ApiError(f"non-JSON response for {crs}: {resp.text[:200]}") from exc

    return parse_board(crs, data)


def _first(lst: Any) -> dict:
    """OpenLDBWS wraps origin/destination as a list; take the first element."""
    if isinstance(lst, list) and lst:
        return lst[0] or {}
    if isinstance(lst, dict):
        return lst
    return {}


def _parse_calling_points(service: dict) -> list[CallingPoint]:
    """subsequentCallingPoints -> [{callingPoint: [ {...}, ... ]}]. Flatten first list."""
    scp = service.get("subsequentCallingPoints") or []
    points: list[CallingPoint] = []
    for group in scp:
        for cp in group.get("callingPoint", []) or []:
            points.append(
                CallingPoint(
                    name=cp.get("locationName", ""),
                    crs=cp.get("crs", "") or "",
                    st=cp.get("st", "") or "",
                    et=cp.get("et", "") or "",
                )
            )
    return points


def parse_board(crs: str, data: dict) -> Board:
    services = data.get("trainServices") or []
    departures: list[Departure] = []
    for svc in services:
        dest = _first(svc.get("destination"))
        etd = (svc.get("etd") or "").strip()
        cancelled = bool(svc.get("isCancelled")) or etd.lower() == "cancelled"
        departures.append(
            Departure(
                std=(svc.get("std") or "").strip(),
                etd=etd,
                destination=dest.get("locationName", "") or "",
                destination_crs=dest.get("crs", "") or "",
                platform=(svc.get("platform") or "").strip(),
                operator=(svc.get("operator") or "").strip(),
                cancelled=cancelled,
                calling_points=_parse_calling_points(svc),
            )
        )

    raw_msgs = data.get("nrccMessages") or []
    messages: list[str] = []
    for m in raw_msgs:
        # messages may be {"value": "..."} or {"xhtmlMessage": "..."} or a plain str
        if isinstance(m, str):
            messages.append(m)
        elif isinstance(m, dict):
            messages.append(m.get("value") or m.get("xhtmlMessage") or "")

    return Board(
        crs=crs.upper(),
        location_name=data.get("locationName", crs.upper()),
        generated_at=data.get("generatedAt", ""),
        departures=departures,
        messages=[m for m in messages if m],
    )
