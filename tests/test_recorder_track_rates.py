import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from session import AudioRecorder


def test_mic_hq_track_keeps_its_own_rate_and_resume_length():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        rec = AudioRecorder(d, 16000)
        rec.start()
        now = time.time()
        rec.write("mic", np.zeros(16000, np.float32), wall=now)
        rec.write("mic_hq", np.zeros(48000, np.float32), wall=now)
        rec.stop()
        assert sf.info(str(d / "mic.flac")).samplerate == 16000
        hq = sf.info(str(d / "mic_hq.flac"))
        assert hq.samplerate == 48000 and abs(hq.frames / hq.samplerate - 1.0) < 1e-6
        # Resume: earlier audio length is measured in each file's own rate.
        rec2 = AudioRecorder(d, 16000)
        assert abs(rec2._base["mic_hq"] - 1.0) < 1e-6
        assert abs(rec2._base["mic"] - 1.0) < 1e-6


def test_redact_cuts_hq_track_in_its_own_rate():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        rec = AudioRecorder(d, 16000)
        rec.start()
        t0 = time.time() - 10
        for i in range(4):
            rec.write("mic_hq", np.ones(48000, np.float32) * 0.1, wall=t0 + i + 1)
        time.sleep(0.5)
        rec.redact_since(t0 + 2)
        rec.stop()
        hq = sf.info(str(d / "mic_hq.flac"))
        assert abs(hq.frames / hq.samplerate - 2.0) < 0.05


def test_decimator_passes_speech_band_blocks_aliases_and_is_seamless():
    from transcribe import _Decimator
    sr = 48000
    t = np.arange(sr) / sr
    tone = lambda f: np.sin(2 * np.pi * f * t).astype(np.float32)
    rms = lambda a: float(np.sqrt(np.mean(a[200:] ** 2)))
    assert abs(rms(_Decimator(3).process(tone(1000))) - rms(tone(1000))) < 0.02
    assert rms(_Decimator(3).process(tone(10000))) < 0.01
    x = tone(1000) + 0.3 * tone(3000)
    whole = _Decimator(3).process(x)
    d = _Decimator(3)
    parts = np.concatenate([d.process(x[i:i + 4800]) for i in range(0, len(x), 4800)])
    assert len(parts) == len(whole) == sr // 3
    assert np.allclose(parts, whole, atol=1e-5)
