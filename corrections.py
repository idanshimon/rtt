"""Vocabulary correction for RTT transcripts.

Post-transcription find/replace to fix recurring domain-term garbles the STT
model produces (e.g. "high trust" -> "HITRUST", "a car" -> "ACR"). Rules live in
`corrections.json`. Two rule kinds:

- literal:  unconditional whole-word/phrase replacement. Use ONLY when the wrong
            form never occurs legitimately in the user's meetings.
- guarded:  replace only when a context word (`near`) appears in the same line.
            Use when the wrong form is also a real word ("fire" -> "FHIR" only
            near epic/hl7/interop/...), so "rapid fire" is left alone.

Applied per emitted line at the chunked-backend emit choke point. Whole-word,
case-insensitive matching; the replacement string's casing is used verbatim.
Fails open: any load/parse error disables corrections rather than killing a run.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional


class Corrections:
    """Loads correction rules and applies them to transcript lines."""

    def __init__(self, literal: Optional[list] = None, guarded: Optional[list] = None) -> None:
        self._literal = []  # list of (compiled_pattern, replacement)
        self._guarded = []  # list of (compiled_pattern, replacement, [near_words_lower])
        self.vocab = None   # meetalign.vocab.Vocabulary: casing of known terms, set by load()
        for rule in literal or []:
            wrong = (rule.get("wrong") or "").strip()
            right = rule.get("right")
            if not wrong or right is None:
                continue
            self._literal.append((self._compile(wrong, bool(rule.get("case"))), right))
        for rule in guarded or []:
            wrong = (rule.get("wrong") or "").strip()
            right = rule.get("right")
            near = [w.lower() for w in (rule.get("near") or []) if w]
            avoid = [w.lower() for w in (rule.get("avoid") or []) if w]
            if not wrong or right is None or not near:
                continue
            self._guarded.append((self._compile(wrong), right, near, avoid))

    @staticmethod
    def _compile(phrase: str, case: bool = False) -> "re.Pattern":
        # Whole-word (or whole-phrase) boundary; case-insensitive unless the rule says "case".
        return re.compile(r"\b" + re.escape(phrase) + r"\b", 0 if case else re.IGNORECASE)

    @property
    def active(self) -> bool:
        return bool(self._literal or self._guarded or self.vocab)

    def apply(self, line: str) -> str:
        """Return `line` with all applicable corrections applied.

        Guarded rules gate on the ORIGINAL line's lowercase content so one
        rule's replacement can't accidentally satisfy another's context test.
        """
        if not line:
            return line
        gate = line.lower()
        out = line
        for pat, repl in self._literal:
            out = pat.sub(repl, out)
        for pat, repl, near, avoid in self._guarded:
            if any(w in gate for w in avoid):
                continue  # protected idiom/phrase present → leave the word alone
            if any(w in gate for w in near):
                out = pat.sub(repl, out)
        if self.vocab is not None:
            out = self.vocab.normalize(out)
        return out

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Corrections":
        """Load rules from `path` (default: corrections.json beside this file).

        Fails open: missing file or bad JSON yields an inert (no-op) instance.
        Override path via env RTT_CORRECTIONS_FILE; set it to empty/"none" to
        disable corrections entirely.
        """
        env = os.environ.get("RTT_CORRECTIONS_FILE")
        if env is not None:
            if env.strip().lower() in ("", "none", "off", "0", "false"):
                return cls()
            path = env
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corrections.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}                      # no rules file: learned words and vocabulary still apply
        if not isinstance(data, dict):
            data = {}
        literal = list(data.get("literal") or [])
        learned = learned_path()
        if learned:
            try:
                with open(learned, encoding="utf-8") as fh:
                    literal += list((json.load(fh) or {}).get("literal") or [])
            except (OSError, ValueError, AttributeError):
                pass
        c = cls(literal=literal, guarded=data.get("guarded"))
        v = load_vocabulary()
        c.vocab = v if len(v) else None
        return c


def vocabulary_paths() -> List[str]:
    """Shared (repo) + private term lists. RTT_VOCABULARY = extra path(s, ':'-separated) or "none"."""
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.get("RTT_VOCABULARY")
    if env is not None and env.strip().lower() in ("", "none", "off", "0", "false"):
        return []
    paths = [os.path.join(here, "vocabulary.json"), os.path.join(here, "private", "vocabulary.json")]
    if env:
        paths += [p for p in env.split(":") if p]
    return paths


_COMMON = None


def common_words() -> set:
    """Ordinary English words (/usr/share/dict/words): never turned into a term ("it" -> "IT")."""
    global _COMMON
    if _COMMON is None:
        try:
            with open("/usr/share/dict/words", encoding="utf-8", errors="ignore") as fh:
                _COMMON = {w.strip().lower() for w in fh if w.strip()}
        except OSError:
            _COMMON = set()
    return _COMMON


def load_vocabulary():
    from meetalign.vocab import Vocabulary
    terms = []
    for p in vocabulary_paths():
        try:
            with open(p, encoding="utf-8") as fh:
                terms += list((json.load(fh) or {}).get("terms") or [])
        except (OSError, ValueError, AttributeError):
            pass
    common = common_words()
    if not common:   # no dictionary: never touch all-lowercase words at all
        common = _AllLower()
    return Vocabulary(terms, common)


class _AllLower(set):
    def __contains__(self, item) -> bool:   # every lowercase token counts as an ordinary word
        return True


def add_terms(terms: List[str], private: bool = True) -> List[str]:
    """Add terms to the private (default) or shared vocabulary file; returns the ones added."""
    paths = vocabulary_paths()
    if not paths:
        return []
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "private", "vocabulary.json") if private else os.path.join(here, "vocabulary.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {"_comment": "Private vocabulary: customer and people names. Not in git."}
    have = {str(t).lower() for t in data.get("terms", [])}
    added = [t.strip() for t in terms if t.strip() and t.strip().lower() not in have]
    if added:
        data["terms"] = sorted(set(data.get("terms", [])) | set(added))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    return added


def remove_terms(terms: List[str]) -> List[str]:
    removed = []
    for path in vocabulary_paths():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        drop = {t.lower() for t in terms}
        keep = [t for t in data.get("terms", []) if t.lower() not in drop]
        if len(keep) != len(data.get("terms", [])):
            removed += [t for t in data.get("terms", []) if t.lower() in drop]
            data["terms"] = keep
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
    return removed


def phrase_list(limit: int = 500) -> List[str]:
    """Terms to send to backends that accept a phrase list / hotwords, before transcription.
    RTT_PHRASE_LIST=0 turns it off (post-correction still runs)."""
    if os.environ.get("RTT_PHRASE_LIST", "1").strip().lower() in ("0", "off", "false", "no", "none"):
        return []
    return load_vocabulary().phrases(limit)


def learned_path() -> Optional[str]:
    """Words learned from meeting-app transcripts (`T`). Private: names of real people, kept
    out of git (private/ is ignored). RTT_LEARNED_CORRECTIONS overrides; "none" disables."""
    env = os.environ.get("RTT_LEARNED_CORRECTIONS")
    if env is not None:
        return None if env.strip().lower() in ("", "none", "off", "0", "false") else env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "corrections.learned.json")


def learn(pairs: List[tuple], source: str = "") -> List[dict]:
    """Add (heard, right) rules to the learned file; returns the rules actually added.
    A heard word written with a capital (a misheard name) only matches with that capital, so
    learning "Urban" -> "Irvin" leaves "urban planning" alone."""
    path = learned_path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    rules = list(data.get("literal") or [])
    have = {(r.get("wrong", "").lower(), r.get("right")) for r in rules}
    added = []
    for heard, right in pairs:
        if not heard or not right or (heard.lower(), right) in have:
            continue
        rule = {"wrong": heard, "right": right, "case": heard[:1].isupper(), "from": source}
        rules.append(rule)
        added.append(rule)
        have.add((heard.lower(), right))
    if added:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["_comment"] = "Learned by RTT from pasted Teams/Zoom transcripts (T). Private; not in git."
        data["literal"] = rules
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return added
