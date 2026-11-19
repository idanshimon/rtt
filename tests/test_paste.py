import threading
import time

import session as sess_mod
from session import Session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView

TEAMS = """Pamela Taylor
2:45 PM
Yep, and then Amy is scheduled to meet with Databricks. I'm not sure when.
Anum Rehman
2:45 PM
Yeah, I think I'm on that meeting. It is sometime this week. Sorry, next week.
Maoshan Liao
2:46 PM
Maoshan, yep.
"""


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    buf.add("[14:45:10] Speaker 1: and then Amy is scheduled to meet with Databricks I'm not sure when")
    ui = SplitView(s, buf, None)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    return s, buf, ui, k, msgs


def _settle(k):
    k._paste_at -= 1
    k.tick()


def _effects(s):
    return [e["action"] for e in s.ledger.entries if e["action"] not in ("session_start", "paste")]


def test_bracketed_paste_runs_no_keys_and_offers_names(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"\x1b[200~" + TEAMS.encode() + b"\x1b[201~")
    _settle(k)
    assert _effects(s) == []                                   # no pause / mark / mute / copy
    assert not s.paused_event.is_set() and not s.mic_muted and s.marks == []
    assert "Pasted a Teams/Zoom transcript (3 turns, 3 people)" in ui.prompt
    assert s.ledger.entries[-1]["action"] == "paste"


def test_paste_split_over_reads_and_without_markers(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    raw = TEAMS.encode()
    k.handle(b"\x1b[200~" + raw[:40])                         # bracketed, split in two reads
    k.tick()
    assert ui.prompt is None                                   # not finished yet
    k.handle(raw[40:] + b"\x1b[201~")
    _settle(k)
    assert "3 turns" in ui.prompt and _effects(s) == []
    k.handle(b"\x1b")
    k._esc_at -= 1
    k.tick()                                                   # Esc = ignore
    (tmp_path / "b").mkdir()
    s2, _, ui2, k2, _ = _setup(tmp_path / "b", monkeypatch)
    k2.handle(raw[:100])                                       # terminal without bracketed paste
    k2.handle(raw[100:])
    _settle(k2)
    assert _effects(s2) == [] and "3 turns" in ui2.prompt


def test_paste_into_open_prompt_and_plain_text_ignored(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"M")                                             # bookmark-with-note prompt
    k.handle(b"\x1b[200~decision: ship it\nnext week\x1b[201~")
    _settle(k)
    assert ui.prompt.endswith("decision: ship it next week")
    k.handle(b"\r")
    assert s.marks[-1]["note"] == "decision: ship it next week"
    k.handle(b"\x1b[200~space mumble quick brown fox umm\x1b[201~")
    _settle(k)
    assert ui.prompt is None and "Paste ignored" in msgs[-1]
    assert not s.paused_event.is_set() and not s.mic_muted and len(s.marks) == 1


def test_enter_on_paste_prompt_runs_names(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    import names_match
    monkeypatch.setattr(__import__("meetalign.ai", fromlist=["x"]), "copilot_call", lambda p: "{}")
    k.handle(b"\x1b[200~" + TEAMS.encode() + b"\x1b[201~")
    _settle(k)
    k.handle(b"\r")                                           # Enter = names
    for _ in range(60):
        k.tick()
        if ui.prompt and ui.prompt.startswith("Apply"):
            break
        time.sleep(0.05)
    assert ui.prompt.startswith("Apply")
    k.handle(b"\r")
    assert buf.all_lines()[0].startswith("[14:45:10] Speaker Pamela Taylor:")


def test_typing_is_still_typing(tmp_path, monkeypatch):
    s, buf, ui, k, msgs = _setup(tmp_path, monkeypatch)
    k.handle(b"m")
    k.handle(b" ")
    assert s.marks and s.paused_event.is_set()
