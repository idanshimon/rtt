import json
import os
from datetime import datetime
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import session as sess_mod
from session import Session, find_session
from summarizer import LiveSummarizer
from transcribe import TranscriptBuffer, _normalize_resume_argv, resume_hint


def _mk(tmp_path, monkeypatch, resume_dir=None, record=False, tp=None):
    monkeypatch.setattr(sess_mod, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", tmp_path / f"s-{time.time_ns()}.sock")
    tp = tp or tmp_path / "transcript.txt"
    if not tp.exists():
        tp.write_text("")
    return Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                   stop_event=threading.Event(), emit=lambda t: None, name=None,
                   sessions_root=tmp_path / "sessions", record=record, sample_rate=16000,
                   resume_dir=resume_dir)


def test_argv_normalizer():
    assert _normalize_resume_argv(["--resume"]) == ["--resume", ""]
    assert _normalize_resume_argv(["-c", "--no-tui"]) == ["-c", "", "--no-tui"]
    assert _normalize_resume_argv(["-c", "weekly"]) == ["-c", "weekly"]
    assert _normalize_resume_argv(["--resume", "20261109-090803"]) == ["--resume", "20261109-090803"]


def test_find_session(tmp_path):
    root = tmp_path / "sessions"
    for sid, start, title in [("20261108-100000", "2026-11-08T10:00:00", "Team weekly"),
                              ("20261109-090803", "2026-11-09T09:08:03", None),
                              ("20261109-120000-NWND-sync", "2026-11-09T12:00:00", "NWND sync")]:
        (root / sid).mkdir(parents=True)
        (root / sid / "session.json").write_text(json.dumps({"id": sid, "started_at": start, "meeting_title": title}))
    assert find_session(root).name == "20261109-120000-NWND-sync"
    assert find_session(root, "20261109-090803").name == "20261109-090803"
    assert find_session(root, "20261108").name == "20261108-100000"
    assert find_session(root, "weekly").name == "20261108-100000"
    with pytest.raises(FileNotFoundError):
        find_session(root, "nothing")


def test_resume_keeps_lines_names_voices_marks_ledger_and_audio(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch, record=True)
    buf = TranscriptBuffer(s.transcript_path)
    s.buffer_ref = buf
    buf.add("[09:10:00] Speaker 1: hello", t=time.time() - 60)
    buf.add("[09:10:05] Speaker 2: hi", t=time.time() - 55)
    s.rename_speaker("1", "Amit")
    s.mark("decision")
    s.speaker_tracker = SimpleNamespace(centroids=[np.ones(4, np.float32), np.zeros(4, np.float32)], counts=[3, 1])
    s.recorder.write("others", np.full(16000, 0.1, np.float32))
    time.sleep(0.4)
    s.set_meeting_title("Team weekly")
    s.close()
    d = s.dir
    assert d.name.endswith("Team-weekly")
    assert "Speaker Amit: hello" in s.transcript_path.read_text()
    n_ledger = len((d / "actions.jsonl").read_text().splitlines())

    meta = json.loads((d / "session.json").read_text())
    assert meta["speaker_map"] == {"1": "Amit"} and meta["ended_at"]
    buf2 = TranscriptBuffer(s.transcript_path)
    assert buf2.load_existing(meta["speaker_map"]) == 2
    assert buf2.all_lines()[0] == "[09:10:00] Speaker Amit: hello"
    assert buf2._lines[0] == "[09:10:00] Speaker 1: hello"          # raw label restored
    s2 = _mk(tmp_path, monkeypatch, resume_dir=find_session(tmp_path / "sessions", "weekly"), record=True,
             tp=s.transcript_path)
    s2.buffer_ref = buf2
    s2.speaker_tracker = SimpleNamespace(centroids=[], counts=[])
    assert s2.load_voices() == 2 and s2.speaker_tracker.counts == [3, 1]
    assert s2.dir == d and s2.meeting_title == "Team weekly"
    assert [m["note"] for m in s2.marks] == ["decision"]
    assert s2.ledger.entries[-1]["action"] == "session_resume"
    assert s2.ledger.entries[-1]["id"] == n_ledger + 1
    buf2.add("[09:30:00] Speaker 1: back again")                    # same voice → still Amit
    assert buf2.all_lines()[-1] == "[09:30:00] Speaker Amit: back again"
    buf2.rename_speaker("Amit", "Amit Cohen")
    assert all("Amit Cohen" in l for l in buf2.all_lines() if "Speaker 2" not in l)
    s2.recorder.write("others", np.full(8000, 0.1, np.float32))
    time.sleep(0.4)
    s2.close()
    a, sr = sf.read(str(d / "others.flac"))
    assert abs(len(a) / sr - 1.5) < 0.05                            # 1 s + 0.5 s appended
    assert s2.dir == d                                               # not renamed twice
    meta2 = json.loads((d / "session.json").read_text())
    assert len(meta2["resumes"]) == 1 and len(meta2["marks"]) == 1
    hint = resume_hint(s2, buf2)
    assert f"rtt --resume {d.name}" in hint and 'rtt -c "Team weekly"' in hint


