"""meetalign as a library: extension points, purity, CLI."""
import json
import subprocess
import sys
from pathlib import Path

import meetalign
from meetalign import (RegexLineFormat, Turn, align, find_bursts, parse_meeting, register_line_format,
                       register_resolver, register_source)

ROOT = Path(__file__).resolve().parents[1]
TEAMS = """Dana Levi
11:39 AM
So let's put the comparison on paper and bring it to the leadership review next week.
Omer Katz
11:40 AM
That works for me, I will draft the slide with the Azure and AWS columns tonight.
"""
RTT = ["[11:39:05] Speaker 1: So let's put the comparison on paper",
       "[11:39:09] Speaker 1: and bring it to the leadership review next week.",
       "[11:40:02] Speaker 3: That works for me, I will draft the slide",
       "[11:40:20] Speaker 3: Okay."]


def test_library_has_no_rtt_imports():
    for f in (ROOT / "meetalign").glob("*.py"):
        src = f.read_text()
        for mod in ("session", "tui", "transcribe", "summarizer", "corrections", "names_match"):
            assert f"import {mod}" not in src and f"from {mod} " not in src, (f.name, mod)


def test_align_is_pure_and_complete():
    before = list(RTT)
    r = align(RTT, TEAMS)
    assert RTT == before                                         # input untouched
    assert r.changes[0] == "[11:39:05] Speaker Dana Levi: So let's put the comparison on paper"
    assert 3 not in r.changes            # "Okay." alone: label 3 has 1 confident line (needs 2)
    assert r.label_names == {"1": "Dana Levi"}
    assert set(r.to_dict()) >= {"summary", "changes", "learn", "label_names"}


def test_other_recorder_layout():
    register_line_format("otter", RegexLineFormat(
        r"^(?P<label>[^()]+) \((?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})\): (?P<text>.*)$",
        "{label} ({time}): {text}"))
    lines = ["Speaker A (11:39:05): So let's put the comparison on paper"]
    r = align(lines, TEAMS, line_format="otter")
    assert r.changes[0] == "Dana Levi (11:39:05): So let's put the comparison on paper"


def test_custom_source_and_resolver():
    register_source("csv", lambda text: [Turn(n, t, int(h) * 3600 + int(m) * 60)
                                         for n, h, m, t in (row.split(",", 3) for row in text.strip().splitlines())])
    turns = parse_meeting("Dana Levi,11,39,So let's put the comparison on paper", "csv")
    assert turns[0].name == "Dana Levi" and turns[0].words
    calls = []
    register_resolver("fake", lambda prompt: calls.append(prompt) or json.dumps({"3": "Omer Katz"}))
    r = align(RTT, TEAMS, resolver="fake")
    assert not calls or r.ai_filled >= 0                        # called only if something undecided
    r2 = align(["[11:40:21] Speaker 9: Okay."], TEAMS, resolver=lambda p: '{"0": "Omer Katz"}')
    assert r2.changes[0] == "[11:40:21] Speaker Omer Katz: Okay." and r2.ai_filled == 1
    r3 = align(["[11:40:21] Speaker 9: Okay."], TEAMS, resolver=lambda p: (_ for _ in ()).throw(OSError("down")))
    assert r3.changes == {} and "down" in r3.ai_error           # AI failure keeps the words result


def test_bursts_are_generic():
    import re
    lines = ["10:00:01 PAUSE", "10:00:01 PAUSE", "10:00:02 MUTE", "10:00:02 PAUSE", "10:10:00 PAUSE"]
    assert find_bursts(lines, re.compile(r"^(\d\d):(\d\d):(\d\d) (PAUSE|MUTE)")) == [0, 1, 2, 3]


def _cli(*args, stdin=None):
    p = subprocess.run([sys.executable, str(ROOT / "rtt_align.py"), *args], capture_output=True, text=True,
                       input=stdin, cwd=ROOT, env={"PATH": "/usr/bin:/bin", "RTT_LEARNED_CORRECTIONS": "none"})
    return p.returncode, p.stdout, p.stderr


def test_cli_names_refine_write_tidy(tmp_path):
    t = tmp_path / "t.txt"
    t.write_text("\n".join(RTT) + "\n")
    code, out, _ = _cli("names", str(t), "-", "--ai", "none", stdin=TEAMS)
    assert code == 0 and "+ [11:39:05] Speaker Dana Levi:" in out and t.read_text().count("Dana") == 0
    code, out, _ = _cli("refine", str(t), "-", "--ai", "none", "--write", stdin=TEAMS)
    assert code == 0 and "wrote" in out and "Speaker Dana Levi" in t.read_text()
    assert (tmp_path / "t.txt.bak").read_text().count("Speaker 1") == 2
    code, out, _ = _cli("names", str(t), "-", "--ai", "none", "--json", stdin=TEAMS)
    assert json.loads(out)["lines_changed"] == 0 and code == 1     # nothing left to change
    code, out, _ = _cli("parse", "-", stdin=TEAMS)
    assert code == 0 and out.startswith("2 turns, 2 people")
    b = tmp_path / "b.txt"
    b.write_text("\n".join(["[10:00:01] [⏸ RTT paused]"] * 5 + ["[10:00:02] Speaker 1: hello"]) + "\n")
    code, out, _ = _cli("tidy", str(b), "--write")
    assert code == 0 and b.read_text().strip() == "[10:00:02] Speaker 1: hello"
    code, _, err = _cli("names", str(t), "-", stdin="nothing here")
    assert code == 2 and "No named turns" in err
