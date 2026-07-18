"""Frame renderers. Each returns a 1-bit PIL image sized to the panel.

Pages: full board, health, next-train (platform-mirror), combo.
All rendering is monochrome (fill=255 on a 0 background), suited to OLED.
"""
from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from . import sysinfo
from .api import Board, Departure
from .display import load_font
from .journeys import countdown_text

# Right-edge padding so right-aligned text (clock, status, platform) never gets
# shaved by the content-image boundary or the panel's physical right edge.
EDGE = 3


class Fonts:
    """Font set built once from the display config."""

    def __init__(self, disp_cfg: dict):
        path = disp_cfg.get("font_path", "") or ""
        self.small = load_font(path, int(disp_cfg.get("font_size", 10)))
        self.header = load_font(path, int(disp_cfg.get("header_font_size", 11)))
        self.big = load_font(path, int(disp_cfg.get("big_font_size", 20)))


# ---- low-level text helpers ------------------------------------------------

def _text_w(draw: ImageDraw.ImageDraw, s: str, font) -> int:
    return int(draw.textlength(s, font=font))


def _line_h(font) -> int:
    try:
        asc, desc = font.getmetrics()
        return asc + desc
    except AttributeError:  # pragma: no cover - old/odd fonts
        bbox = font.getbbox("Ag")
        return bbox[3] - bbox[1]


def _truncate(draw, s: str, font, max_w: int) -> str:
    if _text_w(draw, s, font) <= max_w:
        return s
    ell = "…"
    while s and _text_w(draw, s + ell, font) > max_w:
        s = s[:-1]
    return s + ell


def _hscroll(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    max_w: int,
    tick: int,
    fps: int,
    speed: int = 18,
    gap: int = 20,
) -> None:
    """Draw text at (x,y) clipped to max_w; scroll horizontally if it overflows."""
    tw = _text_w(draw, text, font)
    if tw <= max_w:
        draw.text((x, y), text, font=font, fill=255)
        return
    h = _line_h(font)
    period = tw + gap
    strip = Image.new("1", (period + max_w, h + 2), 0)
    sdraw = ImageDraw.Draw(strip)
    sdraw.text((0, 0), text, font=font, fill=255)
    sdraw.text((period, 0), text, font=font, fill=255)
    offset = int((tick / max(fps, 1)) * speed) % period
    window = strip.crop((offset, 0, offset + max_w, h + 2))
    base.paste(window, (x, y))