def test_summary_continues(tmp_path):
    out = tmp_path / "summary.md"
    out.write_text("## Decisions\n- ship it\n")
    sm = LiveSummarizer(lambda: ["a", "b", "c"], out)
    assert sm.resume_from(out, 3)
    assert sm.sections["Decisions"] == ["ship it"]
    assert sm.update_once() is False                                 # nothing new since the stop


def test_running_session_is_never_picked(tmp_path, monkeypatch):
    import os, subprocess, sys
    root = tmp_path / "sessions"
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "transcribe.py"])
    try:
        for sid, start, pid in [("20261109-090000", "2026-11-09T09:00:00", 999999),
                                ("20261109-100000", "2026-11-09T10:00:00", live.pid)]:
            (root / sid).mkdir(parents=True)
            (root / sid / "session.json").write_text(json.dumps({"id": sid, "started_at": start, "pid": pid}))
        assert sess_mod.session_is_running({"pid": live.pid})
        assert not sess_mod.session_is_running({"pid": 999999})
        assert find_session(root).name == "20261109-090000"        # live one skipped
        with pytest.raises(FileNotFoundError):
            find_session(root, "20261109-100000")
    finally:
        live.kill()


def test_old_session_without_pid(monkeypatch):
    monkeypatch.setattr(sess_mod, "_rtt_process_starts", lambda: [datetime(2026, 11, 9, 10, 3, 59).timestamp()])
    assert sess_mod.session_is_running({"started_at": "2026-11-09T10:04:00"})            # matching live process
    assert not sess_mod.session_is_running({"started_at": "2026-11-09T09:08:03"})        # no process
    assert not sess_mod.session_is_running({"started_at": "2026-11-09T10:04:00", "ended_at": "x"})


def test_review_fixes_resume(tmp_path, monkeypatch):
    # closed sessions are resumable even if their RTT process still runs (N = new session)
    assert not sess_mod.session_is_running({"pid": os.getpid() + 0, "ended_at": "2026-11-09T10:00:00"})
    # latest = last activity, not first start
    root = tmp_path / "sessions"
    for sid, meta in [("20261109-080000", {"started_at": "2026-11-09T08:00:00", "ended_at": "2026-11-09T08:30:00",
                                            "resumes": [{"at": "2026-11-09T12:00:00"}]}),
                      ("20261109-100000", {"started_at": "2026-11-09T10:00:00", "ended_at": "2026-11-09T10:30:00"})]:
        (root / sid).mkdir(parents=True)
        (root / sid / "session.json").write_text(json.dumps({"id": sid, **meta}))
    assert find_session(root).name == "20261109-080000"
    # merged names survive a resume exactly (raw lines saved, not reversed)
    s = _mk(tmp_path, monkeypatch)
    buf = TranscriptBuffer(s.transcript_path)
    s.buffer_ref = buf
    buf.add("[09:00:00] Speaker 0: a")
    buf.add("[09:00:05] Speaker 3: b")
    s.rename_speaker("0", "Amit")
    s.rename_speaker("3", "Amit")
    s.close()
    meta = json.loads((s.dir / "session.json").read_text())
    rows = json.loads((s.dir / "lines.json").read_text())
    b2 = TranscriptBuffer(s.transcript_path)
    b2.load_raw(rows, meta["speaker_map"])
    assert b2.raw_lines() == ["[09:00:00] Speaker 0: a", "[09:00:05] Speaker 3: b"]
    # a resumed session cannot undo actions from before the resume
    s2 = _mk(tmp_path, monkeypatch, resume_dir=s.dir, tp=s.transcript_path)
    s2.buffer_ref = b2
    r = s2.undo()
    assert not r["ok"]
    rename_id = next(e["id"] for e in s2.ledger.entries if e["action"] == "rename_speaker")
    assert "before the session was resumed" in s2.undo(rename_id)["error"]
    # elapsed excludes the stopped gap
    s2.resumes = [{"at": "2026-11-09T10:00:00", "after_end": "2026-11-09T09:00:00"}]
    s2.started_at = time.time() - 3600 - 120
    assert 110 <= s2.active_seconds() <= 130


def test_title_rename_replaces_old_slug(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch)
    s.set_meeting_title("Old title")
    s.close()
    assert s.dir.name.endswith("-Old-title")
    s2 = _mk(tmp_path, monkeypatch, resume_dir=s.dir, tp=s.transcript_path)
    s2.set_meeting_title("New title")
    s2.close()
    assert s2.dir.name.endswith("-New-title") and "Old" not in s2.dir.name


def test_new_session_forgets_resume_gaps(tmp_path, monkeypatch):
    s = _mk(tmp_path, monkeypatch)
    s.buffer_ref = TranscriptBuffer(s.transcript_path)
    s.new_transcript_path = lambda: tmp_path / "t2.txt"
    s.resumes = [{"at": "2026-11-09T10:00:00", "after_end": "2026-11-09T09:00:00"}]
    s.new_session("next")
    assert s.resumes == [] and s.active_seconds() < 5
