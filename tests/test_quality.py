import re
import threading
import time
from pathlib import Path

import numpy as np

import session as sess_mod
from session import Session
from speakers import SpeakerTracker
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView


def _mk(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / f"s-{time.time_ns()}.sock")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    s.buffer_ref = TranscriptBuffer(tp)
    return s


def test_esc_closes_help(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch)
    ui = SplitView(s, s.buffer_ref, None)
    k = KeyActions(s, s.buffer_ref, None, lambda t: True, lambda m: None, ui=ui)
    k.handle(b"?")
    assert ui.show_help
    k.handle(b"\x1b")
    k._esc_at -= 1
    k.tick()                                   # lone Esc after the escape-sequence wait
    assert not ui.show_help
    k.handle(b"\x1b\x1b")                      # Esc with help closed: harmless
    assert not ui.show_help


def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def test_cap_stops_new_voices_and_locks_to_most_heard():
    t = SpeakerTracker.__new__(SpeakerTracker)
    SpeakerTracker.__init__(t, model_path=Path("/nonexistent"))
    a, b = _unit([1, 0, 0, 0]), _unit([0, 1, 0, 0])
    noisy_b = _unit([0.2, 0.3, 0.9, 0.1])      # Tim on the street: below threshold vs everyone
    assert t._assign({"x": a}) == {"x": 0}
    for _ in range(3):
        t._assign({"x": a, "y": b})
    t._assign({"y": noisy_b})                   # uncapped: becomes a 3rd voice
    assert len(t.centroids) == 3
    t.set_max_speakers(2)
    assert t.allowed == [0, 1]                  # the two most-heard voices
    out = t._assign({"y": noisy_b})
    assert out["y"] in (0, 1) and len(t.centroids) == 3   # no new voice; junk voice 3 unmatched
    out = t._assign({"x": a, "y": _unit([0.1, 0.2, 0.95, 0.0])})
    assert out == {"x": 0, "y": 1}              # two voices in one chunk stay two people
    t.set_max_speakers(None)
    assert t.allowed is None


def test_k_sets_expected_people(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch)
    s.speaker_tracker = SpeakerTracker.__new__(SpeakerTracker)
    SpeakerTracker.__init__(s.speaker_tracker, model_path=Path("/nonexistent"))
    ui = SplitView(s, s.buffer_ref, None)
    k = KeyActions(s, s.buffer_ref, None, lambda t: True, lambda m: None, ui=ui)
    k.handle(b"k")
    assert "How many people" in ui.prompt
    k.handle(b"2\r")
    assert s.expected_speakers == 2 and s.speaker_tracker.max_speakers == 2
    assert s.ledger.entries[-1]["action"] == "expected_speakers"
    k.handle(b"k")
    k.handle(b"0\r")
    assert s.expected_speakers is None and s.speaker_tracker.max_speakers is None


def test_quality_flags_the_tim_call_early(tmp_path, monkeypatch):
    """Replay of the real 2026-11-09 call (2 people, 11 labels, 45% fragments): warn within ~2 min."""
    s = _mk(tmp_path, monkeypatch)
    src = Path(__file__).parent / "fixtures" / "call-head.txt"
    first = None
    for ln in src.read_text().splitlines():
        s.buffer_ref.add(ln)
        q = s.quality()
        if q["warnings"] and first is None:
            first = ln[1:9]
            assert any("voices" in w for w in q["warnings"])
    assert first is not None and first <= "10:39:30"
    s.expected_speakers = 2
    assert not any("voices" in w for w in s.quality()["warnings"])


def test_quality_quiet_on_a_clean_call(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch)
    for i in range(60):
        s.buffer_ref.add(f"[10:{i // 60:02d}:{i % 60:02d}] Speaker {1 + i % 2}: this is a normal full sentence number {i}")
    assert s.quality()["warnings"] == []
