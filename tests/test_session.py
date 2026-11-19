import json
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import session as sess_mod
from session import Session, send_command
from summarizer import LiveSummarizer, parse_sections, render_sections
from tui import KeyActions


def make_session(tmp_path, monkeypatch, record=False):
    monkeypatch.setattr(sess_mod, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", Path("/tmp") / f"rtt-test-{time.time_ns()}.sock")
    lines = []
    tp = tmp_path / "transcript.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lines.append, name="unit test",
                sessions_root=tmp_path / "sessions", record=record, sample_rate=16000)
    return s, lines


def test_pause_resume_writes_markers_and_metadata(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    assert s.pause() and s.paused_event.is_set()
    assert not s.pause()  # idempotent
    assert s.resume() and not s.paused_event.is_set()
    assert lines[0] == "[⏸ RTT paused]" and lines[1].startswith("[▶ RTT resumed — paused ")
    meta = json.loads((s.dir / "session.json").read_text())
    assert len(meta["pauses"]) == 1 and s.dir.name.endswith("unit-test")
    assert (s.dir / "transcript.txt").is_symlink()


def test_mark_and_mute(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    s.mark("שלום decision")
    s.toggle_mic()
    assert lines[0] == "[★ RTT bookmark: שלום decision]" and "mic muted" in lines[1]
    st = s.status()
    assert st["marks"] == 1 and st["mic_muted"] and st["state"] == "live"


def test_recorder_writes_tracks_and_respects_pause(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch, record=True)
    blk = np.full((1600, 1), 0.1, dtype=np.float32)
    s.recorder.write("others", blk)
    s.recorder.write("mic", blk)
    s.pause()
    s.recorder.write("others", blk)  # dropped while paused
    s.resume()
    s.close()
    a, sr = sf.read(str(s.dir / "others.flac"))
    assert sr == 16000 and len(a) == 1600
    assert (s.dir / "mic.flac").exists()
    assert "ended_at" in json.loads((s.dir / "session.json").read_text())


def test_control_socket_roundtrip(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    assert s.start_control_server()
    try:
        assert send_command({"cmd": "pause"})["changed"] is True
        assert send_command({"cmd": "status"})["status"]["state"] == "paused"
        assert send_command({"cmd": "mark", "note": "x"})["mark"]["note"] == "x"
        assert send_command({"cmd": "bogus"})["ok"] is False
        assert send_command({"cmd": "stop"})["ok"] and s.stop_event.is_set()
    finally:
        s.close()
    assert not sess_mod.SOCKET_PATH.exists()


def test_parse_and_render_sections_tolerant():
    md = "## Summary\n- A\n## Decisions\n- none yet\n### Action Items\n* Dana: QA\n## Open questions\n"
    sec = parse_sections(md)
    assert sec["Summary"] == ["A"] and sec["Decisions"] == [] and sec["Action items"] == ["Dana: QA"]
    assert "- none yet" in render_sections(sec)


def test_summarizer_incremental_and_error_safe(tmp_path):
    lines = ["[10:00:00] Speaker 0: hi"]
    summ = LiveSummarizer(lambda: list(lines), tmp_path / "summary.md")
    sent = []

    def fake_call(current, new):
        sent.append(list(new))
        return "## Summary\n- greeting\n## Decisions\n## Action items\n- Amit: send deck\n## Open questions\n"

    summ._call = fake_call
    assert summ.update_once()
    assert not summ.update_once()  # nothing new
    lines.append("[10:01:00] Speaker 1: ok")
    assert summ.update_once()
    assert sent == [["[10:00:00] Speaker 0: hi"], ["[10:01:00] Speaker 1: ok"]]
    assert summ.sections["Action items"] == ["Amit: send deck"]

    def boom(current, new):
        raise RuntimeError("HTTP 500")

    summ._call = boom
    lines.append("[10:02:00] x")
    assert not summ.update_once() and "HTTP 500" in summ.status()["error"]
    assert summ.sections["Action items"] == ["Amit: send deck"]  # last good summary kept


class FakeBuffer:
    def __init__(self):
        self.renamed = []
        self.copied = []

    def known_speakers(self):
        return ["0", "1"]

    def rename_speaker(self, a, b):
        self.renamed.append((a, b))
        return 3

    def speaker_map(self):
        return {}

    def copy_full_to_clipboard(self):
        self.copied.append("full")

    def copy_delta_to_clipboard(self):
        self.copied.append("delta")


def test_key_actions(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    buf, msgs, clip = FakeBuffer(), [], []
    s.buffer_ref = buf
    k = KeyActions(s, buf, None, lambda t: clip.append(t) or True, msgs.append)
    k.handle(b" ")
    assert s.paused_event.is_set()
    k.handle(b"\x10")  # legacy Ctrl+P
    assert not s.paused_event.is_set()
    k.handle(b"c")
    k.handle(b"\x05")
    assert buf.copied == ["full", "delta"]
    k.handle(b"M")
    k.handle("החלטה".encode("utf-8"))  # Hebrew note, multi-byte
    k.handle(b"\r")
    assert lines[-1] == "[★ RTT bookmark: החלטה]"
    k.handle(b"r")
    k.handle(b"0=Amit\r")
    assert buf.renamed == [("0", "Amit")]
    k.handle(b"s")
    assert "summary is off" in msgs[-1]
    k.handle(b"q")
    assert not s.stop_event.is_set()
    k.handle(b"q")
    assert s.stop_event.is_set()
