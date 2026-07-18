"""Display abstraction + burn-in mitigation.

Three backends, chosen at runtime:
  real      -> luma.oled SSD1306/SH1106 over I2C (the HC4)
  emulator  -> luma.emulator (pygame window / capture) for desktop preview
  simulate  -> no deps beyond Pillow; writes each frame as a PNG

All backends accept a 1-bit PIL image sized to the panel. The Display wrapper
applies orbit (pixel-shift), optional invert, contrast, and quiet-hours before
pushing the frame.
"""
from __future__ import annotations

import os
from datetime import datetime, time as dtime
from functools import lru_cache
from typing import Any

from PIL import Image, ImageChops, ImageFont

# Fonts to try, in order, when no font_path is configured (so sizes still scale).
_FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


@lru_cache(maxsize=32)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TTF at `size`; fall back to a system TTF, then Pillow's bitmap font."""
    candidates = [path] if path else []
    candidates += _FALLBACK_FONTS
    for cand in candidates:
        if cand and os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _coerce_addr(val: Any) -> int:
    if isinstance(val, int):
        return val
    return int(str(val), 16)


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    t = now.time()
    s, e = _parse_hhmm(start), _parse_hhmm(end)
    if s <= e:
        return s <= t < e
    return t >= s or t < e  # window wraps midnight


class SimulateDevice:
    """Writes frames to PNG files. Handy for CI / headless preview."""

    def __init__(self, width: int, height: int, out_dir: str):
        self.width = width
        self.height = height
        self.mode = "1"
        self.out_dir = out_dir
        self._n = 0
        os.makedirs(out_dir, exist_ok=True)

    def display(self, image: Image.Image) -> None:
        self._n += 1
        image.save(os.path.join(self.out_dir, "latest.png"))
        image.save(os.path.join(self.out_dir, f"frame_{self._n:04d}.png"))

    def contrast(self, level: int) -> None:  # no-op
        pass

    def hide(self) -> None:
        pass

    def show(self) -> None:
        pass

    def cleanup(self) -> None:
        pass


def _make_device(kind: str, disp_cfg: dict[str, Any]):
    width = int(disp_cfg.get("width", 128))
    height = int(disp_cfg.get("height", 64))

    if kind == "simulate":
        out_dir = disp_cfg.get("simulate_dir", "./frames")
        return SimulateDevice(width, height, out_dir)

    if kind == "emulator":
        from luma.emulator.device import pygame  # type: ignore

        return pygame(width=width, height=height, mode="1", frame_rate=60)

    if kind == "real":
        from luma.core.interface.serial import i2c  # type: ignore
        from luma.oled.device import sh1106, ssd1306  # type: ignore

        serial = i2c(
            port=int(disp_cfg.get("i2c_port", 1)),
            address=_coerce_addr(disp_cfg.get("i2c_address", 0x3C)),
        )
        driver = str(disp_cfg.get("driver", "ssd1306")).lower()
        cls = sh1106 if driver == "sh1106" else ssd1306
        return cls(
            serial,
            width=width,
            height=height,
            rotate=int(disp_cfg.get("rotate", 0)),
        )

    raise ValueError(f"unknown display kind: {kind}")


class Display:
    def __init__(self, cfg, kind: str = "real"):
        self.cfg = cfg
        self.kind = kind
        disp_cfg = dict(cfg["display"])
        self.device = _make_device(kind, disp_cfg)
        self.width = self.device.width
        self.height = self.device.height
        self._blanked = False

        self._burn = cfg["burn_in"]
        self._quiet = cfg["quiet_hours"]
        self._orbit_idx = 0
        # Content is drawn into an inset "safe area" so the pixel-shift orbit can
        # move the frame around without ever clipping edge content (e.g. the clock).
        self._orbit_on = bool(self._burn.get("orbit", True))
        self.margin = int(self._burn.get("orbit_max", 2)) if self._orbit_on else 0
        self.content_width = self.width - 2 * self.margin
        self.content_height = self.height - 2 * self.margin
        m = self.margin
        # offsets relative to the centered position, each within [-m, m]
        self._orbit_path = [(0, 0), (m, 0), (m, m), (0, m), (-m, m), (-m, 0), (-m, -m), (0, -m), (m, -m)]

    # -- burn-in helpers -------------------------------------------------
    def next_cycle(self) -> None:
        """Call once per page change to advance the pixel-shift orbit."""
        self._orbit_idx = (self._orbit_idx + 1) % len(self._orbit_path)

    def _orbit_offset(self) -> tuple[int, int]:
        if not self._burn.get("orbit", True):
            return (0, 0)
        return self._orbit_path[self._orbit_idx]

    def _apply_invert(self, image: Image.Image, now: datetime) -> Image.Image:
        mins = int(self._burn.get("invert_minutes", 0) or 0)
        if mins <= 0:
            return image
        window = (now.hour * 60 + now.minute) // mins
        if window % 2 == 1:
            return ImageChops.invert(image.convert("1")).convert("1")
        return image

    def _quiet_state(self, now: datetime) -> str:
        """Return 'normal', 'dim', or 'blank' for the current time."""
        if not self._quiet.get("enabled", False):
            return "normal"
        if _in_quiet_hours(now, self._quiet["start"], self._quiet["end"]):
            return "blank" if self._quiet.get("action") == "blank" else "dim"
        return "normal"

    def quiet_state(self, now: datetime | None = None) -> str:
        """Public: 'normal' | 'dim' | 'blank' for the given (or current) time."""
        return self._quiet_state(now or datetime.now())

    # -- output ----------------------------------------------------------
    def render(self, image: Image.Image, now: datetime | None = None) -> None:
        now = now or datetime.now()
        state = self._quiet_state(now)

        if state == "blank":
            if not self._blanked:
                try:
                    self.device.hide()
                except Exception:
                    self.device.display(Image.new("1", (self.width, self.height), 0))
                self._blanked = True
            return

        if self._blanked:
            try:
                self.device.show()
            except Exception:
                pass
            self._blanked = False

        contrast = self._burn.get("contrast_night" if state == "dim" else "contrast_day", 255)
        try:
            self.device.contrast(int(contrast))
        except Exception:
            pass

        # Place the content image into the full panel at the (margin-shifted) origin,
        # so orbiting never clips edge content.
        dx, dy = self._orbit_offset()
        panel = Image.new("1", (self.width, self.height), 0)
        panel.paste(image, (self.margin + dx, self.margin + dy))
        panel = self._apply_invert(panel, now)

        self.device.display(panel)

    def cleanup(self) -> None:
        try:
            self.device.display(Image.new("1", (self.width, self.height), 0))
        except Exception:
            pass
        for meth in ("hide", "cleanup"):
            fn = getattr(self.device, meth, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
