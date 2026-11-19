"""Run RTT on the bundled sample without loading live settings."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    demo = Path(__file__).resolve().parent
    work = Path(tempfile.mkdtemp(prefix="rtt-demo-")).resolve()
    transcript = work / "transcript.txt"
    env = {k: v for k, v in os.environ.items() if not k.startswith("RTT_")}
    # The socket path is relative to the private cwd to stay below AF_UNIX limits.
    env.update(RTT_RUN_DIR="run", RTT_SPEAKER_TRACKING="0", RTT_VOCABULARY="none",
               RTT_LEARNED_CORRECTIONS="none", RTT_CORRECTIONS_FILE="none",
               RTT_STOP_FLUSH_TIMEOUT="120", HF_HUB_DISABLE_TELEMETRY="1")
    print(f"Demo output: {work}", flush=True)
    result = subprocess.run([
        sys.executable, str(demo.parent / "transcribe.py"), "--backend", "local",
        "--model-size", "base", "--language", "en", "--window", "25",
        "--input-file", str(demo / "garden.wav"), "--output-file", str(transcript),
        "--session-name", "garden-demo", "--no-summary", "--no-record", "--no-tui",
    ], cwd=work, env=env, stdin=subprocess.DEVNULL)
    if result.returncode:
        return result.returncode
    if not transcript.is_file() or not transcript.read_text(encoding="utf-8").strip():
        sys.exit("RTT did not write a transcript. Check the model and messages above.")
    print(f"Transcript: {transcript}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
