import json
import threading
import time

import corrections
import session as sess_mod
from names_match import learnable, parse_meeting_transcript, refine_text
from session import Session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView

TEAMS = """Dana Levi
11:41 AM
All right, we got a couple of hands up, Irvin, and then we'll come over to you, Adamya. Go ahead, Irvin.
Irvin Johnson
11:41 AM
Is that still dependent on the Zenith pathways? I was talking to **** ***** about it on Friday.
"""
RTT = [
    "[11:41:23] Speaker 4: All right, we got a couple hands up",
    "[11:41:25] Speaker 4: Urban and then we'll come over to you Damien.",
    "[11:41:27] Speaker 4: Go ahead Urban.",
    "[11:41:30] Speaker 11: Is that still dependent on the Zena pathways?",
    "[11:41:34] Speaker 11: I was talking to Dick about it on Friday.",
    "[11:41:40] Speaker 11: Yeah.",
]


def test_refine_takes_app_wording_keeps_edges_and_redactions():
    f = {x.index: x for x in refine_text(RTT, parse_meeting_transcript(TEAMS))}
    assert f[0].new.endswith("All right, we got a couple of hands up,")
    assert f[1].new.endswith("Irvin, and then we'll come over to you, Adamya.")   # misheard names at the edges
    assert f[2].new.endswith("Go ahead, Irvin.")
    assert "Zenith pathways" in f[3].new
    assert 4 not in f                       # app hid a name there: RTT keeps "Dick"
    assert 5 not in f                       # too short to align
    assert all(x.new.startswith(x.old[:x.old.index(": ") + 2]) for x in f.values())   # time + label kept
    words = {(h, r) for h, r, _ in learnable(list(f.values()))}
    assert ("Urban", "Irvin") in words and ("Damien", "Adamya") in words and ("Zena", "Zenith") in words


def test_learned_words_are_private_case_safe_and_live(tmp_path, monkeypatch):
    path = tmp_path / "private" / "corrections.learned.json"
    monkeypatch.setenv("RTT_LEARNED_CORRECTIONS", str(path))
    added = corrections.learn([("Urban", "Irvin"), ("zena", "Zenith"), ("Urban", "Irvin")], "test")
    assert [a["wrong"] for a in added] == ["Urban", "zena"]          # no duplicate
    c = corrections.Corrections.load()
    assert c.apply("Go ahead Urban.") == "Go ahead Irvin."
    assert c.apply("urban planning matters") == "urban planning matters"   # capitalized rule: case-safe
    assert c.apply("the Zena pathway") == "the Zenith pathway"
    assert json.loads(path.read_text())["literal"][0]["case"] is True


def test_T_key_refines_names_learns_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("RTT_LEARNED_CORRECTIONS", str(tmp_path / "learned.json"))
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    for ln in RTT:
        buf.add(ln)

    class Backend:
        _corrections = corrections.Corrections()
    s.backend_ref = Backend()
    import subprocess
    import names_match
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type("R", (), {"stdout": TEAMS})())
    monkeypatch.setattr(__import__("meetalign.ai", fromlist=["x"]), "copilot_call", lambda prompt: "{}")
    ui = SplitView(s, buf, None)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    k.handle(b"T")
    for _ in range(60):
        k.tick()
        if ui.prompt:
            break
        time.sleep(0.05)
    assert "L + Enter" in ui.prompt and "reworded" in msgs[-1] and "Urban→Irvin" in msgs[-1]
    assert "Zena" not in msgs[-1]                     # seen once, not a participant name: not offered
    k.handle(b"L\r")
    out = buf.all_lines()
    assert out[1] == "[11:41:25] Speaker Dana Levi: Irvin, and then we'll come over to you, Adamya."
    assert out[4] == "[11:41:34] Speaker Irvin Johnson: I was talking to Dick about it on Friday."
    assert s.backend_ref._corrections.apply("Urban said hi") == "Irvin said hi"   # live from now on
    e = [x for x in s.ledger.entries if x["action"] == "meeting_refine"][-1]
    assert "Urban → Irvin" in e["params"]["learned"]
    assert s.undo_preview() == "text + names from Teams/Zoom"
    s.undo()
    assert buf.all_lines() == RTT                     # text and names back; learned words stay
