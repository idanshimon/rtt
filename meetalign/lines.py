"""Your transcript's line layout <-> Line.

A LineFormat knows how to read a line (time, speaker label, text) and write it back with a new
label and/or text, so the matcher never assumes RTT's layout. RTT's is "[HH:MM:SS] Speaker X: text".
Another recorder: `register_line_format("otter", RegexLineFormat(r"...", "{time} {label}: {text}"))`.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Protocol, Sequence

from .clock import secs
from .model import Line


class LineFormat(Protocol):
    def parse(self, index: int, raw: str) -> Optional[Line]: ...
    def render(self, line: Line, label: Optional[str] = None, text: Optional[str] = None) -> str: ...


class RegexLineFormat:
    """Regex with named groups h, m, s, label, text; `template` uses {time} {label} {text}."""

    def __init__(self, pattern: str, template: str) -> None:
        self.re = re.compile(pattern)
        self.template = template

    def parse(self, index: int, raw: str) -> Optional[Line]:
        m = self.re.match(raw)
        if not m:
            return None
        g = m.groupdict()
        return Line(index, secs(int(g["h"]), int(g["m"]), int(g.get("s") or 0)), g["label"], g["text"], raw)

    def render(self, line: Line, label: Optional[str] = None, text: Optional[str] = None) -> str:
        m = self.re.match(line.raw)
        if m is None:
            return line.raw
        # keep everything outside the label/text groups exactly as it was
        out, last = [], 0
        for name, new in (("label", label), ("text", text)):
            a, b = m.span(name)
            out.append(line.raw[last:a])
            out.append(new if new is not None else line.raw[a:b])
            last = b
        out.append(line.raw[last:])
        return "".join(out)


RTT_FORMAT = RegexLineFormat(r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\] Speaker (?P<label>[^:]+): (?P<text>.*)$",
                             "[{time}] Speaker {label}: {text}")
LINE_FORMATS: Dict[str, LineFormat] = {"rtt": RTT_FORMAT}


def register_line_format(name: str, fmt: LineFormat) -> None:
    LINE_FORMATS[name] = fmt


def get_line_format(fmt) -> LineFormat:
    if isinstance(fmt, str):
        if fmt not in LINE_FORMATS:
            raise ValueError(f"unknown line format {fmt!r}; known: {', '.join(sorted(LINE_FORMATS))}")
        return LINE_FORMATS[fmt]
    return fmt


def parse_lines(raw_lines: Sequence[str], fmt) -> List[Line]:
    f = get_line_format(fmt)
    return [ln for ln in (f.parse(i, r) for i, r in enumerate(raw_lines)) if ln is not None]
