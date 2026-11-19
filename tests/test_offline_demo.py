"""The public demo must use a bundled synthetic fixture, not a local recording."""
import os
import subprocess
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"


def test_demo_audio_is_portable_pcm():
    clip = DEMO / "garden.wav"
    assert clip.is_file() and not clip.is_symlink()
    with wave.open(str(clip)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert 18 <= wav.getnframes() / wav.getframerate() <= 22


def test_demo_ignores_live_settings(tmp_path):
    # Exercise the actual launcher with a stand-in CLI; never start capture or fetch a model in CI.
    import shutil
    import sys
    (tmp_path / 'demo').mkdir()
    shutil.copy(DEMO / 'run.py', tmp_path / 'demo')
    shutil.copy(DEMO / 'garden.wav', tmp_path / 'demo')
    (tmp_path / 'transcribe.py').write_text('''
import os, sys
from pathlib import Path
args = sys.argv[1:]
def opt(flag): return args[args.index(flag) + 1]
assert opt('--backend') == 'local' and '--no-summary' in args
assert Path(opt('--input-file')).resolve() == Path(__file__).parent / 'demo/garden.wav'
assert Path.cwd() != Path(__file__).parent
assert Path(os.environ['RTT_RUN_DIR']).resolve().parent == Path.cwd()
assert 'RTT_INPUT_DEVICE' not in os.environ
out = Path(opt('--output-file')).resolve()
assert out.parent == Path.cwd()
out.write_text('The garden gate is open.')
''')
    result = subprocess.run([sys.executable, tmp_path / 'demo/run.py'], cwd=tmp_path,
                            env=dict(os.environ, TMPDIR=str(tmp_path), RTT_RUN_DIR='live',
                                     RTT_INPUT_DEVICE='microphone', RTT_SUMMARY='1'),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    transcripts = list(tmp_path.glob('rtt-demo-*/transcript.txt'))
    assert len(transcripts) == 1 and str(transcripts[0]) in result.stdout
    assert not (tmp_path / 'sessions').exists()
