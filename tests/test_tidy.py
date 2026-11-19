import threading

import session as sess_mod
from session import Session, burst_marker_indices, tidy_file
from transcribe import TranscriptBuffer

LINES = [
    "[14:40:00] [⏸ RTT paused]",                       # a real, lone pause: kept
    "[14:40:30] [▶ RTT resumed — paused 14:40:00–14:40:30]",
    "[14:47:38] Speaker 1: real speech in the middle",
    "[14:47:38] [⏸ RTT paused]",
    "[14:47:38] [★ RTT bookmark]",
    "[14:47:39] [▶ RTT resumed — paused 14:47:38–14:47:39]",
    "[14:47:39] [🔇 mic muted — transcribing others only]",
    "[14:47:40] [🎙 mic unmuted]",
    "[14:50:00] [★ RTT bookmark: decision]",          # a real bookmark: kept
]


def test_burst_detection_keeps_real_markers_and_speech():
    assert burst_marker_indices(LINES) == [3, 4, 5, 6, 7]


def test_tidy_live_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / "s.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    for ln in LINES:
        buf.add(ln)
    s.marks = [{"at": "14:47:38", "note": ""}, {"at": "14:50:00", "note": "decision"}]
    r = s.tidy("test")
    assert r["removed"] == 5 and len(buf.all_lines()) == 4
    assert "real speech" in tp.read_text() and "mic muted" not in tp.read_text()
    assert [m["note"] for m in s.marks] == ["decision"]
    assert s.undo_preview() == "tidy"
    s.undo()
    assert buf.all_lines() == LINES and len(s.marks) == 2


def test_tidy_file_after_session(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("\n".join(LINES) + "\n")
    assert tidy_file(f) == 5
    assert f.read_text().count("RTT paused") == 1
