import threading
import tempfile
from pathlib import Path

import session as sess_mod
from session import Session
from transcribe import TranscriptBuffer
from tui import CONT_INDENT, SplitView


def _ui(tmp_path, monkeypatch, lines):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    b = TranscriptBuffer(tp)
    s.buffer_ref = b
    for ln in lines:
        b.add(ln)
    return SplitView(s, b, None)


def test_colors_per_speaker_wrap_and_click_rows(tmp_path, monkeypatch):
    ui = _ui(tmp_path, monkeypatch, [
        "[10:00:00] Speaker Dana: short",
        "[10:00:05] Speaker Omer: " + "word " * 30,
        "[10:00:09] Speaker ?: Yeah.",
        "[10:00:10] Speaker Dana: again",
    ])
    panel = ui._transcript_panel(height=20, width=60)
    body = panel.renderable
    assert ui.speaker_color("Dana") != ui.speaker_color("Omer")
    assert ui.speaker_color("Dana") == ui.speaker_colors["Dana"]          # stable
    assert ui.speaker_color("?") == "grey50"
    text = body.plain.splitlines()
    assert text[0] == "[10:00:00] Dana: short"                          # "Speaker" not shown
    assert all(not r.startswith("word") for r in text)                   # no row starts mid-line at col 0
    cont = [r for r in text if r.startswith(" " * (CONT_INDENT - 2) + "▏ ")]
    assert cont and all(r.rstrip().endswith("word") for r in cont)       # wrapped at word boundaries
    styles = {str(sp.style) for sp in body.spans}
    assert any(ui.speaker_colors["Dana"] in s for s in styles)
    assert [lab for _, _, lab in ui.click_map] == ["Dana", "Omer", "?", "Dana"]
    assert ui.click_map[1][1] - ui.click_map[1][0] + 1 == len(cont) + 1   # click rows cover wrapped rows
