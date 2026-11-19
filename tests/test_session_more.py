import time
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

import meeting_watch as mw
from rtt_post import me_ratio
from tests.test_session import FakeBuffer, make_session
from transcribe import TranscriptBuffer
from tui import KeyActions, SplitView, speaker_of


def test_off_the_record_removes_text_audio_marks_and_inflight_lines(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch, record=True)
    buf = TranscriptBuffer(tmp_path / "t.txt")
    s.buffer_ref = buf
    buf.add("[10:00:00] Speaker 0: keep me")
    buf._times[-1] -= 600                       # 10 minutes ago
    buf.add("[10:09:00] Speaker 1: secret")     # now
    s.mark("old")
    s.marks[-1]["t"] -= 600
    s.mark("secret mark")
    blk = np.full((16000, 1), 0.1, dtype=np.float32)
    now = time.time()
    for i in range(5):
        s.recorder.write("others", blk, wall=now - 4 + i)   # 5 s of audio, captured in real time
    time.sleep(0.5)
    r = s.off_the_record(minutes=2 / 60)         # last 2 seconds
    assert r["lines_removed"] == 1 and buf.all_lines() == ["[10:00:00] Speaker 0: keep me"]
    assert "secret" not in (tmp_path / "t.txt").read_text()
    assert [m["note"] for m in s.marks] == ["old"]
    assert s.paused_event.is_set()
    # speech that was still in flight is dropped while paused; markers still pass
    assert s.accept_line("Speaker 1: late secret") is False
    assert s.accept_line("[▶ RTT resumed]") is True
    s.resume()
    assert s.accept_line("Speaker 1: new") is True
    s.close()
    a, sr = sf.read(str(s.dir / "others.flac"))
    assert abs(len(a) / sr - (5 - r["audio_seconds_removed"])) < 0.05
    assert 1.2 <= r["audio_seconds_removed"] <= 2.6   # last ~2 s of capture time (test sleeps 0.5 s)
    assert s.redactions and lines[-1].startswith("[▶ RTT resumed")


def test_switch_request(tmp_path, monkeypatch):
    s, lines = make_session(tmp_path, monkeypatch)
    assert s.request_switch(" VIBE ") == "vibe"
    assert s.take_switch_request() == "vibe" and s.take_switch_request() is None
    try:
        s.request_switch("nope")
        assert False
    except ValueError:
        pass
    s.set_backend("vibe")
    assert s.backend == "vibe" and lines[-1] == "[⇄ backend openai → vibe]"


def test_speaker_of():
    assert speaker_of("[10:00:00] Speaker 0: hi") == "0"
    assert speaker_of("[10:00:00] Speaker Guest-2: a: b") == "Guest-2"
    assert speaker_of("[10:00:00] [★ RTT bookmark: Speaker x]") is None
    assert speaker_of("[10:00:00] [⏸ RTT paused]") is None


def test_rename_display_name_repoints_original(tmp_path):
    buf = TranscriptBuffer(tmp_path / "t.txt")
    buf.add("[10:00:00] Speaker 0: hi")
    buf.rename_speaker("0", "Amit")
    buf.rename_speaker("Amit", "Amit Cohen")
    assert buf.all_lines() == ["[10:00:00] Speaker Amit Cohen: hi"]
    assert "Amit Cohen" in (tmp_path / "t.txt").read_text()


def test_mouse_click_opens_rename_for_that_line(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch)
    buf = TranscriptBuffer(tmp_path / "t.txt")
    buf.add("[10:00:00] Speaker 0: first")
    buf.add("[10:00:05] Speaker 1: second")
    s.buffer_ref = buf
    ui = SplitView(s, buf, None)
    ui._transcript_panel(height=20, width=80)    # builds the click map
    assert ui.click_map == [(3, 3, "0"), (4, 4, "1")]
    k = KeyActions(s, buf, None, lambda t: True, lambda m: None, ui=ui)
    k.handle(b"\x1b[<0;10;4M")                   # left click, row 4 -> Speaker 1
    assert ui.prompt.startswith("Rename speaker \u201c1\u201d")
    k.handle("דנה\r".encode("utf-8"))
    assert buf.all_lines()[1] == "[10:00:05] Speaker דנה: second"
    k.handle(b"\x1b[<0;100;4M")                  # click in summary column: ignored
    assert ui.prompt is None
    k.handle(b"\x1b[<0;10;4m")                   # release events ignored
    assert ui.prompt is None


def test_escape_handling(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch)
    msgs = []
    k = KeyActions(s, FakeBuffer(), None, lambda t: True, msgs.append)
    k.handle(b"M")
    k.handle(b"\x1b")
    k._esc_at -= 1
    k.tick()                                      # lone Esc cancels the prompt
    assert msgs[-1] == "Cancelled."
    k.handle(b"\x1b[A")                           # arrow key: ignored, no crash
    k.handle(b" ")
    assert s.paused_event.is_set()


