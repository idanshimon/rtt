"""Meeting-app transcript text -> [Turn].

`parse_meeting(text, source="auto")`. The built-in "auto" source reads, mixed in one paste:
  Teams copy:   Name / 11:39 AM / text…        (name line, time line, text lines)
  Teams .vtt:   00:01:02.000 --> …  <v Name>text</v>
  Zoom / other: [11:39:02] Name: text  ·  Name: text  ·  [Name] 11:39:02 / text
Add a format with `register_source("webex", fn)` where fn(text) -> [Turn]; words are filled in for you.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

from .clock import secs
from .model import Turn
from .text import words

TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*([AaPp][Mm])?\s*$")
NAME_RE = re.compile(r"^[^\d\[\]<>:]{2,60}$")

SourceParser = Callable[[str], List[Turn]]
SOURCES: Dict[str, SourceParser] = {}


def register_source(name: str, parser: SourceParser) -> None:
    SOURCES[name] = parser


def parse_meeting(text: str, source: str = "auto") -> List[Turn]:
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; known: {', '.join(sorted(SOURCES))}")
    turns = [t for t in SOURCES[source](text) if t.text]
    for t in turns:
        t.words = set(words(t.text))
    return turns


def looks_like_name(s: str) -> bool:
    s = s.strip()
    if not NAME_RE.match(s) or len(s.split()) > 5 or s.endswith((".", "?", "!", ",")):
        return False
    parts = [p for p in re.split(r"[\s\-]+", s) if p]
    return bool(parts) and all(p[0].isupper() or p[0] in "'(" for p in parts)


def _auto(raw: str) -> List[Turn]:
    lines = [ln.rstrip() for ln in raw.replace("\r", "").split("\n")]
    turns: List[Turn] = []
    cur: Optional[Turn] = None
    pending_t: Optional[float] = None
    teams_mode = False      # seen "Name / 11:39 AM": "X: text" lines are then text, not speakers
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        i += 1
        if not ln or ln == "WEBVTT" or ln.isdigit():
            continue
        m = re.match(r"^(\d{1,2}:\d{2}:\d{2})\.\d+\s*-->", ln)            # vtt cue time
        if m:
            h, mi, s = (int(x) for x in m.group(1).split(":"))
            pending_t = None if h < 6 else secs(h, mi, s)              # relative vtt times: unknown clock
            continue
        m = re.match(r"^<v\s+([^>]+)>(.*?)(?:</v>)?$", ln)              # vtt voice tag
        if m:
            cur = Turn(m.group(1).strip(), m.group(2).strip(), pending_t)
            turns.append(cur)
            continue
        m = re.match(r"^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?\]?\s+([^:]{2,60}):\s*(.*)$", ln)
        if m and looks_like_name(m.group(5)):                          # [11:39:02] Name: text
            cur = Turn(m.group(5).strip(), m.group(6).strip(),
                       secs(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4)))
            turns.append(cur)
            continue
        m = re.match(r"^\[([^\]]{2,60})\]\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?$", ln)
        if m:                                                          # [Name] 11:39:02
            cur = Turn(m.group(1).strip(), "", secs(int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), m.group(5)))
            turns.append(cur)
            continue
        tm = TIME_RE.match(nxt)
        if looks_like_name(ln) and tm:                                 # Teams: Name / 11:39 AM
            cur = Turn(ln, "", secs(int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0), tm.group(4)))
            turns.append(cur)
            teams_mode = True
            i += 1
            continue
        m = re.match(r"^([^:]{2,60}):\s+(.+)$", ln)                       # Name: text
        if m and looks_like_name(m.group(1)) and not (teams_mode and cur is not None):
            cur = Turn(m.group(1).strip(), m.group(2).strip(), None)
            turns.append(cur)
            continue
        if cur is not None:                                            # continuation text
            cur.text = (cur.text + " " + ln).strip()
    return turns


register_source("auto", _auto)
