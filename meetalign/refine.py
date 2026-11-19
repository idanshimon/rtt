"""The meeting app's wording for the part of each line both transcripts share.

Rules (each guards a failure seen on a real call):
  - 60%+ of the line's words must match one turn at the same time; the taken span must be 60%+ real
    matches and 0.6–1.5x as long, else the line is left alone.
  - Only the shared region is replaced; words before/after stay, so a neighbour's words in the same
    turn never leak in. One edge word may be swapped 1:1 if it sounds alike (misheard names).
  - Where the app hid words ("**** *****"), keep what was heard.
  - Keep a capitalized sentence start.
"""
from __future__ import annotations

import difflib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .clock import near
from .model import Line, TextFix, Turn
from .text import COMMON, toks


def _alike(a, b) -> bool:
    ra = difflib.SequenceMatcher(None, a[1], b[1]).ratio()
    return ra >= 0.4 or (a[0][:1].isupper() and b[0][:1].isupper() and ra >= 0.25)


def refine_lines(lines: Sequence[Line], turns: List[Turn], window_s: float = 75.0,
                 min_ratio: float = 0.6) -> List[TextFix]:
    fixes: List[TextFix] = []
    ttoks = [toks(u.text) for u in turns]
    for ln in lines:
        rt = toks(ln.text)
        if len(rt) < 3:
            continue
        rn = [n for _, n in rt]
        best = None
        for u, ut in zip(turns, ttoks):
            if u.t is not None and not near(ln.t, u.t, window_s):
                continue
            sm = difflib.SequenceMatcher(None, rn, [n for _, n in ut], autojunk=False)
            blocks = [b for b in sm.get_matching_blocks() if b.size]
            matched = sum(b.size for b in blocks)
            ratio = matched / len(rn)
            if ratio < min_ratio or not blocks:
                continue
            b0, b1 = blocks[0], blocks[-1]
            a_start, a_end, start, end = b0.a, b1.a + b1.size, b0.b, b1.b + b1.size
            if matched / max(1, end - start) < 0.6:
                continue
            if best is None or ratio > best[0]:
                best = (ratio, ut, a_start, a_end, start, end)
        if best is None:
            continue
        ratio, ut, a_start, a_end, start, end = best
        for _ in range(2):
            if a_start > 0 and start > 0 and _alike(rt[a_start - 1], ut[start - 1]):
                a_start, start = a_start - 1, start - 1
            if a_end < len(rt) and end < len(ut) and _alike(rt[a_end], ut[end]):
                a_end, end = a_end + 1, end + 1
        span = ut[start:end]
        if not span or any(n == "*" for _, n in ut[max(0, start - 1):end + 1]):
            continue
        head = [tok for tok, _ in rt[:a_start]]
        tail = [tok for tok, _ in rt[a_end:]]
        mid = [tok for tok, _ in span]
        if rt[0][0][:1].isupper() and not head and mid:
            mid[0] = mid[0][:1].upper() + mid[0][1:]
        new_text = " ".join(head + mid + tail)
        if new_text == ln.text.strip():
            continue
        if not (0.6 <= len(span) / max(1, a_end - a_start) <= 1.5):
            continue
        sm2 = difflib.SequenceMatcher(None, rn[a_start:a_end], [n for _, n in span], autojunk=False)
        pairs = []
        for op, a0, a1, c0, c1 in sm2.get_opcodes():
            if op == "replace" and a1 - a0 <= 3 and c1 - c0 <= 3:
                heard = " ".join(tok.strip(".,?!;:\"()") for tok, _ in rt[a_start + a0:a_start + a1])
                right = " ".join(tok.strip(".,?!;:\"()") for tok, _ in span[c0:c1])
                pairs.append((heard, right))
        fixes.append(TextFix(ln.index, ln.text, new_text, round(ratio, 2), pairs))
    return fixes


def learnable(fixes: Iterable[TextFix], people: Optional[Iterable[str]] = None,
              min_count: int = 2) -> List[Tuple[str, str, int]]:
    """Words worth a dictionary rule: the app's side has a capital (name/acronym), the heard side
    is not just common words, not numbers or endings. Kept when seen min_count+ times, or when the
    right word is part of a participant's name (`people`)."""
    names = {p.lower() for full in (people or []) for p in full.replace("'", " ").split()}
    counts: Dict[Tuple[str, str], int] = {}
    for f in fixes:
        for heard, right in f.pairs:
            if not right or heard.lower() == right.lower():
                continue
            if not any(c.isupper() or c.isdigit() for c in right):
                continue
            if all(w in COMMON for w in heard.split()):
                continue
            if right.split()[0] in ("I", "I'm", "I'll") or len(heard) < 3:
                continue
            if right.replace(",", "").isdigit():
                continue
            if right.lower().startswith(heard.lower()) or heard.lower().startswith(right.lower()):
                continue
            counts[(heard, right)] = counts.get((heard, right), 0) + 1
    out = [(h, r, n) for (h, r), n in counts.items()
           if n >= min_count or (names and all(w.lower() in names for w in r.split()))]
    return sorted(out, key=lambda x: -x[2])
