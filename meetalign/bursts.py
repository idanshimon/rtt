"""Status-line bursts: many pause/resume/mute/bookmark lines within seconds = keys fired by
accident (e.g. text pasted as keystrokes). A person does these a few times an hour."""
from __future__ import annotations

import re
from typing import List, Pattern, Sequence

RTT_MARKER_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[(⏸|▶|🔇|🎙|★ RTT bookmark)")


def find_bursts(lines: Sequence[str], marker_re: Pattern = RTT_MARKER_RE, gap_s: float = 3.0,
                min_run: int = 4) -> List[int]:
    """Indices of marker lines in runs of min_run+ within gap_s of each other (speech ignored)."""
    marks = []
    for i, ln in enumerate(lines):
        m = marker_re.match(ln)
        if m:
            marks.append((i, int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))))
    out: List[int] = []
    run: list = []
    for i, t in marks:
        if run and t - run[-1][1] > gap_s:
            if len(run) >= min_run:
                out += [j for j, _ in run]
            run = []
        run.append((i, t))
    if len(run) >= min_run:
        out += [j for j, _ in run]
    return out
