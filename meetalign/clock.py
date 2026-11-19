"""Clock-time helpers (seconds since midnight; midnight-safe comparisons)."""
from typing import Optional


def secs(h: int, m: int, s: int = 0, ampm: Optional[str] = None) -> float:
    if ampm:
        h = h % 12 + (12 if ampm.lower() == "pm" else 0)
    return h * 3600 + m * 60 + s


def ahead(a: float, b: float) -> float:
    """Seconds from clock time b forward to a, in (-12 h, 12 h] (handles midnight)."""
    d = (a - b) % 86400
    return d - 86400 if d > 43200 else d


def near(t: float, turn_t: float, window_s: float) -> bool:
    """Line time t inside a turn stamped turn_t (to the minute), ± window."""
    return -window_s <= ahead(t, turn_t) <= 60 + window_s


def fmt(t: Optional[float]) -> str:
    if t is None:
        return "--:--"
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}"
