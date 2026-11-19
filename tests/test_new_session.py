import json
import time

import numpy as np

from languages import lang_profile
from session import send_command
from summarizer import LiveSummarizer
from tests.test_session import FakeBuffer, make_session
from transcribe import TranscriptBuffer
from tui import KeyActions


def test_lang_profiles():
    assert lang_profile("vibe", {})["label"] == "he+en"
    assert lang_profile("openai", {})["label"] == "he+en"
    assert lang_profile("openai", {"language": "en"})["label"] == "en only"
    assert lang_profile("llmspeech", {"llmspeech_locales": "en-US,he-IL"})["label"] == "he+en"
    assert lang_profile("llmspeech", {"llmspeech_locales": "en-US"})["label"] == "en only"
    assert lang_profile("azure", {"azure_languages": "en-US,he-IL"})["mix"] is True
    assert lang_profile("azure", {})["label"] == "en only"
    assert lang_profile("mai", {})["label"] == "en (he ?)"
    assert lang_profile("local", {})["he"] is None


def setup(tmp_path, monkeypatch, record=False):
    s, lines = make_session(tmp_path, monkeypatch, record=record)
    buf = TranscriptBuffer(tmp_path / "transcripts" / "t1.txt")
    s.buffer_ref = buf
    s.new_transcript_path = lambda: tmp_path / "transcripts" / "t2.txt"
    s.lang_cfg = {"llmspeech_locales": "en-US,he-IL"}
    calls = []
    summ = LiveSummarizer(buf.all_lines, s.dir / "summary.md")
    monkeypatch.setattr(LiveSummarizer, "_call", lambda self, cur, new: (calls.append(len(new)) or
                        "## Summary\n- old meeting point\n## Decisions\n## Action items\n## Open questions\n"))
    s.summarizer = summ
    return s, buf, summ, calls


def test_new_session_rolls_everything_over(tmp_path, monkeypatch):
    s, buf, summ, calls = setup(tmp_path, monkeypatch, record=True)
    emitted = s._emit_lines = []
    s._emit = emitted.append
    buf.add("[10:00:00] Speaker 1: old meeting")
    buf.rename_speaker("1", "Dana")
    s.mark("old")
    s.recorder.write("others", np.full((16000, 1), 0.1, dtype=np.float32))
    s.pause()
    old_dir = s.dir
    hooked = []
    s.on_new_session = lambda: hooked.append(1)
    r = s.new_session("standup", "key")
    time.sleep(0.5)
    # old session closed and complete
    meta = json.loads((old_dir / "session.json").read_text())
    assert meta["ended_at"] and (old_dir / "others.flac").exists()
    assert "old meeting point" in (old_dir / "summary.md").read_text()      # finished from snapshot
    old_led = [json.loads(l)["action"] for l in (old_dir / "actions.jsonl").read_text().splitlines()]
    assert old_led[-1] == "new_session" and "resume" in old_led
    # new session fresh
    assert s.dir != old_dir and s.dir.name.endswith("standup") and r["previous"] == str(old_dir)
    assert s.marks == [] and not s.paused_event.is_set() and hooked == [1]
    assert emitted[-1].startswith("[✦ RTT new session") and buf.all_lines() == []
    assert buf.speaker_map() == {} and (tmp_path / "transcripts" / "t2.txt").exists()
    assert "old meeting" not in (tmp_path / "transcripts" / "t2.txt").read_text()
    assert summ.sections.get("Summary") in ([], None, ["none yet"]) or "old meeting point" not in summ.summary_md
    new_led = [json.loads(l) for l in (s.dir / "actions.jsonl").read_text().splitlines()]
    assert new_led[0]["action"] == "session_start" and new_led[0]["params"]["previous"] == old_dir.name
    assert s.recorder is not None and s.recorder is not None
    s.close()


def test_key_and_ctl(tmp_path, monkeypatch):
    s, buf, summ, _ = setup(tmp_path, monkeypatch)
    k = KeyActions(s, buf, summ, lambda t: True, lambda m: None)
    first = s.dir
    k.handle(b"N")
    k.handle("ישיבה\r".encode())
    time.sleep(0.5)
    assert s.dir != first and s.dir.name.endswith("ישיבה")
    s.start_control_server()
    try:
        r = send_command({"cmd": "new", "name": ""})
        assert r["ok"] and r["session"]["previous"].endswith("ישיבה")
        st = send_command({"cmd": "status"})["status"]
        assert st["lang"]["label"] == "he+en"
    finally:
        s.close()
