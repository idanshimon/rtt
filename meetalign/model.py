"""Data types shared by every stage."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Turn:
    """One speaker turn from the meeting app (Teams/Zoom/…): who, what, when."""
    name: str
    text: str
    t: Optional[float] = None          # clock seconds since midnight; None = unknown
    words: set = field(default_factory=set)


@dataclass
class Line:
    """One line of your own transcript, parsed by a LineFormat."""
    index: int                          # position in the list you passed in
    t: float                            # clock seconds since midnight
    label: str                          # speaker label as written ("2", "Guest-1", "Amit")
    text: str                           # the speech
    raw: str                            # the full original line


@dataclass
class LineMatch:
    index: int
    label: str
    name: Optional[str]
    score: float
    how: str                            # "words" | "label" | "ai" | "none"


@dataclass
class TextFix:
    index: int
    old_text: str
    new_text: str
    ratio: float
    pairs: List[Tuple[str, str]]        # (heard, app) word differences inside the line


@dataclass
class AlignResult:
    """Everything align() found. Nothing is applied: callers decide what to do with it."""
    changes: Dict[int, str]             # index -> new full line (names and/or text)
    lines_before: Dict[int, str]        # index -> original line, for stale checks and undo
    label_names: Dict[str, str]         # labels that clearly belong to one person
    matches: List[LineMatch]
    fixes: List[TextFix]
    learn: List[Tuple[str, str]]        # (heard, right) words worth adding to a dictionary
    turns: List[Turn]
    ai_filled: int = 0
    ai_error: Optional[str] = None

    @property
    def names(self) -> List[str]:
        return sorted({t.name for t in self.turns})

    def summary(self) -> str:
        named = [m for m in self.matches if m.name]
        per: Dict[str, int] = {}
        for m in named:
            per[m.name] = per.get(m.name, 0) + 1
        hows = {h: sum(1 for m in named if m.how == h) for h in ("words", "label", "ai")}
        who = ", ".join(f"{n} {c}" for n, c in sorted(per.items(), key=lambda x: -x[1]))
        s = (f"{len(named)}/{len(self.matches)} lines named ({hows['words']} by words, "
             f"{hows['label']} by label, {hows['ai']} by AI) · {who}")
        return (f"{len(self.fixes)} lines reworded · " + s) if self.fixes else s

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "turns": len(self.turns), "names": self.names,
                "label_names": self.label_names, "lines_changed": len(self.changes),
                "reworded": len(self.fixes), "ai_filled": self.ai_filled, "ai_error": self.ai_error,
                "learn": [list(p) for p in self.learn],
                "changes": [{"index": i, "before": self.lines_before[i], "after": a}
                            for i, a in sorted(self.changes.items())]}
