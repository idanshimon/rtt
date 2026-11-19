import time
from datetime import datetime

import pytest

from session import Session
from tests.test_session import make_session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView


def _at(h, m, s, now):
    return datetime.fromtimestamp(now).replace(hour=h, minute=m, second=s, microsecond=0).timestamp()


def test_parse_minutes_and_times():
    now = datetime(2026, 11, 9, 9, 20, 0).timestamp()
    p = Session.parse_off_record
    assert p("", now) == {"minutes": 2.0}
    assert p("3", now) == {"minutes": 3.0}
    assert p("2m", now) == {"minutes": 2.0}
    assert p("90s", now) == {"minutes": 1.5}
    assert p("09:15:09", now) == {"since": _at(9, 15, 9, now)}
    assert p("[09:15:09]", now) == {"since": _at(9, 15, 9, now)}
    assert p("9:15", now) == {"since": _at(9, 15, 0, now)}
    assert p("[09:15:09] Speaker 1: Thank you. 10:00", now) == {"since": _at(9, 15, 9, now)}
    assert p("23:59:00", now)["since"] == _at(23, 59, 0, now) - 86400   # yesterday
    for bad in ("abc", "25:00", "09:61"):
        with pytest.raises(ValueError):
            p(bad, now)


def _session_with_lines(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch)
    buf = TranscriptBuffer(tmp_path / "t.txt")
    s.buffer_ref = buf
    now = time.time()
    for i, txt in enumerate(["keep", "secret one", "secret two"]):
        t = now - 300 + i * 60
        buf.add(f"[{time.strftime('%H:%M:%S', time.localtime(t))}] Speaker 0: {txt}")
        buf._times[-1] = t
    return s, buf


def test_off_record_since_line_time(tmp_path, monkeypatch):
    s, buf = _session_with_lines(tmp_path, monkeypatch)
    stamp = buf.all_lines()[1][1:9]
    r = s.off_the_record(source="test", **s.parse_off_record(stamp))
    assert r["lines_removed"] == 2 and r["since"] == stamp
    assert [l.split(": ", 1)[1] for l in buf.all_lines()] == ["keep"]
    assert "secret" not in (tmp_path / "t.txt").read_text()


def test_click_line_picks_time_then_enter_deletes(tmp_path, monkeypatch):
    s, buf = _session_with_lines(tmp_path, monkeypatch)
    ui = SplitView(s, buf, None)
    ui._transcript_panel(height=20, width=80)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    k.handle(b"o")
    assert ui.prompt.startswith("Off the record")
    k.handle(b"\x1b[5~")                       # PgUp still scrolls while the prompt is open
    ui._transcript_panel(height=20, width=80)
    k.handle(b"\x1b[<0;10;4M")                 # click row 4 = 2nd line
    stamp = buf.all_lines()[1][1:9]
    assert ui.prompt.endswith(stamp)
    assert "2 lines will be deleted" in msgs[-1]
    k.handle(b"\r")
    assert [l.split(": ", 1)[1] for l in buf.all_lines() if "Speaker 0" in l] == ["keep"]
    assert s.paused_event.is_set()


def test_typed_time_and_bad_input(tmp_path, monkeypatch):
    s, buf = _session_with_lines(tmp_path, monkeypatch)
    ui = SplitView(s, buf, None)
    msgs = []
    k = KeyActions(s, buf, None, lambda t: True, msgs.append, ui=ui)
    k.handle(b"o")
    k.handle(b"nope\r")
    assert "line's time" in msgs[-1] and len(buf.all_lines()) == 3
    stamp = buf.all_lines()[2][1:9]
    k.handle(b"o")
    k.handle(f"[{stamp}]\r".encode())
    assert [l.split(": ", 1)[1] for l in buf.all_lines() if "Speaker 0" in l] == ["keep", "secret one"]
