#!/usr/bin/env python3
"""rtt-align: line up an RTT transcript with a Teams/Zoom transcript, from the command line.

Works on any transcript file, without a running RTT. Shows what would change unless --write.

  rtt-align parse   MEETING                        what was read from the paste (turns, people)
  rtt-align names   TRANSCRIPT MEETING             real names on lines
  rtt-align refine  TRANSCRIPT MEETING [--learn]   names + the app's wording; --learn adds misheard
                                                   names to the private dictionary
  rtt-align tidy    TRANSCRIPT                     remove pause/mute/bookmark bursts
  rtt-align learned                                list the learned words
  rtt-align vocab list | add TERM… [--shared] | remove TERM… | mine [FILES…] [--add N]
                                                   domain terms (NWND, SSPs…): right casing on every
                                                   backend + phrase list where supported. Terms go to
                                                   private/vocabulary.json unless --shared.

TRANSCRIPT  a transcript file, a session id/name (as for `rtt -c`), or "live" (the running RTT,
            applied there and undoable with z)
MEETING     a file, "-" for stdin, or "clip" for the clipboard

Options:
  --write          change the file (a .bak copy is kept); default only shows the diff
  -o FILE          write the result to FILE instead
  --json           machine-readable result
  --ai NAME        copilot (default) or none; resolves lines words can't decide
  --format NAME    transcript line layout (default rtt; see meetalign.register_line_format)
  --source NAME    meeting text format (default auto; see meetalign.register_source)
  --window S       time window in seconds (default 75)
Exit code: 0 ok, 1 nothing matched / nothing to do, 2 usage or input error.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from meetalign import align, find_bursts, parse_meeting  # noqa: E402
from meetalign.clock import fmt  # noqa: E402


def read_meeting(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    if arg == "clip":
        return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    return Path(arg).read_text(encoding="utf-8")


def resolve_transcript(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    from session import find_session
    d = find_session(HERE / "sessions", arg)
    meta = json.loads((d / "session.json").read_text(encoding="utf-8"))
    return Path(meta["transcript"])


def show_diff(changes, before, out=sys.stdout) -> None:
    for i in sorted(changes):
        out.write(f"- {before[i]}\n+ {changes[i]}\n")


def write_out(path: Path, lines, changes, args) -> str:
    new = [changes.get(i, ln) for i, ln in enumerate(lines)]
    target = Path(args.o) if args.o else path
    if not args.o:
        shutil.copy2(path, str(path) + ".bak")
    target.write_text("\n".join(new) + "\n", encoding="utf-8")
    return str(target)


def cmd_parse(args) -> int:
    turns = parse_meeting(read_meeting(args.meeting), args.source)
    if args.json:
        print(json.dumps([{"name": t.name, "time": fmt(t.t), "text": t.text} for t in turns], ensure_ascii=False))
    else:
        people = sorted({t.name for t in turns})
        print(f"{len(turns)} turns, {len(people)} people: {', '.join(people)}")
        for t in turns:
            print(f"  [{fmt(t.t)}] {t.name}: {t.text[:90]}")
    return 0 if turns else 1


def cmd_align(args, refine: bool) -> int:
    meeting = read_meeting(args.meeting)
    if args.transcript == "live":
        from session import send_command
        resp = send_command({"cmd": "refine" if refine else "names", "text": meeting, "ai": args.ai != "none",
                             "learn": bool(getattr(args, "learn", False)), "dry_run": not args.write}, timeout=150)
        print(json.dumps(resp, ensure_ascii=False) if args.json else resp.get("summary") or resp.get("error"))
        return 0 if resp.get("ok") else 1
    path = resolve_transcript(args.transcript)
    lines = path.read_text(encoding="utf-8").splitlines()
    r = align(lines, meeting, line_format=args.format, source=args.source, refine=refine,
              resolver=None if args.ai == "none" else args.ai, window_s=args.window)
    if not r.turns:
        print("No named turns found in the meeting text.", file=sys.stderr)
        return 2
    learned = []
    if args.write or args.o:
        where = write_out(path, lines, r.changes, args) if r.changes else None
        if refine and getattr(args, "learn", False) and r.learn:
            import corrections
            learned = corrections.learn(r.learn, source=f"rtt-align {path.name}")
    if args.json:
        d = r.to_dict()
        d["learned"] = learned
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        if not (args.write or args.o):
            show_diff(r.changes, r.lines_before)
        print(r.summary())
        if r.ai_error:
            print(f"AI skipped: {r.ai_error}")
        if r.learn:
            print("words to learn: " + ", ".join(f"{h}→{w}" for h, w in r.learn)
                  + ("" if learned else "  (use --learn with --write to add them)"))
        if learned:
            print(f"learned {len(learned)} word(s)")
        if args.write or args.o:
            print(f"wrote {where}" if r.changes else "nothing to write")
    return 0 if r.changes else 1


def cmd_tidy(args) -> int:
    if args.transcript == "live":
        from session import send_command
        print(json.dumps(send_command({"cmd": "tidy"})))
        return 0
    path = resolve_transcript(args.transcript)
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = set(find_bursts(lines))
    if args.json:
        print(json.dumps({"remove": len(idx), "lines": [lines[i] for i in sorted(idx)]}, ensure_ascii=False))
    elif not (args.write or args.o):
        for i in sorted(idx):
            print(f"- {lines[i]}")
    if (args.write or args.o) and idx:
        target = Path(args.o) if args.o else path
        if not args.o:
            shutil.copy2(path, str(path) + ".bak")
        target.write_text("\n".join(ln for i, ln in enumerate(lines) if i not in idx) + "\n", encoding="utf-8")
    if not args.json:
        print(f"{len(idx)} burst lines" + (" removed" if (args.write or args.o) and idx else ""))
    return 0 if idx else 1


def cmd_vocab(args) -> int:
    import glob
    import corrections
    from meetalign.vocab import mine
    if args.action == "list":
        for path in corrections.vocabulary_paths():
            try:
                terms = json.loads(Path(path).read_text(encoding="utf-8")).get("terms", [])
            except (OSError, ValueError):
                continue
            print(f"{len(terms)} term(s) in {path}")
            print("  " + ", ".join(terms))
        return 0
    if args.action == "add":
        added = corrections.add_terms(args.terms, private=not args.shared)
        print(f"added {len(added)}: {', '.join(added)}" if added else "nothing new")
        return 0 if added else 1
    if args.action == "remove":
        gone = corrections.remove_terms(args.terms)
        print(f"removed {len(gone)}: {', '.join(gone)}" if gone else "not found")
        return 0 if gone else 1
    files = args.terms or glob.glob(str(HERE / "transcripts" / "*.txt"))
    lines = [ln for f in files for ln in Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()]
    known = corrections.load_vocabulary().terms
    found = mine(lines, known, min_count=args.min)
    if args.json:
        print(json.dumps(found[:args.top], ensure_ascii=False))
    else:
        print(f"{len(found)} candidate terms not in the vocabulary (from {len(files)} files); top {args.top}:")
        for d in found[:args.top]:
            mixed = "  ← mixed casing" if len(d["forms"]) > 1 else ""
            print(f"  {d['term']:<12} {d['count']:>5}  {d['forms']}{mixed}")
    if args.add:
        added = corrections.add_terms([d["term"] for d in found[:args.add]], private=not args.shared)
        print(f"added {len(added)} to the {'shared' if args.shared else 'private'} vocabulary")
    return 0


def cmd_learned(args) -> int:
    import corrections
    path = corrections.learned_path()
    try:
        rules = json.loads(Path(path).read_text(encoding="utf-8")).get("literal", []) if path else []
    except (OSError, ValueError):
        rules = []
    if args.json:
        print(json.dumps({"file": path, "rules": rules}, ensure_ascii=False))
    else:
        print(f"{len(rules)} learned word(s) in {path}")
        for r in rules:
            print(f"  {r['wrong']} → {r['right']}" + ("  (case-sensitive)" if r.get("case") else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rtt-align", description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, meeting=True, transcript=True):
        if transcript:
            p.add_argument("transcript")
        if meeting:
            p.add_argument("meeting")
        p.add_argument("--write", action="store_true")
        p.add_argument("-o", metavar="FILE")
        p.add_argument("--json", action="store_true")
        p.add_argument("--ai", default="copilot")
        p.add_argument("--format", default="rtt")
        p.add_argument("--source", default="auto")
        p.add_argument("--window", type=float, default=75.0)

    p = sub.add_parser("parse")
    p.add_argument("meeting")
    p.add_argument("--json", action="store_true")
    p.add_argument("--source", default="auto")
    common(sub.add_parser("names"))
    pr = sub.add_parser("refine")
    common(pr)
    pr.add_argument("--learn", action="store_true")
    common(sub.add_parser("tidy"), meeting=False)
    pl = sub.add_parser("learned")
    pl.add_argument("--json", action="store_true")
    pv = sub.add_parser("vocab")
    pv.add_argument("action", choices=["list", "add", "remove", "mine"])
    pv.add_argument("terms", nargs="*")
    pv.add_argument("--shared", action="store_true")
    pv.add_argument("--add", type=int, default=0, metavar="N")
    pv.add_argument("--min", type=int, default=3)
    pv.add_argument("--top", type=int, default=40)
    pv.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "parse":
            return cmd_parse(args)
        if args.cmd in ("names", "refine"):
            return cmd_align(args, refine=args.cmd == "refine")
        if args.cmd == "tidy":
            return cmd_tidy(args)
        if args.cmd == "vocab":
            return cmd_vocab(args)
        return cmd_learned(args)
    except (OSError, ValueError) as exc:
        print(f"rtt-align: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
