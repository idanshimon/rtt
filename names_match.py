"""Compatibility layer: the original names_match API on top of the `meetalign` library.

New code should use `meetalign` directly (align(), parse_meeting(), …). These wrappers keep the
older string-based signatures working.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from meetalign import ai as _ai
from meetalign.lines import RTT_FORMAT, parse_lines
from meetalign.model import LineMatch, TextFix as _Fix, Turn
from meetalign.names import match_names
from meetalign.refine import learnable as _learnable, refine_lines
from meetalign.sources import parse_meeting


def parse_meeting_transcript(raw: str) -> List[Turn]:
    return parse_meeting(raw)


def match_lines(lines: Sequence[str], turns: List[Turn], window_s: float = 75.0) -> Dict:
    matches, label_names = match_names(parse_lines(lines, RTT_FORMAT), turns, window_s)
    return {"matches": matches, "label_names": label_names, "turns": len(turns)}


def ai_resolve(lines: Sequence[str], turns: List[Turn], matches: List[LineMatch], call) -> int:
    try:
        return _ai.resolve_unnamed(parse_lines(lines, RTT_FORMAT), turns, matches, call)
    except Exception:
        return 0


def copilot_call(prompt: str, model=None) -> str:
    return _ai.copilot_call(prompt, model)


class TextFix:
    """Old shape: full lines in .old / .new."""

    def __init__(self, f: _Fix, raw: str) -> None:
        self.index, self.ratio, self.pairs = f.index, f.ratio, f.pairs
        self.old = raw
        ln = RTT_FORMAT.parse(f.index, raw)
        self.new = RTT_FORMAT.render(ln, text=f.new_text) if ln else raw


def refine_text(lines: Sequence[str], turns: List[Turn], window_s: float = 75.0) -> List[TextFix]:
    return [TextFix(f, lines[f.index]) for f in refine_lines(parse_lines(lines, RTT_FORMAT), turns, window_s)]


def learnable(fixes, min_count: int = 1) -> List[Tuple[str, str, int]]:
    return _learnable(fixes, min_count=min_count)
