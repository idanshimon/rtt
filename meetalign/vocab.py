"""Domain vocabulary: the right spelling of terms speech models mangle (NWND, SSPs, FHIR…).

Pure functions; callers decide where term lists come from.
  normalize(text, terms, common)  fix casing of known terms: "SSPS"/"ssps" -> "SSPs", "nwnd" -> "NWND".
                                  A lowercase token that is an ordinary word ("it", "us") is never
                                  changed, so "IT" in the list does not touch "it".
  phrases(terms)                  list for speech-model phrase lists / prompts.
  mine(lines, known)              acronym-like terms in transcripts, with how they were written.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Set


def _pattern(term: str) -> "re.Pattern":
    return re.compile(r"(?<![\w-])(" + re.escape(term) + r")(s|S)?(?![\w-])", re.IGNORECASE)


class Vocabulary:
    def __init__(self, terms: Iterable[str] = (), common: Optional[Set[str]] = None) -> None:
        seen: Dict[str, str] = {}
        for t in terms:
            t = (t or "").strip()
            if len(t) >= 2:
                seen.setdefault(t.lower(), t)
        self.terms: List[str] = sorted(seen.values(), key=lambda s: (-len(s), s))
        self.common = common or set()
        self._pats = [(t, _pattern(t)) for t in self.terms]

    def __len__(self) -> int:
        return len(self.terms)

    def normalize(self, text: str) -> str:
        for term, pat in self._pats:
            def fix(m, term=term):
                word, plural = m.group(1), m.group(2) or ""
                if word == term and plural in ("", "s"):
                    return m.group(0)
                whole = (word + plural)
                if whole.islower() and (whole in self.common or word in self.common):
                    return m.group(0)                 # an ordinary word ("it", "us"), not the term
                if plural and word.islower() and not term.isupper():
                    return m.group(0)
                return term + ("s" if plural else "")
            text = pat.sub(fix, text)
        return text

    def phrases(self, limit: int = 500) -> List[str]:
        return self.terms[:limit]


def mine(lines: Sequence[str], known: Iterable[str] = (), min_count: int = 3) -> List[dict]:
    """Acronym-like tokens (2+ capitals, letters/digits) seen min_count+ times, grouped
    case-insensitively with their spellings, e.g. {"term": "SSP", "count": 131,
    "forms": {"SSP": 67, "SSPS": 56, ...}}. Plural forms fold into the singular."""
    known_l = {k.lower() for k in known}
    raw: Dict[str, Counter] = {}
    for ln in lines:
        text = ln.split(": ", 1)[1] if ": " in ln[:60] else ln
        for tok in re.findall(r"(?<![\w-])[A-Za-z][A-Za-z0-9]{1,9}(?:'s)?(?![\w-])", text):
            core = tok[:-2] if tok.endswith("'s") else tok
            if sum(c.isupper() for c in core) < 2:
                continue
            raw.setdefault(core.lower(), Counter())[core] += 1
    # A plural ("ssps") joins its singular only if the singular exists in the data or the
    # vocabulary, so words that merely end in s ("devops", "ops") stay themselves.
    forms: Dict[str, Counter] = {}
    for key, c in raw.items():
        single = key[:-1] if key.endswith("s") and len(key) > 2 else None
        n_plural, n_single = sum(c.values()), sum(raw.get(single, Counter()).values()) if single else 0
        fold = (single is not None and key not in known_l
                and (single in known_l or (single in raw and n_single * 3 >= n_plural)))
        target = single if fold else key
        forms.setdefault(target, Counter()).update(c)
    out = []
    for key, c in forms.items():
        total = sum(c.values())
        if total < min_count or key in known_l:
            continue
        singular = [f for f in c if f.lower() == key]
        best = max(singular or list(c), key=lambda f: (c[f], f.isupper()))
        out.append({"term": best, "count": total, "forms": dict(c.most_common())})
    return sorted(out, key=lambda d: -d["count"])
