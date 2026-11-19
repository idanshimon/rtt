"""align(): the whole pipeline in one call. Pure: returns an AlignResult, applies nothing."""
from __future__ import annotations

from typing import Optional, Sequence, Union

from .ai import Resolver, get_resolver, resolve_unnamed
from .lines import LineFormat, get_line_format, parse_lines
from .model import AlignResult, Line
from .names import match_names
from .refine import learnable, refine_lines
from .sources import parse_meeting


def align(raw_lines: Sequence[str], meeting: Union[str, list], *, line_format: Union[str, LineFormat] = "rtt",
          source: str = "auto", names: bool = True, refine: bool = False,
          resolver: Union[str, Resolver, None] = None, window_s: float = 75.0) -> AlignResult:
    """Match `raw_lines` (your transcript) against `meeting` (pasted app text, or [Turn]).

    names    put real names on lines (and on labels that clearly belong to one person)
    refine   take the app's wording for the shared part of each line; list learnable words
    resolver None/"none" = words only; "copilot" or any callable(prompt) -> text for the rest
    """
    fmt = get_line_format(line_format)
    turns = parse_meeting(meeting, source) if isinstance(meeting, str) else list(meeting)
    original = list(raw_lines)
    current = list(original)
    fixes, learn = [], []
    if refine and turns:
        fixes = refine_lines(parse_lines(current, fmt), turns, window_s)
        for f in fixes:
            ln = fmt.parse(f.index, current[f.index])
            if ln is not None:
                current[f.index] = fmt.render(ln, text=f.new_text)
        learn = [(h, r) for h, r, _ in learnable(fixes, people=[t.name for t in turns])]
    matches, label_names, ai_n, ai_err = [], {}, 0, None
    if names and turns:
        parsed = parse_lines(current, fmt)       # better words match names better
        matches, label_names = match_names(parsed, turns, window_s)
        res = get_resolver(resolver)
        if res is not None and any(m.name is None for m in matches):
            try:
                ai_n = resolve_unnamed(parsed, turns, matches, res)
            except Exception as exc:              # words-only result still stands
                ai_err = str(exc)[:160]
        for m in matches:
            if m.name:
                ln = fmt.parse(m.index, current[m.index])
                if ln is not None:
                    current[m.index] = fmt.render(ln, label=m.name)
    changes = {i: current[i] for i in range(len(current)) if current[i] != original[i]}
    return AlignResult(changes=changes, lines_before={i: original[i] for i in changes},
                       label_names=label_names, matches=matches, fixes=fixes, learn=learn, turns=turns,
                       ai_filled=ai_n, ai_error=ai_err)
