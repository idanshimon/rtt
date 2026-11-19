import json
import threading
import time

import shutil
import tempfile
from pathlib import Path

import pytest

import rtt_mcp
import session as sess_mod
from session import ControlServer, Session
from transcribe import TranscriptBuffer


@pytest.fixture
def live(tmp_path, monkeypatch):
    run = Path(tempfile.mkdtemp(prefix="rttm", dir="/tmp"))   # AF_UNIX paths must be short
    monkeypatch.setattr(sess_mod, "RUN_DIR", run)
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", run / "s.sock")
    monkeypatch.setattr(rtt_mcp, "SESSIONS", tmp_path / "sessions")
    tp = tmp_path / "t.txt"
    tp.write_text("")
    s = Session(backend="openai", transcript_path=tp, paused_event=threading.Event(),
                stop_event=threading.Event(), emit=lambda t: None, sessions_root=tmp_path / "sessions")
    buf = TranscriptBuffer(tp)
    s.buffer_ref = buf
    buf.add("[14:45:10] Speaker 1: hello there")
    srv = ControlServer(s)
    srv.start()
    yield s
    srv._running = False
    srv._sock.close()
    shutil.rmtree(run, ignore_errors=True)


def call(name, **args):
    r = rtt_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r["result"]
    return res["isError"], res["content"][0]["text"]


def test_protocol_handshake_and_list():
    r = rtt_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["serverInfo"]["name"] == "rtt"
    assert rtt_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in rtt_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"rtt_start", "rtt_new_session", "rtt_transcript", "rtt_off_record", "rtt_switch_backend"} <= names


def test_actions_go_through_socket_and_ledger(live):
    err, txt = call("rtt_status")
    assert not err and json.loads(txt)["status"]["backend"] == "openai"
    assert not call("rtt_pause")[0] and live.paused_event.is_set()
    assert not call("rtt_resume")[0] and not live.paused_event.is_set()
    assert not call("rtt_mute", muted=True)[0] and live.mic_muted
    assert not call("rtt_mark", note="decision")[0] and live.marks[-1]["note"] == "decision"
    assert not call("rtt_switch_backend", backend="vibe")[0] and live.take_switch_request() == "vibe"
    assert call("rtt_switch_backend", backend="nope")[0]
    err, txt = call("rtt_log", last=3)
    assert not err and all(e.get("source") == "ctl" for e in json.loads(txt)["entries"])
    err, txt = call("rtt_transcript", last_lines=5)
    assert not err and "hello there" in json.loads(txt)["text"]
    assert call("rtt_start")[0]                         # already running → refused


def test_not_running_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sess_mod, "SOCKET_PATH", Path("/tmp/rtt-mcp-test-none.sock"))
    err, txt = call("rtt_pause")
    assert err and "not running" in txt


def test_transcript_since_untimed_lines_and_cap(tmp_path, monkeypatch):
    d = tmp_path / "sessions" / "20261113-090000-x"
    d.mkdir(parents=True)
    (d / "transcript.txt").write_text("[09:00:00] A: early\n[09:15:09] B: from here\ncontinued line\n[09:20:00] A: late\n")
    monkeypatch.setattr(rtt_mcp, "SESSIONS", tmp_path / "sessions")
    r = rtt_mcp.t_transcript(last_lines=0, session="x", since="9:15")
    assert r["text"].splitlines() == ["[09:15:09] B: from here", "continued line", "[09:20:00] A: late"]
    assert r["total_lines"] == 4 and r["returned_lines"] == 3
    assert rtt_mcp.t_transcript(last_lines=1, session="x")["text"] == "[09:20:00] A: late"
    monkeypatch.setattr(rtt_mcp, "MAX_CHARS", 30)
    r = rtt_mcp.t_transcript(last_lines=0, session="x")
    assert r["truncated"] and r["text"] == "[09:20:00] A: late"
    assert call("rtt_transcript", session="x", since="later")[0]


def test_unexpected_args_and_annotations():
    err, txt = call("rtt_pause", bogus=1)
    assert err and "TypeError" in txt
    tools = {t["name"]: t for t in rtt_mcp.tool_list()}
    assert tools["rtt_transcript"]["annotations"]["readOnlyHint"]
    assert tools["rtt_off_record"]["annotations"]["destructiveHint"]


def test_stdout_is_reserved_for_protocol():
    import subprocess, sys
    out = subprocess.run([sys.executable, "rtt_mcp.py"], input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
                         capture_output=True, text=True, timeout=30).stdout
    assert [json.loads(l) for l in out.splitlines()] == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
