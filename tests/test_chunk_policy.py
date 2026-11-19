import numpy as np

from chunk_policy import ChunkPolicy, quietest_cut
from chunked_backends import ChunkedHTTPBackend, VibeVoiceLocalBackend
from session import send_command
from tests.test_session import FakeBuffer, make_session
from tui import KeyActions


def test_steps_and_bounds():
    p = ChunkPolicy(10, lo=3, hi=60)
    assert p.step(+1) == 15 and p.step(+1) == 20 and p.step(-1) == 15
    assert p.set(500) == 60 and p.step(+1) == 60
    assert p.set(1) == 3 and p.step(-1) == 3
    p.bounds(10, 120)
    assert p.seconds == 10


def test_auto_grows_when_behind_and_shrinks_back():
    seen = []
    p = ChunkPolicy(10, mode="auto", on_change=lambda pol: seen.append(pol.current))
    p.report(10, 9.5, 0)          # rtf .95 -> grow
    assert p.current == 12.5
    p.report(12.5, 2, 40)         # backlog > 2x window -> grow
    assert p.current > 15
    for _ in range(10):
        p.report(p.current, 1, 0)  # fast again -> shrink back to the chosen 10 s, not below
    assert p.current == 10 and seen
    f = ChunkPolicy(10)
    f.report(10, 9.9, 100)        # fixed mode never adapts
    assert f.current == 10 and f.last_rtf > 0.9


def test_quietest_cut_finds_pause():
    sr = 1000
    a = np.full(10 * sr, 0.3, dtype=np.float32)
    a[7200:7800] = 0.001           # pause at 7.2-7.8 s
    cut, quiet = quietest_cut(a, sr, 6000, 10000)
    assert 7200 <= cut <= 7800 and quiet
    _, quiet2 = quietest_cut(np.full(10 * sr, 0.3, dtype=np.float32), sr, 6000, 10000)
    assert quiet2 is False


class Rec(ChunkedHTTPBackend):
    def __init__(self, **kw):
        super().__init__(sample_rate=1000, chunk_seconds=10, api_key="k", **kw)
        self.sizes = []

    def _transcribe_chunk(self, wav):
        self.sizes.append(len(wav))
        return ["Speaker 0: x"]


def test_backend_auto_cuts_at_pause_and_live_resize():
    b = Rec()
    a = np.full(25 * 1000, 0.3, dtype=np.float32)
    a[7300:7700] = 0.0
    b.push_audio(a)
    shared = ChunkPolicy(10, mode="auto")
    b.attach_policy(shared)
    c = b._drain_ready_chunk()
    assert 7300 <= c.shape[0] <= 7700 and shared.quiet_cuts == 1
    shared.set_mode("fixed")
    shared.set(5)                  # live change takes effect on the next chunk
    assert b._drain_ready_chunk().shape[0] == 5000


def test_backend_bounds_and_cli_value_kept():
    v = VibeVoiceLocalBackend.__new__(VibeVoiceLocalBackend)
    p = ChunkPolicy(5)
    ChunkedHTTPBackend.attach_policy(v, p)
    assert p.seconds == 10 and p.hi == 120      # vibe minimum 10 s
    assert Rec().policy.seconds == 10
    tiny = ChunkedHTTPBackend(sample_rate=100, chunk_seconds=1, api_key="k")
    assert tiny.policy.seconds == 1             # explicit CLI value is not clamped


def test_session_keys_ctl_ledger_undo(tmp_path, monkeypatch):
    s, _ = make_session(tmp_path, monkeypatch)
    msgs = []
    k = KeyActions(s, FakeBuffer(), None, lambda t: True, msgs.append)
    k.handle(b"]")
    assert msgs[-1].startswith("Chunk size")      # no policy yet -> clear message
    s.chunk = ChunkPolicy(10)
    k.handle(b"]")
    k.handle(b"]")
    assert s.chunk.seconds == 20
    k.handle(b"[")
    k.handle(b"\\")
    assert s.chunk.seconds == 15 and s.chunk.mode == "auto"
    assert s.status()["chunk_label"] == "chunk auto 15s"
    s.undo()
    assert s.chunk.mode == "fixed" and s.chunk.seconds == 15
    s.undo()
    assert s.chunk.seconds == 20
    s.start_control_server()
    try:
        assert send_command({"cmd": "chunk", "value": "30"})["chunk"]["seconds"] == 30
        assert send_command({"cmd": "chunk", "value": "auto"})["chunk"]["mode"] == "auto"
        assert send_command({"cmd": "chunk", "value": "abc"})["ok"] is False
        log = send_command({"cmd": "log"})["entries"]
        assert [e["source"] for e in log if e["action"] == "chunk"][-2:] == ["ctl", "ctl"]
    finally:
        s.close()
