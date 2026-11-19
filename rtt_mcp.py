#!/usr/bin/env python3
"""MCP server for RTT: lets an agent (Hermes, Copilot, Claude) drive a running RTT session.

Stdlib only (the repo venv is Python 3.9; the `mcp` package needs 3.10+). Speaks MCP
JSON-RPC over stdio. Every action goes through the same control socket as the keys and
`rtt-ctl`, so the session's action ledger (and undo) covers agent actions too.

Register (Hermes config.yaml):
  mcp_servers:
    rtt:
      command: /path/to/rtt/rtt-mcp.sh
"""
import collections
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
# stdout is the protocol channel: anything an imported module prints must go to stderr.
_OUT = sys.stdout
sys.stdout = sys.stderr
from session import send_command  # noqa: E402

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_CHARS = 40000                         # cap tool text so a long meeting can't flood the agent context
SLOW_CMDS = {"names", "refine"}           # AI name matching can take a while
SESSIONS = Path(os.environ.get("RTT_SESSIONS_DIR", str(REPO / "sessions")))
WRAPPERS = {
    "openai": "run-openai.sh", "llmspeech": "run-llmspeech.sh", "mai": "run-mai.sh",
    "vibe": "run-vibe.sh", "azure": "run-azure.sh", "azure-heb": "run-azure-heb.sh",
}
APPS = {"terminal": "Terminal", "iterm": "iTerm", "warp": "Warp"}
BACKENDS = ["openai", "llmspeech", "mai", "vibe", "azure", "local"]


class ToolError(Exception):
    pass


