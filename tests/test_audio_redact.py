import time

import numpy as np
import soundfile as sf

from session import AudioRecorder

SR = 16000
SEC = np.full(SR, 0.1, dtype=np.float32)


def _run(rec, writes):
    rec.start()
    for wall in writes:
        rec.write("others", SEC, wall=wall)
    time.sleep(0.4)


def _len(path):
    return sf.info(str(path)).frames / SR


def test_cut_by_capture_time_across_a_pause(tmp_path):
    t0 = 1_000_000.0
    rec = AudioRecorder(tmp_path, SR)
    # 0-3 s recorded, paused 60 s (nothing recorded), then 3 s more
    _run(rec, [t0 + 1, t0 + 2, t0 + 3, t0 + 64, t0 + 65, t0 + 66])
    removed = rec.redact_since(t0 + 63.5)     # second part covers t0+63..66: cut 0.5 s into it
    rec.stop()
    assert abs(_len(tmp_path / "others.flac") - 3.5) < 0.05   # not 66 wall-seconds cut
    assert abs(removed - 2.5) < 0.05


def test_crash_leftover_wav_is_kept_on_resume(tmp_path):
    with sf.SoundFile(str(tmp_path / "others.wav"), mode="w", samplerate=SR, channels=1,
                      format="WAV", subtype="PCM_16") as fh:
        fh.write(np.full(SR * 2, 0.1, np.float32))       # 2 s left by a crash
    rec = AudioRecorder(tmp_path, SR)
    _run(rec, [time.time()])
    rec.stop()
    assert abs(_len(tmp_path / "others.flac") - 3.0) < 0.05


def test_cut_reaches_into_earlier_run(tmp_path):
    t0 = 2_000_000.0
    rec = AudioRecorder(tmp_path, SR)
    _run(rec, [t0 + 1, t0 + 2, t0 + 3, t0 + 4])           # first run: 4 s
    rec.stop()
    rec2 = AudioRecorder(tmp_path, SR)                    # resumed later
    _run(rec2, [t0 + 101, t0 + 102])                      # second run: 2 s
    rec2.redact_since(t0 + 2)                             # line from the first run, 2 s in
    rec2.stop()
    assert abs(_len(tmp_path / "others.flac") - 2.0) < 0.05   # first run's 0-2 s kept, rest gone


def test_everything_cut_leaves_no_broken_file(tmp_path):
    t0 = 3_000_000.0
    rec = AudioRecorder(tmp_path, SR)
    _run(rec, [t0 + 1, t0 + 2])
    rec.redact_since(t0 - 10)
    rec.stop()
    assert not (tmp_path / "others.flac").exists() and not (tmp_path / "others.wav").exists()


def test_second_cut_after_cutting_into_earlier_run(tmp_path):
    t0 = 4_000_000.0
    rec = AudioRecorder(tmp_path, SR)
    _run(rec, [t0 + i for i in range(1, 11)])             # run 1: 10 s (t0..t0+10)
    rec.stop()
    rec2 = AudioRecorder(tmp_path, SR)
    _run(rec2, [t0 + 100 + i for i in range(1, 11)])      # run 2: 10 s
    rec2.redact_since(t0 + 5)                             # back to 5 s of run 1
    for i in range(1, 6):
        rec2.write("others", SEC, wall=t0 + 300 + i)      # 5 s more (t0+300..305)
    time.sleep(0.4)
    assert abs(rec2.position_at("others", t0 + 302) - 7.0) < 0.05
    rec2.redact_since(t0 + 302)
    rec2.stop()
    assert abs(_len(tmp_path / "others.flac") - 7.0) < 0.05
