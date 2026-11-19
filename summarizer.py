"""Live meeting summary via GitHub Copilot (`/responses`, default gpt-6.1-terra).

Auth: the GitHub token from `gh auth token -u <user>` (RTT_COPILOT_GH_USER,
default: the active `gh` account). Token stays in memory; refreshed on 401.

Incremental: each update sends the previous summary + only the transcript lines
added since, so cost per call stays flat as the meeting grows. Updates run every
RTT_SUMMARY_INTERVAL seconds (default 60) when there are new lines, or on demand.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

COPILOT_URL = os.environ.get("RTT_COPILOT_URL", "https://api.githubcopilot.com/responses")
DEFAULT_MODEL = os.environ.get("RTT_SUMMARY_MODEL", "gpt-6.1-terra")

SECTIONS = ("Summary", "Decisions", "Action items", "Open questions")

INSTRUCTIONS = """You maintain a LIVE summary of a meeting transcript as it happens.
Transcripts come from speech recognition: expect errors, missing words and generic labels like
"Speaker 1". Numbered speakers are tracked by voice and stay the same person for the session;
"Speaker ?" is a too-short utterance whose voice was not identified. Prefer names when people are named.
Mixed Hebrew/English is normal. Write the summary in the main language of the meeting.

You receive the CURRENT summary and NEW transcript lines. Return the UPDATED summary, keeping
earlier points unless the new lines change them. Be short and concrete. Only include what was
actually said; never invent owners, dates or numbers. Lines in square brackets that start with a symbol
and "RTT" (e.g. "[★ RTT bookmark: …]", "[⏸ RTT paused]", "[⇄ backend …]") are NOT speech: they come
from the person running the transcription. Never attribute them to a speaker and never treat
"bookmark" as a name. A bookmark marks an important moment (its note, if any, says why): make sure
what was said around it is reflected.

Output exactly these four markdown sections, each a bullet list (use "- none yet" when empty):
## Summary
## Decisions
## Action items
## Open questions"""


def _gh_token(user: str) -> str:
    """Token for `user`, or for the active `gh` account when `user` is empty."""
    cmd = ["gh", "auth", "token"] + (["-u", user] if user else [])
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    tok = out.stdout.strip()
    if out.returncode != 0 or not tok:
        raise RuntimeError(f"{' '.join(cmd)} failed: {out.stderr.strip()[:200]}")
    return tok


def parse_sections(md: str) -> dict:
    """Split the model's markdown into {section: [bullets]}; tolerant of small format drift."""
    out = {k: [] for k in SECTIONS}
    cur = None
    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            cur = next((k for k in SECTIONS if k.lower() == title or title.startswith(k.lower())), None)
            continue
        if cur and line[:2] in ("- ", "* ", "• "):
            item = line[2:].strip()
            if item and item.lower() not in ("none yet", "none"):
                out[cur].append(item)
    return out


def render_sections(sections: dict) -> str:
    parts = []
    for k in SECTIONS:
        items = sections.get(k) or []
        parts.append(f"## {k}\n" + ("\n".join(f"- {i}" for i in items) if items else "- none yet"))
    return "\n\n".join(parts) + "\n"


