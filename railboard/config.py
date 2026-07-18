"""Configuration loading and defaults.

Loads config.yaml, deep-merges it over built-in defaults, and pulls the API key
from the RDM_API_KEY environment variable (never from the file).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "api": {
        "base_url": "https://api1.raildata.org.uk",
        "product_prefix": "1010-live-departure-board-dep",
        "operation": "GetDepBoardWithDetails",
        "version": "20220120",
        "num_rows": 12,
        "timeout": 10,
    },
    "refresh_seconds": 60,
    # Stations, journeys and the page rotation are defined by the user in
    # config.yaml (see config.example.yaml). Defaults are intentionally empty so
    # nothing station-specific is baked into the code — out of the box only the
    # hardware-free health page shows.
    "stations": [],
    "journeys": [],
    "pages": ["health"],
    "display": {
        "dwell_seconds": 8,
        "fps": 10,
        "font_path": "",
        "font_size": 10,
        "header_font_size": 11,
        "big_font_size": 16,
    },
    "burn_in": {
        "orbit": True,
        "orbit_max": 3,
        "invert_minutes": 0,
        "contrast_day": 255,
        "contrast_night": 40,
    },
    "quiet_hours": {
        "enabled": True,
        "start": "01:00",
        "end": "06:30",
        "action": "dim",
    },
    "disk_paths": {"root": "/"},
}


class ConfigError(RuntimeError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    """Return base with override merged in. Lists are replaced, not merged."""
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


class Config:
    """Thin dot/getitem accessor over the merged config dict."""

    def __init__(self, data: dict[str, Any], api_key: str | None):
        self.data = data
        self.api_key = api_key

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def journey(self, journey_id: str) -> dict[str, Any] | None:
        for j in self.data.get("journeys", []):
            if j.get("id") == journey_id:
                return j
        return None

    def station_name(self, crs: str) -> str:
        for s in self.data.get("stations", []):
            if s.get("crs") == crs:
                return s.get("name", crs)
        return crs

    def station_crs_list(self) -> list[str]:
        crs = {s["crs"] for s in self.data.get("stations", [])}
        # journeys may reference origins not in the stations list
        for j in self.data.get("journeys", []):
            crs.add(j["origin"])
        return sorted(crs)


def load_config(path: str | os.PathLike | None = None) -> Config:
    data = copy.deepcopy(DEFAULTS)
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        with p.open("r", encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        if not isinstance(user, dict):
            raise ConfigError(f"config file must be a YAML mapping: {p}")
        data = _deep_merge(data, user)

    api_key = os.environ.get("RDM_API_KEY", "").strip() or None
    return Config(data, api_key)
