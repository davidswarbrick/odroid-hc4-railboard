"""System / disk health for the health page. Stdlib-first; psutil optional."""
from __future__ import annotations

import os
import shutil
import socket
from dataclasses import dataclass, field

try:  # optional, nicer temps/mem if present
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


@dataclass
class DiskInfo:
    label: str
    path: str
    used: int
    total: int

    @property
    def percent(self) -> int:
        return int(round(100 * self.used / self.total)) if self.total else 0

    @property
    def free(self) -> int:
        return self.total - self.used


@dataclass
class Health:
    hostname: str
    ip: str
    disks: list[DiskInfo] = field(default_factory=list)
    cpu_temp: float | None = None
    load1: float | None = None
    uptime_s: float | None = None


def lan_ip() -> str:
    """Best-effort primary LAN IP without actually sending packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no traffic sent for UDP connect
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "0.0.0.0"
    finally:
        s.close()


def cpu_temp() -> float | None:
    """Degrees C from psutil or the thermal sysfs, whichever is available."""
    if psutil is not None:
        try:
            temps = psutil.sensors_temperatures()
            for entries in temps.values():
                if entries:
                    return round(float(entries[0].current), 1)
        except Exception:
            pass
    # Fallback: /sys/class/thermal/thermal_zone*/temp (millidegrees)
    base = "/sys/class/thermal"
    try:
        zones = sorted(z for z in os.listdir(base) if z.startswith("thermal_zone"))
    except OSError:
        return None
    for zone in zones:
        try:
            with open(os.path.join(base, zone, "temp"), "r") as fh:
                raw = int(fh.read().strip())
            return round(raw / 1000.0, 1)
        except (OSError, ValueError):
            continue
    return None


def uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", "r") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError):
        return None


def _disk(label: str, path: str) -> DiskInfo | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return DiskInfo(label=label, path=path, used=usage.used, total=usage.total)


def gather(disk_paths: dict[str, str] | None = None) -> Health:
    disk_paths = disk_paths or {"root": "/"}
    disks = [d for d in (_disk(lbl, p) for lbl, p in disk_paths.items()) if d]
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = None
    return Health(
        hostname=socket.gethostname(),
        ip=lan_ip(),
        disks=disks,
        cpu_temp=cpu_temp(),
        load1=load1,
        uptime_s=uptime_seconds(),
    )


def human_bytes(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}P"


def human_uptime(seconds: float) -> str:
    m, _ = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"
