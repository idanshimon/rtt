import threading
import time

import session as sess_mod
from session import Session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    for i in range(30):
        buf.add(f"[10:00:{i:02d}] Speaker {i % 2}: line {i}")
    ui = SplitView(s, buf, None)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    return s, buf, ui, k, msgs


def _render(ui):
    ui._render()          # sets footer_row / undo_button like the live screen


def test_undo_button_shows_and_undoes_by_click(tmp_path, monkeypatch):
    s, buf, ui, k, _ = _setup(tmp_path, monkeypatch)
    _render(ui)
    assert ui.undo_button is None                        # nothing to undo yet
    s.rename_speaker("0", "Amit", "key")
    _render(ui)
    assert ui.undo_button is not None
    assert "rename 0 → Amit" in ui._footer().plain
    k.handle(f"\x1b[<0;3;{ui.footer_row}M".encode())   # click the button
    assert buf.speaker_map() == {}
    assert s.ledger.entries[-1]["action"] == "undo" and s.ledger.entries[-1]["source"] == "mouse"
    _render(ui)
    assert ui.undo_button is None


def test_click_outside_button_does_not_undo(tmp_path, monkeypatch):
    s, buf, ui, k, _ = _setup(tmp_path, monkeypatch)
    s.rename_speaker("0", "Amit", "key")
    _render(ui)
    k.handle(f"\x1b[<0;{ui.undo_button[1] + 5};{ui.footer_row}M".encode())
    assert buf.speaker_map() == {"0": "Amit"}


def test_ui_actions_are_logged(tmp_path, monkeypatch):
    s, buf, ui, k, _ = _setup(tmp_path, monkeypatch)
    ui._transcript_panel(height=12, width=80)
    k.handle(b"?")
    k.handle(b"?")
    k.handle(b"\t")
    k.handle(b"\t")
    k.handle(b"\x1b[5~")                       # PgUp: leaves live
    ui._transcript_panel(height=12, width=80)
    k.handle(b"\x1b[F")                        # End: back to live
    k.handle(b"r")
    k.handle(b"oops\r")                         # invalid rename
    k.handle(b"M")
    k.handle(b"half")
    k.handle(b"\x1b")
    k._esc_at -= 1
    k.tick()                                    # Esc → prompt cancelled
    k.handle(b"q")
    acts = [(e["action"], e["params"]) for e in s.ledger.entries]
    names = [a for a, _ in acts]
    assert names.count("help") == 2 and names.count("focus") == 2
    assert ("scroll", {"transcript": "scrolled back"}) in acts and ("scroll", {"transcript": "live"}) in acts
    assert ("invalid_input", {"for": "rename", "typed": "oops"}) in acts
    assert any(a == "prompt_cancelled" and p["typed"] == "half" for a, p in acts)
    assert names[-1] == "stop_armed"
    # none of these are undoable, so the button still offers nothing
    assert s.undo_preview() is None
