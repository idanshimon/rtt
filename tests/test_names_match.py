import threading
import time

import session as sess_mod
from names_match import ai_resolve, match_lines, parse_meeting_transcript
from session import Session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView

TEAMS = """Dana Levi
11:39 AM
So let's put the comparison on paper and bring it to the leadership review next week.
Omer Katz
11:39 AM
Yeah.
Omer Katz
11:40 AM
That works for me, I will draft the slide with the Azure and AWS columns tonight.
Dana Levi
11:40 AM
Great, and add the pricing row too.
"""
RTT = [
    "[11:30:00] Speaker 1: something said before the paste",
    "[11:39:05] Speaker 1: So let's put the comparison on paper",
    "[11:39:09] Speaker 1: and bring it to the leadership review next week.",
    "[11:39:20] Speaker ?: Yeah.",
    "[11:40:02] Speaker 3: That works for me, I will draft the slide",
    "[11:40:06] Speaker 1: with the Azure and AWS columns tonight.",   # mixed label: really Omer
    "[11:40:15] Speaker 1: Great, and add the pricing row too.",
    "[11:50:00] Speaker 1: long after the paste",
]


def test_parse_formats():
    t = parse_meeting_transcript(TEAMS)
    assert [x.name for x in t] == ["Dana Levi", "Omer Katz", "Omer Katz", "Dana Levi"]
    assert t[0].t == 11 * 3600 + 39 * 60
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Dana Levi>Hello there all</v>\n"
    assert [(x.name, x.text) for x in parse_meeting_transcript(vtt)] == [("Dana Levi", "Hello there all")]
    zoom = "[11:39:02] Dana Levi: hello there\nOmer Katz: hi back\n[Julian D'Orsaneo] 11:40:00\ntext here"
    assert [x.name for x in parse_meeting_transcript(zoom)] == ["Dana Levi", "Omer Katz", "Julian D'Orsaneo"]
    pm = parse_meeting_transcript("Dana Levi\n2:05 PM\nafternoon words")
    assert pm[0].t == 14 * 3600 + 5 * 60


def test_line_level_names_fix_mixed_labels():
    r = match_lines(RTT, parse_meeting_transcript(TEAMS))
    got = {m.index: m.name for m in r["matches"]}
    assert 0 not in got and 7 not in got                 # outside the pasted time range
    assert got[1] == got[2] == got[6] == "Dana Levi"
    assert got[4] == got[5] == "Omer Katz"               # Speaker 1 line that was really Omer
    assert got[3] is None                                 # "Yeah." is left for AI
    assert r["label_names"] == {"1": "Dana Levi"}         # 3 of 4 → Dana; later Speaker 1 lines get it too


def test_ai_fills_only_known_names():
    t = parse_meeting_transcript(TEAMS)
    r = match_lines(RTT, t)
    n = ai_resolve(RTT, t, r["matches"], lambda prompt: '{"3": "Omer Katz", "99": "Nobody"}')
    assert n == 1 and next(m for m in r["matches"] if m.index == 3).how == "ai"
    assert ai_resolve(RTT, t, r["matches"], lambda p: "not json") == 0


def test_t_key_applies_and_z_undoes(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    for ln in RTT:
        buf.add(ln)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"stdout": TEAMS})())
    import names_match
    monkeypatch.setattr(__import__("meetalign.ai", fromlist=["x"]), "copilot_call", lambda prompt: '{"3": "Omer Katz"}')
    ui = SplitView(s, buf, None)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    k.handle(b"t")
    for _ in range(50):
        k.tick()                                   # the prompt opens on the key thread
        if ui.prompt:
            break
        time.sleep(0.05)
    assert ui.prompt.startswith("Apply?") and "6/6 lines named" in msgs[-1]
    k.handle(b"\r")
    lines = buf.all_lines()
    assert lines[5] == "[11:40:06] Speaker Omer Katz: with the Azure and AWS columns tonight."
    assert lines[3] == "[11:39:20] Speaker Omer Katz: Yeah."
    assert "Speaker Dana Levi:" in tp.read_text()
    s.rename_speaker("Dana Levi", "Dana")
    assert buf.all_lines()[1].startswith("[11:39:05] Speaker Dana:")
    s.undo()                                              # undo the rename
    s.undo()                                              # undo the names
    assert buf.all_lines() == RTT


def test_midnight_and_text_that_looks_like_a_speaker():
    teams = "Alice Ray\n11:59 PM\nwe should ship the new pricing model tomorrow\nAction Item: send the deck to Bob\nBob Stone\n12:00 AM\nI will send the deck first thing in the morning\n"
    t = parse_meeting_transcript(teams)
    assert [x.name for x in t] == ["Alice Ray", "Bob Stone"]          # "Action Item" stays text
    assert "Action Item: send the deck" in t[0].text
    lines = ["[23:59:30] Speaker 1: we should ship the new pricing model tomorrow",
             "[00:00:20] Speaker 2: I will send the deck first thing in the morning"]
    got = {m.index: m.name for m in match_lines(lines, t)["matches"]}
    assert got == {0: "Alice Ray", 1: "Bob Stone"}


def _session(tmp_path, monkeypatch, lines):
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    s.buffer_ref = TranscriptBuffer(tp)
    for ln in lines:
        s.buffer_ref.add(ln)
    return s


def test_undo_keeps_later_renames_and_stale_preview_applies_nothing(tmp_path, monkeypatch):
    s = _session(tmp_path, monkeypatch, RTT)
    prev = s.match_meeting_names(TEAMS, use_ai=False)
    s.apply_meeting_names(prev, "test")
    s.rename_speaker("3", "Third")                          # a later, unrelated rename
    entry = next(e for e in s.ledger.entries if e["action"] == "meeting_names")
    s.undo(entry["id"])
    assert s.buffer_ref.speaker_map() == {"3": "Third"}      # names gone, later rename kept
    assert s.buffer_ref.all_lines()[1] == RTT[1]
    s2 = _session(tmp_path / "b", monkeypatch, RTT)
    prev = s2.match_meeting_names(TEAMS, use_ai=False)
    s2.off_the_record(source="test", since=0)               # everything deleted after the preview
    r = s2.apply_meeting_names(prev, "test")
    assert not r["ok"] and s2.buffer_ref.speaker_map() == {}