def test_watcher_state_machine(monkeypatch):
    t = [0.0]
    users = []
    running = [False]
    events = []
    w = mw.Watcher(mic_users=lambda: list(users), rtt_running=lambda: running[0],
                   ask_start=lambda app: events.append(("ask_start", app)) or True,
                   ask_stop=lambda: events.append(("ask_stop",)) or True,
                   start=lambda: (events.append(("start",)), running.__setitem__(0, True)),
                   stop=lambda: (events.append(("stop",)), running.__setitem__(0, False)),
                   clock=lambda: t[0], log=lambda m: None)
    users.append((1, "com.microsoft.teams2"))
    w.step()
    assert events == []                           # not yet START_AFTER
    t[0] += mw.START_AFTER
    w.step()
    assert events == [("ask_start", "com.microsoft.teams2"), ("start",)]
    t[0] += 100
    w.step()
    assert len(events) == 2                       # no re-prompt during the call
    users.clear()
    w.step()
    t[0] += mw.STOP_AFTER
    w.step()
    assert events[-2:] == [("ask_stop",), ("stop",)]
    # a call where RTT is already running by hand: never prompt, never stop it
    running[0] = True
    users.append((1, "us.zoom.xos"))
    t[0] += 100
    w.step()
    t[0] += mw.START_AFTER
    w.step()
    users.clear()
    w.step()
    t[0] += mw.STOP_AFTER
    w.step()
    assert events[-1] == ("stop",) and len(events) == 4
    users.append((2, "com.apple.python3"))        # non-meeting app using the mic
    w.step()
    assert w.in_call_since is None


def test_me_ratio():
    sr = 16000
    tracks = {"mic": np.concatenate([np.full(sr, 0.3), np.full(sr, 0.01)]).astype(np.float32),
              "others": np.concatenate([np.full(sr, 0.01), np.full(sr, 0.3)]).astype(np.float32)}
    assert me_ratio(tracks, sr, 0, 1) > 2 and me_ratio(tracks, sr, 1, 2) < 0.5
    assert me_ratio({"audio": tracks["mic"]}, sr, 0, 1) == 0.0


def test_scroll_focus_wheel_and_keys(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch)
    buf = TranscriptBuffer(tmp_path / "t.txt")
    for i in range(50):
        buf.add(f"[10:00:{i:02d}] Speaker {i % 3}: line {i}")
    s.buffer_ref = buf

    class Summ:
        model = "m"
        sections = {"Summary": [f"point {i}" for i in range(40)], "Decisions": [], "Action items": [], "Open questions": []}
        def status(self): return {"error": None, "busy": False, "last_update": None}
    ui = SplitView(s, buf, Summ())
    k = KeyActions(s, buf, None, lambda t: True, lambda m: None, ui=ui)
    def tx_text(): return str(ui._transcript_panel(height=12, width=80).renderable)
    def sum_text(): return str(ui._summary_panel(height=12, width=60).renderable)
    assert "line 49" in tx_text() and ui.tx_top is None            # follows live
    k.handle(b"\x1b[5~")                                              # PgUp on transcript
    t = tx_text()
    assert "line 49" not in t and ui.tx_top is not None
    assert ui.click_map and ui.click_map[0][0] == 3                   # clicks map to scrolled rows
    k.handle(b"\x1b[F")                                               # End -> live again
    assert ui.tx_top is None and "line 49" in tx_text()
    k.handle(b"\x1b[<64;10;5M")                                       # wheel up over transcript
    assert ui.tx_top is not None
    k.handle(b"\x1b[<65;10;5M" * 10)                                  # wheel down back to live
    tx_text()
    assert ui.tx_top is None
    assert "point 0" in sum_text()
    k.handle(b"\t")                                                    # focus summary
    assert ui.focus == "sum"
    k.handle(b"\x1b[6~")                                              # PgDn summary
    assert "point 0" not in sum_text() and ui.sum_top > 0
    k.handle(b"\x1b[H")                                               # Home
    assert ui.sum_top == 0
    k.handle(b"\x1b[<65;100;5M")                                      # wheel over summary column
    sum_text()
    assert ui.sum_top == 3 and ui.focus == "sum"
    k.handle(b"\x1b[B")
    assert ui.sum_top == 4
    k.handle(b"M")                                                    # prompt open: keys go to prompt
    k.handle(b"\x1b[6~")                                              # ...but scrolling still works
    assert ui.sum_top > 4 and ui.prompt is not None
