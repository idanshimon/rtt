import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from chunked_backends import ChunkedHTTPBackend, OpenAITranscribeBackend, VibeVoiceLocalBackend
from speakers import MODEL_DEFAULT, SpeakerTracker, SpokenLine, relabel_text

# Real-voice fixtures stay outside the repo; tests skip when they are absent.
#   RTT_TEST_MULTI_VOICE: a recording that opens with ~13 s of one voice
#   RTT_TEST_ONE_VOICE:   ≥30 s of a single different speaker
import os
_PRIVATE = Path(__file__).resolve().parent.parent / "private" / "fixtures"
HEB = Path(os.environ.get("RTT_TEST_MULTI_VOICE", _PRIVATE / "multi-voice.wav"))
IDAN = Path(os.environ.get("RTT_TEST_ONE_VOICE", _PRIVATE / "one-voice.wav"))


def test_spokenline_is_a_str_with_metadata():
    ln = SpokenLine("Speaker A: hi", 1.5, 3.0, "A")
    assert ln == "Speaker A: hi" and ln.strip() == "Speaker A: hi"
    assert ln.with_text("Speaker 2: hi").label == "A"
    assert relabel_text("Speaker A: a: b", "A", "2") == "Speaker 2: a: b"


def test_formatters_keep_segment_times():
    lines = OpenAITranscribeBackend._format_diarized(
        {"segments": [{"speaker": "A", "text": "hi", "start": 1.2, "end": 2.0}]})
    assert lines == ["Speaker A: hi"] and lines[0].start == 1.2 and lines[0].label == "A"
    v = VibeVoiceLocalBackend._format_segments([{"Start": 0.5, "End": 4, "Speaker": 1, "Content": "שלום"}])
    assert v[0].start == 0.5 and v[0].label == "1"


class Fake(ChunkedHTTPBackend):
    def __init__(self):
        super().__init__(sample_rate=1000, chunk_seconds=10, api_key="k")

    def _transcribe_chunk(self, wav):
        return [SpokenLine("Speaker A: one", 0.0, 2.0, "A"), SpokenLine("Speaker B: two", 6.5, 8.0, "B")]


def test_lines_get_spoken_time_not_arrival_time():
    b = Fake()
    now = time.time()
    b.push_audio(np.full(10_000, 0.2, dtype=np.float32), captured_at=now)   # 10 s ending now
    b.transcribe_ready()
    out = b.drain_text()
    assert abs(out[0].t - (now - 10)) < 0.01 and abs(out[1].t - (now - 3.5)) < 0.01


needs_model = pytest.mark.skipif(not (MODEL_DEFAULT.exists() and HEB.exists() and IDAN.exists()),
                                 reason="speaker model or fixtures missing")


def _load(p, sr=16000):
    a, s = sf.read(str(p), dtype="float32")
    a = a.mean(1) if a.ndim > 1 else a
    if s != sr:
        x = np.arange(0, len(a) * sr / s) * s / sr
        a = np.interp(x, np.arange(len(a)), a).astype(np.float32)
    return a


@needs_model
def test_tracker_keeps_same_voice_across_chunks_and_swapped_local_labels():
    tr = SpeakerTracker.create()
    assert tr is not None
    sr = 16000
    idan = _load(IDAN)[: 30 * sr]
    other = _load(HEB)[: 30 * sr]          # podcast host, opening 13 s is one voice
    def chunk(first, second):
        return np.concatenate([first, second])
    # chunk 1: Idan = A, other = B
    c1 = chunk(idan[: 6 * sr], other[: 6 * sr])
    o1 = tr.relabel(c1, sr, [SpokenLine("Speaker A: x", 0, 6, "A"), SpokenLine("Speaker B: y", 6, 12, "B")])
    # chunk 2: order swapped, so the backend calls the other voice A
    c2 = chunk(other[6 * sr: 12 * sr], idan[6 * sr: 12 * sr])
    o2 = tr.relabel(c2, sr, [SpokenLine("Speaker A: y", 0, 6, "A"), SpokenLine("Speaker B: x", 6, 12, "B")])
    idan1 = o1[0].split(":")[0]
    idan2 = o2[1].split(":")[0]
    assert idan1 == idan2 and o1[1].split(":")[0] == o2[0].split(":")[0] and idan1 != o2[0].split(":")[0]
    assert tr.snapshot()["speakers"] == 2


@needs_model
def test_too_short_voice_is_marked_unknown():
    tr = SpeakerTracker.create()
    sr = 16000
    a = _load(IDAN)[: 10 * sr]
    o = tr.relabel(a, sr, [SpokenLine("Speaker A: long", 0, 8, "A"), SpokenLine("Speaker B: mm", 8.0, 8.4, "B")])
    assert o[0].startswith("Speaker 1:") and o[1].startswith("Speaker ?:")


def test_title_only_when_in_call():
    import threading
    from session import title_when_in_call
    class S:
        title = None
        def set_meeting_title(self, t): self.title = t
    s, stop = S(), threading.Event()
    calls = iter([None, None, "com.microsoft.teams2"])
    title_when_in_call(s, stop, poll_s=0, lookup=lambda: "Weekly sync", in_call=lambda: next(calls))
    assert s.title == "Weekly sync"
    s2, stop2 = S(), threading.Event()
    stop2.set()
    title_when_in_call(s2, stop2, poll_s=0, lookup=lambda: "X", in_call=lambda: None)
    assert s2.title is None
