"""Chunk length control for chunked backends: manual steps and an automatic mode."""
from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple

import numpy as np

CHUNK_STEPS = (3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120)


class ChunkPolicy:
    """Chunk length shared by the UI, rtt-ctl and the running backend (survives backend switches).

    fixed: every chunk is `seconds` long (the old behaviour).
    auto:  cut at the quietest moment in the last 40% of the window, so words and sentences are
           not split mid-way; grow the window when the backend falls behind (processing time
           close to real time or audio piling up), shrink back toward `seconds` once it keeps up.
    """

    def __init__(self, seconds: float, mode: str = "fixed", lo: float = 3.0, hi: float = 120.0,
                 on_change: Optional[Callable[["ChunkPolicy"], None]] = None) -> None:
        self._lock = threading.Lock()
        self.lo, self.hi = lo, hi
        self.seconds = float(min(hi, max(lo, seconds)))   # what the user chose
        self.current = self.seconds                       # what is in effect (auto may differ)
        self.mode = mode
        self.on_change = on_change
        self.last_rtf: Optional[float] = None
        self.quiet_cuts = 0
        self.hard_cuts = 0

    def bounds(self, lo: float, hi: float) -> None:
        with self._lock:
            self.lo, self.hi = lo, hi
            self.seconds = min(hi, max(lo, self.seconds))
            self.current = min(hi, max(lo, self.current))

    def set(self, seconds: float) -> float:
        with self._lock:
            self.seconds = self.current = float(min(self.hi, max(self.lo, seconds)))
        self._changed()
        return self.seconds

    def step(self, direction: int) -> float:
        cur = self.seconds
        if direction > 0:
            nxt = next((x for x in CHUNK_STEPS if x > cur + 1e-6), CHUNK_STEPS[-1])
        else:
            nxt = next((x for x in reversed(CHUNK_STEPS) if x < cur - 1e-6), CHUNK_STEPS[0])
        return self.set(nxt)

    def set_mode(self, mode: str) -> str:
        if mode not in ("fixed", "auto"):
            raise ValueError("mode must be fixed or auto")
        with self._lock:
            self.mode = mode
            self.current = self.seconds
        self._changed()
        return mode

    def samples(self, sample_rate: int) -> int:
        return max(1, int(sample_rate * self.current))

    def report(self, chunk_seconds: float, processing_seconds: float, backlog_seconds: float) -> None:
        """Called after each chunk; in auto mode adapts the window so the backend keeps up."""
        if chunk_seconds <= 0:
            return
        rtf = processing_seconds / chunk_seconds
        changed = False
        with self._lock:
            self.last_rtf = rtf
            if self.mode != "auto":
                return
            if rtf > 0.8 or backlog_seconds > 2 * self.current:
                new = min(self.hi, self.current * 1.25)
            elif rtf < 0.35 and backlog_seconds < self.current and self.current > self.seconds:
                new = max(self.seconds, self.current / 1.25)
            else:
                new = self.current
            if abs(new - self.current) >= 0.5:
                self.current = new
                changed = True
        if changed:
            self._changed()

    def label(self) -> str:
        if self.mode == "auto":
            return f"chunk auto {self.current:.0f}s"
        return f"chunk {self.seconds:.0f}s"

    def snapshot(self) -> dict:
        return {"seconds": self.seconds, "current": round(self.current, 1), "mode": self.mode,
                "last_rtf": None if self.last_rtf is None else round(self.last_rtf, 2),
                "quiet_cuts": self.quiet_cuts, "hard_cuts": self.hard_cuts}

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change(self)
            except Exception:
                pass


def quietest_cut(audio: np.ndarray, sample_rate: int, start: int, end: int,
                 frame_s: float = 0.3) -> Tuple[int, bool]:
    """Centre of the quietest `frame_s` window in [start, end), and whether it is a real pause."""
    frame = max(1, int(sample_rate * frame_s))
    hop = max(1, frame // 3)
    seg = audio[start:end].astype(np.float64)
    if seg.shape[0] < 2 * frame:
        return end, False
    c = np.concatenate(([0.0], np.cumsum(seg * seg)))
    starts = np.arange(0, seg.shape[0] - frame + 1, hop)
    e = np.sqrt((c[starts + frame] - c[starts]) / frame)
    i = int(np.argmin(e))
    whole = float(np.sqrt(np.mean(np.square(audio[:end])))) or 1e-9
    return start + int(starts[i]) + frame // 2, bool(e[i] < 0.35 * whole)
