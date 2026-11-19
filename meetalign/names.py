"""Who said each line: same time (±window, midnight-safe) + most shared words and word pairs;
labels whose confident lines agree (2+, 75%+) give their name to their undecided lines."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .clock import ahead, near
from .model import Line, LineMatch, Turn
from .text import FILLER, words


def in_span(t: float, turns: List[Turn], window_s: float) -> bool:
    timed = [u.t for u in turns if u.t is not None]
    if not timed:
        return True
    return (near(t, timed[0], window_s) or near(t, timed[-1], window_s)
            or 0 <= ahead(t, timed[0]) <= ahead(timed[-1], timed[0]))


def match_names(lines: Sequence[Line], turns: List[Turn], window_s: float = 75.0
                ) -> Tuple[List[LineMatch], Dict[str, str]]:
    matches: List[LineMatch] = []
    for ln in lines:
        if not in_span(ln.t, turns, window_s):
            continue
        ws = words(ln.text)
        if not ws:
            continue
        content = [w for w in ws if w not in FILLER] or ws
        pairs = list(zip(ws, ws[1:]))
        by_name: Dict[str, float] = {}
        for u in turns:
            if u.t is not None and not near(ln.t, u.t, window_s):
                continue
            hit = sum(1 for w in content if w in u.words) / len(content)
            if pairs:   # word order agreement separates "yeah" turns from real ones
                ut = words(u.text)
                upairs = set(zip(ut, ut[1:]))
                hit = 0.6 * hit + 0.4 * (sum(1 for p in pairs if p in upairs) / len(pairs))
            by_name[u.name] = max(by_name.get(u.name, 0.0), hit)
        ranked = sorted(by_name.items(), key=lambda x: -x[1])
        best: Tuple[Optional[str], float] = ranked[0] if ranked else (None, 0.0)
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        strong = len(content) >= 3 and best[1] >= 0.5 and best[1] - second >= 0.15
        matches.append(LineMatch(ln.index, ln.label, best[0] if strong else None, round(best[1], 2),
                                 "words" if strong else "none"))
    votes: Dict[str, Dict[str, int]] = {}
    for m in matches:
        if m.name and m.label != "?":
            votes.setdefault(m.label, {}).setdefault(m.name, 0)
            votes[m.label][m.name] += 1
    label_names: Dict[str, str] = {}
    for lab, v in votes.items():
        name, n = max(v.items(), key=lambda x: x[1])
        if n >= 2 and n / sum(v.values()) >= 0.75:
            label_names[lab] = name
    for m in matches:
        if m.name is None and m.label in label_names:
            m.name, m.how = label_names[m.label], "label"
    return matches, label_names
