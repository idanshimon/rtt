"""Optional AI step for lines the words can't decide. A resolver is any callable prompt -> text.

Built in: "copilot" (GitHub Copilot /responses; `gh auth token [-u <RTT_COPILOT_GH_USER>]`) and
"none". Add another backend with `register_resolver("azure", my_fn)`.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence

from .clock import fmt
from .model import Line, LineMatch, Turn

Resolver = Callable[[str], str]
RESOLVERS: Dict[str, Optional[Resolver]] = {}

COPILOT_URL = os.environ.get("RTT_COPILOT_URL", "https://api.githubcopilot.com/responses")
DEFAULT_MODEL = os.environ.get("RTT_SUMMARY_MODEL", "gpt-6.1-terra")


def register_resolver(name: str, fn: Optional[Resolver]) -> None:
    RESOLVERS[name] = fn


def get_resolver(name_or_fn) -> Optional[Resolver]:
    if name_or_fn is None or callable(name_or_fn):
        return name_or_fn
    if name_or_fn not in RESOLVERS:
        raise ValueError(f"unknown resolver {name_or_fn!r}; known: {', '.join(sorted(RESOLVERS))}")
    return RESOLVERS[name_or_fn]


def copilot_call(prompt: str, model: Optional[str] = None) -> str:
    user = os.environ.get("RTT_COPILOT_GH_USER", "")
    cmd = ["gh", "auth", "token"] + (["-u", user] if user else [])
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    token = out.stdout.strip()
    if out.returncode != 0 or not token:
        raise RuntimeError(f"{' '.join(cmd)} failed: {out.stderr.strip()[:200]}")
    body = {"model": model or DEFAULT_MODEL, "input": prompt, "reasoning": {"effort": "low"},
            "max_output_tokens": 4000}
    req = urllib.request.Request(COPILOT_URL, json.dumps(body).encode("utf-8"),
                                 {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                  "Copilot-Integration-Id": "vscode-chat", "Editor-Version": "rtt/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    return "".join(c.get("text", "") for o in data.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") == "output_text")


def copilot_resolver(prompt: str) -> str:
    return copilot_call(prompt)        # looked up at call time, so tests/patches can replace it


register_resolver("copilot", copilot_resolver)
register_resolver("none", None)


def resolve_unnamed(lines: Sequence[Line], turns: List[Turn], matches: List[LineMatch],
                    resolver: Resolver) -> int:
    """Fill lines still without a name; only answers that are one of the turns' names count."""
    todo = [m for m in matches if m.name is None]
    if not todo:
        return 0
    by_index = {ln.index: ln for ln in lines}
    names = sorted({t.name for t in turns})
    meeting = "\n".join(f"[{fmt(t.t)}] {t.name}: {t.text}" for t in turns)[:20000]
    rtt = "\n".join(f"{m.index}: {by_index[m.index].raw}" for m in todo if m.index in by_index)[:12000]
    prompt = ("Below is a meeting app's transcript with real names, and lines from a second automatic transcript "
              "of the SAME meeting whose speakers are unknown. For each numbered line, answer which person said it, "
              f"using only these names: {', '.join(names)}. Use wording and timing. If you cannot tell, use null.\n"
              "Answer only JSON: {\"<line number>\": \"<name or null>\"}.\n\n"
              f"MEETING APP TRANSCRIPT:\n{meeting}\n\nLINES:\n{rtt}")
    txt = resolver(prompt)
    try:
        data = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except ValueError:
        return 0
    n = 0
    for m in todo:
        v = data.get(str(m.index))
        if v in names:
            m.name, m.how = v, "ai"
            n += 1
    return n
