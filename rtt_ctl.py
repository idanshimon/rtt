#!/usr/bin/env python3
"""Control a running RTT session from any terminal, script, Raycast or agent.

  rtt-ctl status            JSON status (state, elapsed, recording, marks, summary, health)
  rtt-ctl pause | resume | toggle
  rtt-ctl mute | unmute     your mic only (others keep being transcribed)
  rtt-ctl mark [note...]    flag this moment in the transcript
  rtt-ctl summarize         refresh the live summary now
  rtt-ctl offrecord [min|HH:MM:SS]  pause and delete the last N minutes (default 2), or everything
                            from a line's time on (e.g. offrecord 09:15:09), of text, summary and audio
  rtt-ctl tidy [--file F]   remove pause/resume/mute/bookmark lines that came in bursts (keys
                            fired by accident); --file F tidies a transcript after the session ended
  rtt-ctl speakers <n>      how many people are in the call (0 = unknown); stops voice splitting
  rtt-ctl quality           live quality: voices, % short lines, % unknown speaker, warnings
  rtt-ctl names [file] [--no-ai] [--dry-run]   name speakers from a Teams/Zoom transcript
                            (file, or the clipboard when no file is given)
  rtt-ctl refine [file] [--learn] [--no-ai] [--dry-run]   names + the app's wording for matched lines;
                            --learn also adds misheard names/terms to the private dictionary
  rtt-ctl rename <from> <to>   rename a speaker (e.g. rename 0 Idan); also fixes the summary
  rtt-ctl log               the session's action ledger (every user action, undoable ones marked)
  rtt-ctl undo [id]         undo the last undoable action, or ledger entry <id>
  rtt-ctl chunk <s|+|-|auto|fixed>  chunk window: seconds, step, or auto (cut at pauses, adapt)
  rtt-ctl switch <backend>  change backend live: openai | llmspeech | mai | vibe | azure | local
  rtt-ctl new [name]        close this session and start a new one (capture keeps running)
  rtt-ctl stop              end the session cleanly
"""
import json
import sys

from session import SOCKET_PATH, send_command

USAGE = __doc__


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE.strip())
        return 0
    cmd = {"cmd": argv[0]}
    if argv[0] == "mark":
        cmd["note"] = " ".join(argv[1:])
    elif argv[0] == "offrecord":
        arg = " ".join(argv[1:]).strip()
        try:
            cmd["minutes"] = float(arg) if arg else 2
        except ValueError:
            cmd["at"] = arg                      # a line's time (09:15:09) or 90s / 2m / 2min: server parses
    elif argv[0] == "tidy" and "--file" in argv:
        from pathlib import Path
        from session import tidy_file
        f = Path(argv[argv.index("--file") + 1])
        print(json.dumps({"ok": True, "removed": tidy_file(f), "file": str(f)}))
        return 0
    elif argv[0] in ("names", "refine"):
        files = [a for a in argv[1:] if not a.startswith("--")]
        if files:
            cmd["text"] = sys.stdin.read() if files[0] == "-" else open(files[0], encoding="utf-8").read()
        else:
            import subprocess
            cmd["text"] = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        cmd["ai"] = "--no-ai" not in argv
        cmd["dry_run"] = "--dry-run" in argv
        cmd["learn"] = "--learn" in argv
    elif argv[0] == "speakers":
        if len(argv) < 2:
            print("usage: rtt-ctl speakers <n>", file=sys.stderr)
            return 2
        cmd["n"] = int(argv[1])
    elif argv[0] == "rename":
        if len(argv) < 3:
            print("usage: rtt-ctl rename <from> <to>", file=sys.stderr)
            return 2
        cmd["from"], cmd["to"] = argv[1], " ".join(argv[2:])
    elif argv[0] == "undo":
        if len(argv) > 1:
            cmd["id"] = int(argv[1])
    elif argv[0] == "new":
        cmd["name"] = " ".join(argv[1:])
    elif argv[0] == "chunk":
        if len(argv) < 2:
            print("usage: rtt-ctl chunk <seconds|+|-|auto|fixed>", file=sys.stderr)
            return 2
        cmd["value"] = argv[1]
    elif argv[0] == "switch":
        if len(argv) < 2:
            print("usage: rtt-ctl switch <backend>", file=sys.stderr)
            return 2
        cmd["backend"] = argv[1]
    try:
        resp = send_command(cmd, timeout=150 if argv[0] in ("names", "refine") else 3.0)
    except (FileNotFoundError, ConnectionRefusedError):
        print(f"No running RTT session (no socket at {SOCKET_PATH}).", file=sys.stderr)
        return 2
    if argv[0] == "status" and resp.get("ok"):
        print(json.dumps(resp["status"], ensure_ascii=False, indent=1))
    elif argv[0] == "log" and resp.get("ok"):
        reverted = set(resp.get("reverted", []))
        for e in resp["entries"]:
            flag = " (undone)" if e["id"] in reverted else (" [undoable]" if "undo" in e else "")
            print(f"#{e['id']:<3} {e['at'][11:]} {e['source']:<6} {e['action']:<16} "
                  f"{json.dumps(e['params'], ensure_ascii=False)}{flag}")
    else:
        print(json.dumps(resp, ensure_ascii=False))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
