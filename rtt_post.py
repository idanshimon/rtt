#!/usr/bin/env python3
"""Second pass on a recorded session: re-transcribe the whole recording with local VibeVoice.

  rtt-post                 latest session in sessions/
  rtt-post <session_dir>   a specific session

Live text is fast but chunked (speaker ids reset per chunk). This pass runs the full
recording through VibeVoice in up to 50-minute pieces, so speaker labels stay consistent
within each piece. Output: <session>/transcript.final.txt. Needs the session to have been
recorded with --record, and the VibeVoice server (started automatically, like rttvibe).
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

REPO = Path(__file__).resolve().parent
URL = os.environ.get("RTT_VIBE_URL", "http://127.0.0.1:8765")
PIECE_S = int(os.environ.get("RTT_POST_PIECE_SECONDS", str(50 * 60)))


def latest_session() -> Path:
    sessions = sorted((REPO / "sessions").glob("*/session.json"))
    if not sessions:
        sys.exit("No sessions found.")
    return sessions[-1].parent


def _track(d: Path, name: str):
    for ext in (".flac", ".wav"):  # .wav remains if a session ended abruptly
        p = d / f"{name}{ext}"
        if p.exists():
            return p
    return None


def load_tracks(d: Path):
    """{name: mono float32 array} for others/mic/audio tracks, plus sample rate."""
    out, sr = {}, None
    for name in ("others", "mic", "audio"):
        p = _track(d, name)
        if p is None:
            continue
        a, r = sf.read(str(p), dtype="float32")
        sr = sr or r
        out[name] = a if a.ndim == 1 else a.mean(axis=1)
    return out, sr


def me_ratio(tracks: dict, sr: int, start: float, end: float) -> float:
    """mic RMS / others RMS over [start, end]; >1 means the speaker is most likely you."""
    if "mic" not in tracks or "others" not in tracks:
        return 0.0
    a, b = int(start * sr), max(int(end * sr), int(start * sr) + 1)
    mic, oth = tracks["mic"][a:b], tracks["others"][a:b]
    if not len(mic):
        return 0.0
    rm = float(np.sqrt(np.mean(mic ** 2)))
    ro = float(np.sqrt(np.mean(oth ** 2))) if len(oth) else 0.0
    return rm / max(ro, 1e-6)


def load_mix(d: Path):
    tracks = [p for p in (_track(d, "others"), _track(d, "mic"), _track(d, "audio")) if p is not None]
    if not tracks:
        sys.exit(f"No audio in {d}. Record with --record.")
    data, sr = [], None
    for p in tracks:
        a, r = sf.read(str(p), dtype="float32")
        sr = sr or r
        data.append(a if a.ndim == 1 else a.mean(axis=1))
    n = max(len(a) for a in data)
    mix = np.zeros(n, dtype=np.float32)
    for a in data:
        mix[: len(a)] += a
    np.clip(mix, -1, 1, out=mix)
    return mix, sr, [p.name for p in tracks]


def ensure_server() -> None:
    try:
        requests.get(URL + "/health", timeout=3).raise_for_status()
        return
    except Exception:
        pass
    print("Starting VibeVoice server…")
    subprocess.Popen([str(REPO / "run-vibe.sh"), "--show-config"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()
    requests.get(URL + "/health", timeout=3).raise_for_status()


def fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def main() -> None:
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_session()
    mix, sr, names = load_mix(d)
    tracks, _ = load_tracks(d)
    me_name = os.environ.get("RTT_ME_NAME", "Me")
    ensure_server()
    prompt = os.environ.get("RTT_VIBE_PROMPT", "")
    piece = PIECE_S * sr
    out_lines = []
    t0 = time.time()
    for i, start in enumerate(range(0, len(mix), piece)):
        seg = mix[start : start + piece]
        buf = io.BytesIO()
        sf.write(buf, seg, sr, format="WAV", subtype="PCM_16")
        headers = {"Content-Type": "audio/wav"}
        if prompt:
            from urllib.parse import quote
            headers["X-Prompt"] = quote(prompt)
        print(f"Piece {i + 1}: {fmt(start / sr)}–{fmt((start + len(seg)) / sr)} …", flush=True)
        r = requests.post(URL + "/transcribe", data=buf.getvalue(), headers=headers, timeout=3600)
        r.raise_for_status()
        off = start / sr
        for s in r.json().get("segments", []):
            text = (s.get("Content") or "").strip()
            if not text or (text.startswith("[") and text.endswith("]")):
                continue
            st = off + (s.get("Start") or 0)
            en = off + (s.get("End") or (s.get("Start") or 0) + 1)
            at = fmt(st)
            spk = s.get("Speaker")
            if me_ratio(tracks, sr, st, en) > 2.0:
                # Your mic track is far louder than the meeting track here: it's you.
                label = f"Speaker {me_name}"
            else:
                # Speaker ids are only consistent within one piece; prefix the piece when >1.
                label = f"Speaker {spk}" if spk is not None else "Speaker ?"
                if len(mix) > piece:
                    label += f" (part {i + 1})"
            out_lines.append(f"[{at}] {label}: {text}")
    out = d / "transcript.final.txt"
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    meta_p = d / "session.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["final_transcript"] = {"path": str(out), "tracks": names, "model": "VibeVoice-ASR 7B (local)",
                                "seconds_audio": round(len(mix) / sr), "seconds_processing": round(time.time() - t0)}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out} ({len(out_lines)} lines, {round(time.time() - t0)}s).")


if __name__ == "__main__":
    main()