class LiveSummarizer(threading.Thread):
    def __init__(
        self,
        get_lines: Callable[[], List[str]],
        out_path: Path,
        on_update: Optional[Callable[[dict], None]] = None,
        model: str = DEFAULT_MODEL,
        interval: float = float(os.environ.get("RTT_SUMMARY_INTERVAL", "60")),
        gh_user: str = os.environ.get("RTT_COPILOT_GH_USER", ""),
        max_new_lines: int = 400,
    ) -> None:
        super().__init__(daemon=True)
        self._get_lines = get_lines
        self._out = out_path
        self._on_update = on_update or (lambda s: None)
        self.model = model
        self.interval = interval
        self._gh_user = gh_user
        self._max_new = max_new_lines
        self._token: Optional[str] = None
        self._stop = threading.Event()
        self._now = threading.Event()
        self._consumed = 0
        self._renames = []
        self.summary_md = render_sections({})
        self.sections = parse_sections(self.summary_md)
        self.updates = 0
        self.last_update: Optional[float] = None
        self.last_error: Optional[str] = None
        self.busy = False
        self._renames: List[tuple] = []

    # -- control --
    def apply_rename(self, src: str, dst: str, remember: bool = True, bare: bool = False) -> None:
        """Rewrite speaker references already in the summary ("Speaker 0 said" -> "Idan said").

        Renames are remembered and re-applied after every model update, so a summary call
        that was already in flight with the old label cannot bring "Speaker 0" back."""
        import re
        if remember:
            self._renames.append((src, dst, bare))
        pats = [re.compile(rf"\bSpeaker\s+{re.escape(src)}\b")]
        if bare:  # src is a name we gave earlier ("Idan"), not a raw label like "A" or "0"
            pats.append(re.compile(rf"(?<!\w){re.escape(src)}(?!\w)"))
        new = {}
        for k, items in self.sections.items():
            out = []
            for it in items:
                for pat in pats:
                    it = pat.sub(dst, it)
                out.append(it)
            new[k] = out
        self.sections = new
        self.summary_md = render_sections(new)
        self._out.write_text(self.summary_md, encoding="utf-8")
        self._on_update(self.sections)

    def fork_final(self, lines: List[str], out_path: Path) -> "LiveSummarizer":
        """A copy that finishes the summary of a closed session from a snapshot of its lines."""
        f = LiveSummarizer(lambda: lines, out_path, model=self.model, gh_user=self._gh_user)
        f.sections, f.summary_md = dict(self.sections), self.summary_md
        f._consumed, f._renames, f._token = self._consumed, list(self._renames), self._token
        try:
            out_path.write_text(f.summary_md, encoding="utf-8")   # last good version, even if the final call fails
        except OSError:
            pass
        return f

    def resume_from(self, out_path: Path, consumed: int) -> bool:
        """Resumed session: continue the saved summary instead of starting over."""
        self._out = out_path
        self._consumed = consumed
        try:
            md = out_path.read_text(encoding="utf-8")
        except OSError:
            return False
        if not md.strip():
            return False
        self.sections = parse_sections(md)
        self.summary_md = render_sections(self.sections)
        self._on_update(self.sections)
        return True

    def reset(self, out_path: Path) -> None:
        """Start over for a new session, writing to `out_path`."""
        self._out = out_path
        self._consumed = 0
        self._renames = []
        self.summary_md = render_sections({})
        self.sections = parse_sections(self.summary_md)
        self.last_update = None
        self.last_error = None
        self.updates = 0
        self._out.write_text(self.summary_md, encoding="utf-8")
        self._on_update(self.sections)

    def rebuild(self) -> None:
        """Forget the summary and re-summarize from the current transcript (after a redaction)."""
        self._consumed = 0
        self.summary_md = render_sections({})
        self.sections = parse_sections(self.summary_md)
        self._out.write_text(self.summary_md, encoding="utf-8")
        self._on_update(self.sections)
        self._now.set()

    def request_now(self) -> None:
        self._now.set()

    def stop(self) -> None:
        self._stop.set()
        self._now.set()

    def status(self) -> dict:
        return {"model": self.model, "updates": self.updates, "busy": self.busy,
                "last_update": time.strftime("%H:%M:%S", time.localtime(self.last_update)) if self.last_update else None,
                "error": self.last_error}

    # -- work --
    def run(self) -> None:
        while not self._stop.is_set():
            self._now.wait(timeout=self.interval)
            forced = self._now.is_set()
            self._now.clear()
            if self._stop.is_set():
                break
            self.update_once(force=forced)

    def update_once(self, force: bool = False) -> bool:
        lines = self._get_lines()
        new = lines[self._consumed:]
        if not new and not force:
            return False
        if not new:
            return False
        new = new[-self._max_new:]
        self.busy = True
        try:
            md = self._call(self.summary_md, new)
            self._consumed = len(lines)
            self.sections = parse_sections(md)
            self.summary_md = render_sections(self.sections)
            for src, dst, bare in self._renames:
                self.apply_rename(src, dst, remember=False, bare=bare)
            self._out.write_text(self.summary_md, encoding="utf-8")
            self.updates += 1
            self.last_update = time.time()
            self.last_error = None
            self._on_update(self.sections)
            return True
        except Exception as exc:  # summary is optional: never disturb transcription
            self.last_error = str(exc)[:160]
            return False
        finally:
            self.busy = False

    def _call(self, current: str, new_lines: List[str]) -> str:
        body = {
            "model": self.model,
            "instructions": INSTRUCTIONS,
            "input": f"CURRENT SUMMARY:\n{current}\n\nNEW TRANSCRIPT LINES:\n" + "\n".join(new_lines),
            "reasoning": {"effort": "low"},
            "max_output_tokens": 2500,
        }
        data: dict = {}
        for attempt in range(2):
            if self._token is None:
                self._token = _gh_token(self._gh_user)
            req = urllib.request.Request(
                COPILOT_URL, json.dumps(body).encode("utf-8"),
                {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                 "Copilot-Integration-Id": "vscode-chat", "Editor-Version": "rtt/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0:
                    self._token = None
                    continue
                raise RuntimeError(f"HTTP {e.code}: {e.read()[:160].decode('utf-8', 'replace')}")
        text = "".join(
            c.get("text", "") for o in data.get("output", []) if o.get("type") == "message"
            for c in o.get("content", []) if c.get("type") == "output_text"
        )
        if not text.strip():
            raise RuntimeError("empty summary response")
        return text