def ctl(cmd: str, **args) -> dict:
    timeout = float(os.environ.get("RTT_MCP_TIMEOUT", "180" if cmd in SLOW_CMDS else "15"))
    try:
        return send_command({"cmd": cmd, **{k: v for k, v in args.items() if v is not None}}, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        raise ToolError("RTT is not running (no control socket). Use rtt_start to launch it.")
    except socket.timeout:             # Python 3.9: not yet an alias of TimeoutError
        raise ToolError(f"RTT did not answer '{cmd}' within {timeout:.0f} s")


def running() -> bool:
    try:
        send_command({"cmd": "status"}, timeout=2)
        return True
    except OSError:
        return False


def _session_dir(session: str = "") -> Path:
    if not session:
        if running():
            return Path(ctl("status")["status"]["session_dir"])
        dirs = sorted(d for d in SESSIONS.iterdir() if d.is_dir()) if SESSIONS.exists() else []
        if not dirs:
            raise ToolError("no sessions found")
        return dirs[-1]
    d = SESSIONS / session
    if d.is_dir():
        return d
    hits = sorted(x for x in SESSIONS.iterdir() if x.is_dir() and session.lower() in x.name.lower())
    if not hits:
        raise ToolError(f"no session matching '{session}'")
    return hits[-1]


# ---------------------------------------------------------------- tool impls
def t_start(backend="openai", name="", resume="", summary=None, window="terminal"):
    if running():
        raise ToolError("RTT is already running; use rtt_new_session to start a new session in it.")
    if window != "headless" and window not in APPS:
        raise ToolError(f"window must be one of {', '.join(list(APPS) + ['headless'])}")
    if backend not in WRAPPERS:
        raise ToolError(f"backend must be one of {', '.join(WRAPPERS)}")
    argv = [str(REPO / WRAPPERS[backend])]
    if name:
        argv += ["--session-name", name]
    if resume:
        argv += ["-c"] if resume == "latest" else ["--resume", resume]
    if summary is not None:
        argv.append("--summary" if summary else "--no-summary")
    cmd = " ".join(shlex.quote(a) for a in argv)
    if window == "headless":
        log = Path(os.environ.get("TMPDIR", "/tmp")) / "rtt-mcp-headless.log"
        with open(log, "a") as fh:
            subprocess.Popen(argv + ["--no-tui"], cwd=str(REPO), stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    else:
        # A .command file opened with `open -a` needs no AppleScript Automation permission
        # (osascript can hang forever on that permission prompt).
        run = Path(os.environ.get("TMPDIR", "/tmp")) / "rtt-start.command"
        run.write_text(f"#!/bin/zsh -l\ncd {shlex.quote(str(REPO))}\nexec {cmd}\n")
        run.chmod(0o755)
        subprocess.run(["open", "-a", APPS[window], str(run)], check=True, capture_output=True, timeout=15)
    for _ in range(60):
        time.sleep(0.5)
        if running():
            return {"ok": True, "started": backend, "window": window, "status": ctl("status")["status"]}
    return {"ok": False, "error": "launched, but the control socket did not appear within 30 s; check the RTT window"}


TS = re.compile(r"^\[(\d{2}:\d{2}(?::\d{2})?)\]")


def _cap(text: str) -> tuple:
    """Keep the newest MAX_CHARS characters, cut at a line start."""
    if len(text) <= MAX_CHARS:
        return text, False
    cut = text[-MAX_CHARS:]
    return cut[cut.find("\n") + 1:], True


def t_transcript(last_lines=40, session="", since=""):
    d = _session_dir(session)
    f = d / "transcript.txt"
    if not f.exists():
        raise ToolError(f"no transcript in {d}")
    if since and not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", since):
        raise ToolError("since must be HH:MM or HH:MM:SS")
    since = since.zfill(5) if len(since) <= 5 else since.zfill(8)
    total, keep, on = 0, collections.deque(maxlen=int(last_lines) if last_lines and last_lines > 0 else None), not since
    with open(f, encoding="utf-8", errors="replace") as fh:   # stream: no full copy of a long meeting in memory
        for ln in fh:
            total += 1
            m = TS.match(ln)
            if since and m:
                on = m.group(1)[:len(since)] >= since
            if on:                                  # untimed lines follow the line before them
                keep.append(ln.rstrip("\n"))
    text, truncated = _cap("\n".join(keep))
    return {"session": d.name, "file": str(f), "total_lines": total, "returned_lines": text.count("\n") + bool(text),
            "truncated": truncated, "text": text}


def t_summary(session=""):
    d = _session_dir(session)
    f = d / "summary.md"
    text, truncated = _cap(f.read_text(encoding="utf-8") if f.exists() else "")
    return {"session": d.name, "summary": text, "truncated": truncated}


def t_sessions(limit=10):
    dirs = sorted((d for d in SESSIONS.iterdir() if d.is_dir()), reverse=True)[: int(limit)] if SESSIONS.exists() else []
    out = []
    for d in dirs:
        meta = {}
        try:
            meta = json.loads((d / "session.json").read_text())
        except (OSError, ValueError):
            pass
        out.append({"id": d.name, "name": meta.get("name") or meta.get("meeting_title"),
                    "backend": meta.get("backend"), "has_summary": (d / "summary.md").exists()})
    return {"running": running(), "sessions": out}


def t_log(last=20):
    r = ctl("log")
    if r.get("ok"):
        r["entries"] = r["entries"][-int(last):]
    return r


def t_names(text, refine=False, learn=False, ai=True, dry_run=False):
    return ctl("refine" if refine else "names", text=text, learn=learn, ai=ai, dry_run=dry_run)


def t_offrecord(minutes=None, at=None):
    return ctl("offrecord", minutes=minutes, at=at) if at else ctl("offrecord", minutes=minutes if minutes is not None else 2)


S = lambda **p: {"type": "object", "properties": p}
STR, INT, NUM, BOOL = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}

TOOLS = {
    "rtt_status": ("State of the running session: live/paused, backend, elapsed, mic, speakers, health, files.",
                   S(), lambda: ctl("status")),
    "rtt_start": ("Launch RTT (when not running). backend: openai (=rtt) | llmspeech (=rttheb) | mai | vibe (=rttvibe) | "
                  "azure (=rttold) | azure-heb (=rtthebold). window: terminal (default, new window with the split view) | "
                  "iterm | warp | headless. resume: a session id, or 'latest'.",
                  S(backend={**STR, "enum": list(WRAPPERS)}, name=STR, resume=STR, summary=BOOL,
                    window={**STR, "enum": ["terminal", "iterm", "warp", "headless"]}), t_start),
    "rtt_stop": ("End the session cleanly (transcript, audio and summary are saved; rtt_start resume='latest' continues it).",
                 S(), lambda: ctl("stop")),
    "rtt_new_session": ("Close the current session and start a new one in the same process (capture keeps running).",
                        S(name=STR), lambda name="": ctl("new", name=name)),
    "rtt_pause": ("Pause transcription and recording.", S(), lambda: ctl("pause")),
    "rtt_resume": ("Resume after a pause.", S(), lambda: ctl("resume")),
    "rtt_mute": ("Mute (true) or unmute (false) your own mic only; others keep being transcribed.",
                 {**S(muted=BOOL), "required": ["muted"]}, lambda muted: ctl("mute" if muted else "unmute")),
    "rtt_mark": ("Bookmark this moment, optionally with a note.", S(note=STR), lambda note="": ctl("mark", note=note)),
    "rtt_rename_speaker": ("Rename a speaker in past and future lines and the summary (e.g. from='Speaker B' or '0', to='Al').",
                           {**S(**{"from": STR, "to": STR}), "required": ["from", "to"]},
                           lambda **a: ctl("rename", **a)),
    "rtt_expected_speakers": ("Tell RTT how many people are in the call (0 = unknown); stops voice over-splitting.",
                              {**S(n=INT), "required": ["n"]}, lambda n: ctl("speakers", n=n)),
    "rtt_names_from_teams": ("Put real names on lines using a pasted Teams/Zoom transcript text. refine=true also takes its "
                             "wording; learn=true adds misheard names to the private dictionary; dry_run previews.",
                             {**S(text=STR, refine=BOOL, learn=BOOL, ai=BOOL, dry_run=BOOL), "required": ["text"]}, t_names),
    "rtt_off_record": ("Pause and DELETE the last N minutes (default 2) of text, summary and audio, or everything from a "
                       "line's time on (at='09:15:09').", S(minutes=NUM, at=STR), t_offrecord),
    "rtt_undo": ("Undo the last undoable action, or ledger entry id.", S(id=INT), lambda id=None: ctl("undo", id=id)),
    "rtt_log": ("Recent action-ledger entries (every user/agent action; undoable ones marked).", S(last=INT), t_log),
    "rtt_switch_backend": ("Switch the transcription backend live.", {**S(backend={**STR, "enum": BACKENDS}), "required": ["backend"]},
                           lambda backend: ctl("switch", backend=backend)),
    "rtt_chunk": ("Chunk window: seconds (e.g. '8'), '+', '-', 'auto' or 'fixed'.", {**S(value=STR), "required": ["value"]},
                  lambda value: ctl("chunk", value=value)),
    "rtt_summarize_now": ("Refresh the live summary now.", S(), lambda: ctl("summarize")),
    "rtt_quality": ("Live quality: voices, % short lines, % unknown speaker, warnings.", S(), lambda: ctl("quality")),
    "rtt_tidy": ("Remove pause/resume/mute/bookmark lines that came in accidental bursts (undoable).", S(), lambda: ctl("tidy")),
    "rtt_transcript": ("Read the transcript (live session, or a past one by id/name fragment). last_lines=0 for all; "
                       "since='HH:MM:SS' for lines from that time.", S(last_lines=INT, session=STR, since=STR), t_transcript),
    "rtt_summary": ("Read the session's live summary (summary.md).", S(session=STR), t_summary),
    "rtt_sessions": ("List recent sessions and whether RTT is running.", S(limit=INT), t_sessions),
    "rtt_command": ("Raw control-socket command for anything not covered above (cmd + args).",
                    {**S(cmd=STR, args={"type": "object"}), "required": ["cmd"]},
                    lambda cmd, args=None: ctl(cmd, **(args or {}))),
}


READ_ONLY = {"rtt_status", "rtt_log", "rtt_quality", "rtt_transcript", "rtt_summary", "rtt_sessions"}
DESTRUCTIVE = {"rtt_off_record", "rtt_stop", "rtt_tidy", "rtt_command"}


def tool_list() -> list:
    out = []
    for n, (d, s, _) in TOOLS.items():
        out.append({"name": n, "description": d, "inputSchema": s,
                    "annotations": {"readOnlyHint": n in READ_ONLY, "destructiveHint": n in DESTRUCTIVE,
                                    "openWorldHint": False}})
    return out


INSTRUCTIONS = ("Controls the user's live meeting transcriber (RTT). Actions are logged in the session's action ledger "
                "and most can be undone with rtt_undo; rtt_off_record deletes text and audio for good. Read with "
                "rtt_transcript / rtt_summary; check rtt_status before acting.")


def call_tool(name: str, args: dict) -> dict:
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name}")
    return TOOLS[name][2](**(args or {}))


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None                                  # notification
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        res = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
               "capabilities": {"tools": {}}, "serverInfo": {"name": "rtt", "version": "1.1"},
               "instructions": INSTRUCTIONS}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": tool_list()}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            out = call_tool(p.get("name"), p.get("arguments") or {})
            err = isinstance(out, dict) and out.get("ok") is False
            res = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}], "isError": err}
        except ToolError as exc:
            res = {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True}
        except Exception as exc:                    # a bad call must never kill the server
            res = {"content": [{"type": "text", "text": f"error: {type(exc).__name__}: {exc}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if resp is not None:
            _OUT.write(json.dumps(resp, ensure_ascii=False) + "\n")
            _OUT.flush()


if __name__ == "__main__":
    main()