def _new_frame(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("1", size, 0)
    return img, ImageDraw.Draw(img)


def _clock(now: datetime) -> str:
    return now.strftime("%H:%M")


def _header(draw, img, size, fonts, left: str, now, tick, fps) -> int:
    """Draw a title bar with a clock; return y of the content area start."""
    w, _ = size
    clock = _clock(now)
    cw = _text_w(draw, clock, fonts.header)
    draw.text((w - cw - EDGE, 0), clock, font=fonts.header, fill=255)
    _hscroll(img, draw, 0, 0, left, fonts.header, w - cw - EDGE - 4, tick, fps)
    hy = _line_h(fonts.header)
    draw.line((0, hy, w, hy), fill=255)
    return hy + 1


def _status_text(dep: Departure) -> str:
    st = dep.status
    if st == "Cancelled":
        return "Cancel"
    if st == "On time":
        return "On time"
    return st  # "Delayed" or an expected "HH:MM"


# ---- pages -----------------------------------------------------------------

def render_board(
    size, fonts: Fonts, board: Board | None, station_name: str,
    now: datetime, tick: int, fps: int, stale_min: int | None = None,
) -> Image.Image:
    img, draw = _new_frame(size)
    w, h = size
    top = _header(draw, img, size, fonts, station_name, now, tick, fps)

    if board is None:
        _center(draw, size, "No data", fonts.header, y=top + 6)
        if stale_min is not None:
            _center(draw, size, f"offline {stale_min}m", fonts.small, y=top + 6 + _line_h(fonts.header))
        return img

    deps = board.departures
    if not deps:
        _center(draw, size, "No departures", fonts.small, y=top + 8)
        _footer_stale(draw, size, fonts, stale_min)
        return img

    row_h = _line_h(fonts.small) + 1
    n = max(1, (h - top) // row_h)
    for i, dep in enumerate(deps[:n]):
        y = top + i * row_h
        time_s = dep.std or "--:--"
        tw = _text_w(draw, time_s + " ", fonts.small)
        status = _status_text(dep)
        sw = _text_w(draw, status, fonts.small)
        draw.text((0, y), time_s, font=fonts.small, fill=255)
        # right-align status; invert cancelled/delayed for emphasis
        sx = w - sw - EDGE
        if dep.status == "Cancelled" or dep.status == "Delayed":
            draw.rectangle((sx - 1, y, w - 1, y + row_h - 2), fill=255)
            draw.text((sx, y), status, font=fonts.small, fill=0)
        else:
            draw.text((sx, y), status, font=fonts.small, fill=255)
        dest_x = tw
        dest_w = sx - dest_x - 3
        _hscroll(img, draw, dest_x, y, dep.destination or "?", fonts.small, dest_w, tick, fps)

    _footer_stale(draw, size, fonts, stale_min)
    return img


def render_bigboard(
    size, fonts: Fonts, board: Board | None, station_name: str,
    now: datetime, tick: int, fps: int, sub_dwell: float = 3.5,
    stale_min: int | None = None,
) -> Image.Image:
    """Large, glanceable single-station view that cycles through the next few
    departures one at a time (bigger fonts than the packed full board)."""
    img, draw = _new_frame(size)
    w, h = size
    top = _header(draw, img, size, fonts, station_name, now, tick, fps)

    if board is None or not board.departures:
        msg = "No data" if board is None else "No departures"
        _center(draw, size, msg, fonts.header, y=top + 6)
        return img

    deps = board.departures
    idx = int((tick / max(fps, 1)) / max(sub_dwell, 0.5)) % len(deps)
    dep = deps[idx]

    big_h = _line_h(fonts.big)
    dest_h = _line_h(fonts.header)
    y = top

    # Hero: big scheduled time on the left, platform on the right.
    draw.text((0, y), dep.std or "--:--", font=fonts.big, fill=255)
    if dep.platform:
        plat = f"P{dep.platform}"
        pw = _text_w(draw, plat, fonts.header)
        draw.text((w - pw - EDGE, y + (big_h - dest_h)), plat, font=fonts.header, fill=255)
    y += big_h

    # Destination, medium weight, scrolls if long.
    _hscroll(img, draw, 0, y, dep.destination or "?", fonts.header, w, tick, fps)
    y += dest_h

    # Status + countdown + position indicator.
    foot = f"{_status_text(dep)} · {countdown_text(dep, now)}"
    pos = f"{idx + 1}/{len(deps)}"
    pw = _text_w(draw, pos, fonts.small)
    draw.text((w - pw - EDGE, y), pos, font=fonts.small, fill=255)
    _hscroll(img, draw, 0, y, foot, fonts.small, w - pw - EDGE - 3, tick, fps)
    return img


def render_next_train(
    size, fonts: Fonts, journey: dict, dep: Departure | None,
    now: datetime, tick: int, fps: int, have_data: bool = True,
) -> Image.Image:
    img, draw = _new_frame(size)
    w, h = size
    top = _header(draw, img, size, fonts, journey.get("title", "Next train"), now, tick, fps)

    if dep is None:
        msg = "No direct service" if have_data else "No data"
        _center(draw, size, msg, fonts.header, y=top + 4)
        sub = f"to {journey.get('target_name', journey.get('target',''))}"
        _center(draw, size, _truncate(draw, sub, fonts.small, w - 4), fonts.small,
                y=top + 4 + _line_h(fonts.header))
        return img

    # Bottom-anchor the two info lines; the big countdown fills the space above.
    small_h = _line_h(fonts.small)
    has_calls = bool(dep.calling_points)
    call_y = h - small_h if has_calls else h
    detail_y = call_y - small_h

    cd = countdown_text(dep, now)
    bw = _text_w(draw, cd, fonts.big)
    big_h = _line_h(fonts.big)
    cy = top + max(0, (detail_y - top - big_h) // 2)
    draw.text(((w - bw) // 2, cy), cd, font=fonts.big, fill=255)

    # time / platform / status line
    plat = f"P{dep.platform}" if dep.platform else "P-"
    detail = f"{dep.expected}  {plat}  {_status_text(dep)}"
    _center(draw, size, _truncate(draw, detail, fonts.small, w - 2), fonts.small, y=detail_y)

    # calling at ... (scrolls)
    if has_calls:
        names = ", ".join(cp.name for cp in dep.calling_points if cp.name)
        prefix = "calls: "
        pw = _text_w(draw, prefix, fonts.small)
        draw.text((0, call_y), prefix, font=fonts.small, fill=255)
        _hscroll(img, draw, pw, call_y, names, fonts.small, w - pw, tick, fps)
    return img


def render_combo(
    size, fonts: Fonts, entries: list[tuple[dict, Departure | None]],
    now: datetime, tick: int, fps: int,
) -> Image.Image:
    img, draw = _new_frame(size)
    w, h = size
    top = _header(draw, img, size, fonts, "Next direct trains", now, tick, fps)
    row_h = max(_line_h(fonts.header), (h - top) // max(1, len(entries)))
    for i, (journey, dep) in enumerate(entries):
        y = top + i * row_h
        key = journey.get("origin", "?")
        keyw = _text_w(draw, key + " ", fonts.header)
        draw.text((0, y), key, font=fonts.header, fill=255)
        if dep is None:
            draw.text((keyw, y), "no service", font=fonts.small, fill=255)
            continue
        cd = countdown_text(dep, now)
        cdw = _text_w(draw, cd, fonts.header)
        draw.text((w - cdw - EDGE, y), cd, font=fonts.header, fill=255)
        mid = f"{dep.expected} {('P'+dep.platform) if dep.platform else ''}".strip()
        _hscroll(img, draw, keyw, y, mid, fonts.small, w - keyw - cdw - EDGE - 4, tick, fps)
    return img


def render_health(
    size, fonts: Fonts, health: sysinfo.Health, now: datetime, tick: int, fps: int,
) -> Image.Image:
    img, draw = _new_frame(size)
    w, h = size
    top = _header(draw, img, size, fonts, health.hostname or "system", now, tick, fps)
    row_h = _line_h(fonts.small) + 1
    y = top

    def line(s: str):
        nonlocal y
        if y + row_h <= h:
            _hscroll(img, draw, 0, y, s, fonts.small, w, tick, fps)
            y += row_h

    line(f"IP {health.ip}")
    for d in health.disks:
        line(f"{d.label} {d.percent}% {sysinfo.human_bytes(d.free)} free")
    bits = []
    if health.cpu_temp is not None:
        bits.append(f"{health.cpu_temp:.0f}C")
    if health.load1 is not None:
        bits.append(f"load {health.load1:.2f}")
    if health.uptime_s is not None:
        bits.append(f"up {sysinfo.human_uptime(health.uptime_s)}")
    if bits:
        line("  ".join(bits))
    return img


# ---- shared drawing helpers -----------------------------------------------

def _center(draw: ImageDraw.ImageDraw, size, text: str, font, y: int) -> None:
    w, _ = size
    tw = _text_w(draw, text, font)
    draw.text(((w - tw) // 2, y), text, font=font, fill=255)


def _footer_stale(draw, size, fonts, stale_min: int | None) -> None:
    if stale_min is None or stale_min < 2:
        return
    w, h = size
    msg = f"stale {stale_min}m"
    tw = _text_w(draw, msg, fonts.small)
    draw.text((w - tw, h - _line_h(fonts.small)), msg, font=fonts.small, fill=255)
