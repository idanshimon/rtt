import json

from summarizer import LiveSummarizer
from tests.test_session import make_session
from transcribe import TranscriptBuffer
from session import send_command


def setup(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    buf = TranscriptBuffer(tmp_path / "t.txt")
    s.buffer_ref = buf
    buf.add("[10:00:00] Speaker 0: I built it all")
    summ = LiveSummarizer(buf.all_lines, s.dir / "summary.md")
    summ._call = lambda cur, new: "## Summary\n- Speaker 0 said it was all built.\n## Decisions\n## Action items\n- Speaker 0 to test\n## Open questions\n"
    s.summarizer = summ
    summ.update_once()
    return s, buf, summ


def ledger(s):
    return [json.loads(l) for l in (s.dir / "actions.jsonl").read_text().splitlines()]


def test_rename_updates_existing_summary_and_survives_inflight_update(tmp_path, monkeypatch):
    s, buf, summ = setup(tmp_path, monkeypatch)
    s.rename_speaker("0", "Idan", "mouse")
    assert summ.sections["Summary"] == ["Idan said it was all built."]
    assert summ.sections["Action items"] == ["Idan to test"]
    assert "Speaker 0" not in (s.dir / "summary.md").read_text()
    buf.add("[10:01:00] Speaker 1: next")
    summ.update_once()           # model answer still says "Speaker 0" (was in flight)
    assert summ.sections["Summary"] == ["Idan said it was all built."]
    s.rename_speaker("Idan", "Idan Shimon", "key")
    assert buf.all_lines()[0] == "[10:00:00] Speaker Idan Shimon: I built it all"
    assert summ.sections["Summary"] == ["Idan Shimon said it was all built."]


def test_ledger_records_and_undo(tmp_path, monkeypatch):
    s, buf, summ = setup(tmp_path, monkeypatch)
    calls = []
    summ.rebuild = lambda: calls.append("rebuild")
    s.rename_speaker("0", "Idan", "mouse")
    s.mark("decision", "key")
    s.toggle_mic("key")
    s.pause("ctl")
    acts = [(e["action"], e["source"]) for e in ledger(s)]
    assert acts == [("session_start", "system"), ("rename_speaker", "mouse"), ("mark", "key"),
                    ("mute", "key"), ("pause", "ctl")]
    assert s.undo()["undone"]["action"] == "pause" and not s.paused_event.is_set()
    assert s.undo()["undone"]["action"] == "mute" and not s.mic_muted
    assert s.undo()["undone"]["action"] == "mark"
    assert s.marks == [] and not any("bookmark" in l for l in buf.all_lines())
    assert s.undo()["undone"]["action"] == "rename_speaker"
    assert buf.all_lines()[0] == "[10:00:00] Speaker 0: I built it all" and calls == ["rebuild"]
    assert s.undo()["ok"] is False                       # nothing left
    undos = [e for e in ledger(s) if e["action"] == "undo"]
    assert [u["params"]["action"] for u in undos] == ["pause", "mute", "mark", "rename_speaker"]
    # history is append-only: original entries are still there
    assert sum(1 for e in ledger(s) if e["action"] == "rename_speaker") == 1


def test_undo_specific_entry_and_offrecord_not_undoable(tmp_path, monkeypatch):
    s, buf, summ = setup(tmp_path, monkeypatch)
    summ.rebuild = lambda: None
    s.rename_speaker("0", "Idan")
    first = ledger(s)[-1]["id"]
    s.mark("x")
    assert s.undo(first)["ok"] and buf.all_lines()[0].startswith("[10:00:00] Speaker 0:")
    assert s.undo(first)["ok"] is False                  # already undone
    s.off_the_record(1)
    off = ledger(s)[-1]
    assert off["action"] == "off_the_record" and "undo" not in off


def test_ctl_rename_log_undo(tmp_path, monkeypatch):
    s, buf, summ = setup(tmp_path, monkeypatch)
    summ.rebuild = lambda: None
    s.start_control_server()
    try:
        assert send_command({"cmd": "rename", "from": "0", "to": "Idan"})["lines"] == 1
        log = send_command({"cmd": "log"})
        assert log["entries"][-1]["action"] == "rename_speaker" and log["entries"][-1]["source"] == "ctl"
        assert send_command({"cmd": "undo"})["ok"]
        assert 2 in send_command({"cmd": "log"})["reverted"]
    finally:
        s.close()


def test_raw_letter_label_does_not_touch_words_and_stale_pause_skipped(tmp_path, monkeypatch):
    s, buf, summ = setup(tmp_path, monkeypatch)
    summ.sections["Summary"] = ["Speaker A raised a Series A round."]
    s.rename_speaker("A", "Amit")
    assert summ.sections["Summary"] == ["Amit raised a Series A round."]
    s.rename_speaker("Amit", "Amit Cohen")
    assert summ.sections["Summary"] == ["Amit Cohen raised a Series A round."]
    s.pause("key")
    s.resume("key")
    s.mark("m")
    s.undo()                       # undoes the mark
    r = s.undo()                   # pause was already resumed -> skip it, undo the rename
    assert r["undone"]["action"] == "rename_speaker"


def test_pick_current_meeting():
    from datetime import datetime
    from session import pick_current_meeting
    lines = ["11:00-12:00 | Our AI Habits Roundtable [2](https://x)", "13:00-14:00 | Friday Huddle",
             "13:00-13:30 | GitHub Copilot Office Hours", "00:00-23:59 | All day thing", "noise"]
    at = lambda h, m: datetime(2026, 11, 6, h, m)
    assert pick_current_meeting(lines, at(11, 50)) == "Our AI Habits Roundtable"
    assert pick_current_meeting(lines, at(12, 52)) == "Friday Huddle"          # upcoming within 10 min, earliest title
    assert pick_current_meeting(lines, at(13, 10)) in ("Friday Huddle", "GitHub Copilot Office Hours")
    assert pick_current_meeting(lines, at(15, 0)) is None
