"""RTT session layer: state, actions, markers, metadata, audio recording, control socket.

One `Session` object owns everything the user can control during a run. The
keyboard (TUI/hotkeys), the control socket (`rtt-ctl`) and the summarizer all
call the same methods, so behavior is identical regardless of where a command
comes from.

Layout on disk (the live transcript itself stays at transcripts/transcript-*.txt
so existing consumers such as livertt keep working):

    sessions/<YYYYMMDD-HHMMSS>[-name]/
        session.json        state, pauses, marks, backend, paths (rewritten on change)
        transcript.txt      symlink to the live transcript
        others.flac         system/meeting audio   (only with --record)
        mic.flac            your microphone        (only with --record, mic mixing)
        mic_hq.flac         your microphone at 48 kHz for voice training (--record, mic mixing)
        audio.flac          single-source capture  (only with --record, no mic mixing)
        summary.md          live summary           (only with --summary)
"""
from __future__ import annotations

import json
import os
import re
import queue
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import soundfile as sf

RUN_DIR = Path(os.environ.get("RTT_RUN_DIR", str(Path.home() / ".rtt" / "run")))
SOCKET_PATH = RUN_DIR / "rtt.sock"


def pick_current_meeting(lines: List[str], now: Optional[datetime] = None) -> Optional[str]:
    """From 'HH:MM-HH:MM | title' lines pick the meeting happening now (or starting within 10 min)."""
    import re
    now = now or datetime.now()
    mins = now.hour * 60 + now.minute
    current, upcoming = [], []
    for ln in lines:
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*\|\s*(.+)", ln)
        if not m:
            continue
        st = int(m.group(1)) * 60 + int(m.group(2))
        en = int(m.group(3)) * 60 + int(m.group(4))
        title = m.group(5).strip().strip("*").strip()
        title = re.sub(r"\s*\[\d+\]\(.*?\)\s*$", "", title)   # strip citation links
        if en <= st or en - st >= 23 * 60:                       # all-day / malformed
            continue
        if st - 5 <= mins < en:
            current.append((st, title))
        elif 0 <= st - mins <= 10:
            upcoming.append((st, title))
    if current:
        return sorted(current)[-1][1]       # latest-started of overlapping meetings
    if upcoming:
        return sorted(upcoming)[0][1]
    return None


MEETING_APPS = ("com.microsoft.teams2", "com.microsoft.teams", "us.zoom.xos", "com.cisco.webexmeetingsapp")


def meeting_app_on_mic() -> Optional[str]:
    try:
        from mic_activity import mic_users
        return next((b for _, b in mic_users() if b in MEETING_APPS), None)
    except Exception:
        return None


def title_when_in_call(session, stop_event, poll_s: float = 15.0, lookup=None, in_call=None) -> None:
    """Name the session from the calendar only once a Teams/Zoom/Webex call is actually using the
    mic, so a scheduled meeting you are not in never labels your session."""
    lookup = lookup or lookup_meeting_title
    in_call = in_call or meeting_app_on_mic
    while not stop_event.is_set():
        if in_call():
            title = lookup()
            if title:
                session.set_meeting_title(title)
            return
        stop_event.wait(poll_s)


def lookup_meeting_title(timeout: float = 60.0) -> Optional[str]:
    """Current/next meeting title from the M365 calendar via the `workiq` CLI (best effort)."""
    import shutil
    import subprocess
    if not shutil.which("workiq"):
        return None
    q = ("List all of my calendar meetings for today, one per line, exactly in this format: "
         "HH:MM-HH:MM | title  (24-hour local start and end time). Skip all-day events. "
         "No other text, no links, no numbering.")
    try:
        out = subprocess.run(["workiq", "ask", "-q", q], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return pick_current_meeting(out.stdout.splitlines())


def _rtt_process_starts() -> List[float]:
    """Start times of running transcribe.py processes (other than this one)."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-ww", "-axo", "pid=,lstart=,command="], capture_output=True, text=True,
                             env={**os.environ, "LC_ALL": "C"}).stdout
    except Exception:
        return []
    starts = []
    for ln in out.splitlines():
        parts = ln.split(None, 6)
        if len(parts) < 7 or "transcribe.py" not in parts[6] or int(parts[0]) == os.getpid():
            continue
        try:
            starts.append(datetime.strptime(" ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y").timestamp())
        except ValueError:
            pass
    return starts


def session_is_running(meta: dict) -> bool:
    """True if the RTT process that owns this session is still alive."""
    if meta.get("ended_at"):         # closed (stop, or N inside an RTT that keeps running)
        return False
    pid = meta.get("pid")
    if not pid:
        # Written by an older RTT without a pid: live if never closed and a transcribe.py
        # process started within a few seconds of the session.
        if meta.get("ended_at") or not meta.get("started_at"):
            return False
        try:
            start = datetime.fromisoformat(meta["started_at"]).timestamp()
        except ValueError:
            return False
        return any(abs(t - start) <= 5 for t in _rtt_process_starts())
    if pid == os.getpid():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        import subprocess
        cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        return "transcribe.py" in cmd
    except Exception:
        return True


def find_session(root: Path, query: Optional[str] = None) -> Path:
    """Find a saved session folder: latest (query None/''), exact id, id prefix, or text in
    the folder name / name / meeting title (latest match wins). Raises FileNotFoundError."""
    root = Path(root)
    def meta(d):
        try:
            return json.loads((d / "session.json").read_text(encoding="utf-8"))
        except Exception:
            return {}
    # Never pick a session another RTT is still writing (its transcript and ledger are live).
    dirs = [d for d in root.iterdir() if (d / "session.json").exists()
            and not session_is_running(meta(d))] if root.exists() else []
    if not dirs:
        raise FileNotFoundError(f"no stopped sessions in {root.resolve()}")
    def last_active(d):
        m = meta(d)
        return max([m.get("started_at") or "", m.get("ended_at") or ""] +
                   [r.get("at") or "" for r in m.get("resumes") or []])
    dirs.sort(key=lambda d: (last_active(d), d.name))
    q = (query or "").strip()
    if not q:
        return dirs[-1]
    for d in dirs:
        if d.name == q or meta(d).get("id") == q:
            return d
    ql = q.lower()
    hits = [d for d in dirs if d.name.lower().startswith(ql)] or [
        d for d in dirs if ql in " ".join(str(x) for x in (d.name, meta(d).get("name"), meta(d).get("meeting_title"))).lower()]
    if not hits:
        raise FileNotFoundError(f"no saved session matches {q!r} in {root.resolve()}")
    return hits[-1]


MARKER_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[(⏸|▶|🔇|🎙|★ RTT bookmark)")


def burst_marker_indices(lines: List[str], gap_s: float = 3.0, min_run: int = 4) -> List[int]:
    """Status lines that came in a burst (keys fired by accident). See meetalign.bursts."""
    from meetalign.bursts import find_bursts
    return find_bursts(lines, MARKER_RE, gap_s, min_run)


def tidy_file(path: Path) -> int:
    """Remove burst status lines from a transcript file of a session that is not running."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    drop = set(burst_marker_indices(lines))
    if drop:
        keep = [ln for i, ln in enumerate(lines) if i not in drop]
        tmp = Path(str(path) + ".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        tmp.replace(path)
    return len(drop)


def _now_hms() -> str:
    return time.strftime("%H:%M:%S")


class AudioRecorder:
    """Writes named mono tracks on a background thread.

    Recording goes to WAV (so the tail can be cut for off-the-record), and each
    track is converted to FLAC when the session closes. Capture callbacks call
    `write(track, block)`; this never blocks the audio thread (bounded queue,
    drops with a counter if the disk stalls).

    Resume-safe: audio already in <track>.flac (earlier runs) and any <track>.wav a
    crash left behind are kept; new audio is appended on close. `audio_index.json`
    maps wall-clock time to the audio position of each track, so off-the-record
    cuts the right audio even across pauses and resumes.
    """

    # Tracks recorded at their own rate instead of the capture rate (voice-training quality).
    TRACK_RATES: Dict[str, int] = {"mic_hq": 48000}

    def __init__(self, directory: Path, sample_rate: int) -> None:
        self._dir = directory
        self._sr = sample_rate
        self._q: "queue.Queue" = queue.Queue(maxsize=2000)
        self._files: Dict[str, sf.SoundFile] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._io_lock = threading.Lock()
        self.dropped_blocks = 0
        self.seconds: Dict[str, float] = {}
        self.enabled = True  # False while paused / off-the-record
        self.final_paths: List[str] = []
        self._base: Dict[str, float] = {}        # seconds already in <track>.flac
        self._flac_keep: Dict[str, float] = {}   # cut earlier-run audio to this many seconds at close
        self._index: Dict[str, List[list]] = {}  # track -> [[wall, seconds_total], ...]
        self._recover_leftover_wavs()
        try:
            self._index = json.loads((self._dir / "audio_index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        for flac in self._dir.glob("*.flac"):
            try:
                info = sf.info(str(flac))
                self._base[flac.stem] = info.frames / info.samplerate
            except Exception:
                pass

    def _recover_leftover_wavs(self) -> None:
        """A crash leaves <track>.wav unconverted: fold it into the FLAC before recording again."""
        for wav in self._dir.glob("*.wav"):
            try:
                data, sr = sf.read(str(wav), dtype="float32")
                flac = wav.with_suffix(".flac")
                if flac.exists():
                    old, _ = sf.read(str(flac), dtype="float32")
                    data = np.concatenate([old.reshape(-1), data.reshape(-1)])
                sf.write(str(flac), data, sr, format="FLAC", subtype="PCM_16")
                wav.unlink()
            except Exception:
                pass   # keep the WAV untouched rather than lose audio

    def rate(self, track: str) -> int:
        return self.TRACK_RATES.get(track, self._sr)

    def start(self) -> None:
        self._thread.start()

    def write(self, track: str, block: np.ndarray, wall: Optional[float] = None) -> None:
        """`wall` = when the block's capture ended (default now; tests pass it explicitly)."""
        if not self.enabled:
            return
        try:
            self._q.put_nowait((track, np.asarray(block, dtype=np.float32).reshape(-1),
                                time.time() if wall is None else wall))
        except queue.Full:
            self.dropped_blocks += 1

    def _loop(self) -> None:
        while not (self._stop.is_set() and self._q.empty()):
            try:
                track, data, wall = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._io_lock:
                fh = self._files.get(track)
                if fh is None:
                    wav = self._dir / f"{track}.wav"
                    fh = sf.SoundFile(str(wav), mode="w+", samplerate=self.rate(track), channels=1,
                                      format="WAV", subtype="PCM_16")
                    self._files[track] = fh
                start = self._base.get(track, 0.0) + self.seconds.get(track, 0.0)
                fh.write(data)
                sr = self.rate(track)
                self.seconds[track] = self.seconds.get(track, 0.0) + len(data) / sr
                idx = self._index.setdefault(track, [])
                if not idx or wall - idx[-1][0] >= 1.0:
                    idx.append([round(wall - len(data) / sr, 3), round(start, 3)])

    def position_at(self, track: str, wall: float) -> Optional[float]:
        """Seconds into the track (earlier runs included) of audio captured at `wall`."""
        idx = self._index.get(track) or []
        prev = None
        for w, pos in idx:
            if w > wall:
                break
            prev = (w, pos)
        if prev is None:
            return 0.0 if idx else None
        nxt = next((p for w, p in idx if w > wall), None)
        est = prev[1] + (wall - prev[0])
        return min(est, nxt) if nxt is not None else est

    def redact_since(self, cutoff_wall: float) -> float:
        """Cut every track from the audio captured at `cutoff_wall` on. Returns seconds removed."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        removed = 0.0
        with self._io_lock:
            tracks = set(self._files) | set(self._base)
            for track in tracks:
                base = self._base.get(track, 0.0)
                total = base + self.seconds.get(track, 0.0)
                pos = self.position_at(track, cutoff_wall)
                if pos is None:
                    pos = total
                pos = max(0.0, min(pos, total))
                fh = self._files.get(track)
                if fh is not None:
                    sr = self.rate(track)
                    keep = int(max(0.0, pos - base) * sr)
                    fh.truncate(keep)
                    fh.seek(keep)
                    self.seconds[track] = keep / sr
                if pos < base:                    # reaches into an earlier run's FLAC
                    self._flac_keep[track] = min(self._flac_keep.get(track, base), pos)
                    self._base[track] = pos       # new audio now follows the trimmed earlier audio
                removed = max(removed, total - pos)
                self._index[track] = [e for e in self._index.get(track, []) if e[0] < cutoff_wall]
        return removed

    def redact_last(self, seconds: float) -> float:
        return self.redact_since(time.time() - seconds)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        with self._io_lock:
            for track in set(self._files) | set(self._flac_keep):
                fh = self._files.get(track)
                flac = self._dir / f"{track}.flac"
                wav = self._dir / f"{track}.wav"
                try:
                    if fh is not None:
                        fh.close()
                        data, sr = sf.read(str(wav), dtype="float32")
                    else:
                        data, sr = np.zeros(0, np.float32), self.rate(track)
                    if flac.exists():            # resumed session: append to the earlier audio
                        old, _ = sf.read(str(flac), dtype="float32")
                        old = old.reshape(-1)
                        if track in self._flac_keep:
                            old = old[:int(self._flac_keep[track] * sr)]
                        data = np.concatenate([old, data.reshape(-1)])
                    if data.size:
                        sf.write(str(flac), data, sr, format="FLAC", subtype="PCM_16")
                        self.final_paths.append(str(flac))
                    elif flac.exists():
                        flac.unlink()            # everything was taken off the record
                    if wav.exists():
                        wav.unlink()
                except Exception:
                    self.final_paths.append(str(wav))  # keep the WAV rather than lose audio
            self.save_index()

    def save_index(self) -> None:
        """Also called on every session save, so a crash keeps the index up to then."""
        try:
            tmp = self._dir / "audio_index.json.tmp"
            tmp.write_text(json.dumps(self._index), encoding="utf-8")
            tmp.replace(self._dir / "audio_index.json")
        except OSError:
            pass

    def paths(self) -> List[str]:
        if self.final_paths:
            return list(self.final_paths)
        return [str(self._dir / f"{t}.wav") for t in self._files]


class Ledger:
    """Append-only log of every user action in a session (sessions/<id>/actions.jsonl).

    Each entry: {id, at, action, source, params, undo?}. `undo` holds what is needed to
    reverse it. Undo never deletes history: it appends {"action": "undo", "target": id}.
    Agents read this to learn corrections (e.g. diarization label 0 was really Idan).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.entries: List[dict] = []
        self.loaded_upto = 0               # entries up to this id come from an earlier run
        if path.exists():                    # resumed session: keep history and id numbering
            for ln in path.read_text(encoding="utf-8").splitlines():
                try:
                    self.entries.append(json.loads(ln))
                except ValueError:
                    pass
            self.loaded_upto = max((e.get("id", 0) for e in self.entries), default=0)

    def record(self, action: str, source: str, params: Optional[dict] = None,
               undo: Optional[dict] = None) -> dict:
        with self._lock:
            e = {"id": len(self.entries) + 1, "at": datetime.now().isoformat(timespec="seconds"),
                 "action": action, "source": source, "params": params or {}}
            if undo is not None:
                e["undo"] = undo
            self.entries.append(e)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            return e

    def reverted_ids(self) -> set:
        return {e["params"].get("target") for e in self.entries if e["action"] == "undo"}

    def last_undoable(self, applicable=lambda e: True) -> Optional[dict]:
        done = self.reverted_ids()
        for e in reversed(self.entries):
            if e["id"] <= self.loaded_upto:
                break                      # actions from before a resume: their state is gone
            if "undo" in e and e["id"] not in done and applicable(e):
                return e
        return None

    def get(self, entry_id: int) -> Optional[dict]:
        return next((e for e in self.entries if e["id"] == entry_id), None)


class Session:
    def __init__(
        self,
        *,
        backend: str,
        transcript_path: Path,
        paused_event: threading.Event,
        stop_event: threading.Event,
        emit: Callable[[str], None],
        name: Optional[str] = None,
        sessions_root: Path = Path("sessions"),
        record: bool = False,
        sample_rate: int = 16000,
        notify: Optional[Callable[[str], None]] = None,
        resume_dir: Optional[Path] = None,
    ) -> None:
        self.started_at = time.time()
        self.sessions_root = sessions_root
        self.sample_rate = sample_rate
        self.record_enabled = record
        prev: dict = {}
        if resume_dir is not None:
            self.dir = Path(resume_dir)
            self.id = self.dir.name
            prev = json.loads((self.dir / "session.json").read_text(encoding="utf-8"))
            try:
                self.started_at = datetime.fromisoformat(prev["started_at"]).timestamp()
            except Exception:
                pass
            name = name or prev.get("name")
        else:
            self.id, self.dir = self._new_dir(name)
        self.name = name
        self.backend = backend
        self.transcript_path = transcript_path
        self.paused_event = paused_event
        self.stop_event = stop_event
        self._emit = emit
        self._notify = notify or (lambda msg: None)
        self._lock = threading.Lock()
        self.mic_muted = False
        self.pauses: List[dict] = []
        self.marks: List[dict] = []
        self._pause_started: Optional[float] = None
        self.backend_ref = None      # set by main() for health display
        self.buffer_ref = None       # TranscriptBuffer, for line counts / last line
        self.summarizer = None       # LiveSummarizer, optional
        self.recorder: Optional[AudioRecorder] = None
        if record:
            self.recorder = AudioRecorder(self.dir, sample_rate)
            self.recorder.start()
        link = self.dir / "transcript.txt"
        try:
            if not link.exists():
                link.symlink_to(Path(transcript_path).resolve())
        except OSError:
            pass
        self._server: Optional[ControlServer] = None
        self._switch_to: Optional[str] = None
        self.backend_history: List[dict] = [{"backend": backend, "from": _now_hms()}]
        self.redactions: List[dict] = []
        self._discard_while_paused = False
        self.meeting_title: Optional[str] = None
        self.ledger = Ledger(self.dir / "actions.jsonl")
        self._undoing = False
        self.chunk = None  # ChunkPolicy, set by transcribe.py for chunked backends
        self.speaker_tracker = None  # SpeakerTracker, set by transcribe.py when available
        self.lang_cfg: dict = {}     # language settings, for the status bar (languages.py)
        self.new_transcript_path = None  # callable -> Path, set by transcribe.py
        self.on_new_session = None       # callable, set by transcribe.py
        self.previous_sessions: List[str] = []
        self.resumes: List[dict] = []
        self.expected_speakers: Optional[int] = None
        self._quality_warned: set = set()
        self.saved_speaker_map: dict = {}
        if prev:
            self.pauses = prev.get("pauses") or []
            self.marks = prev.get("marks") or []
            self.redactions = prev.get("redactions") or []
            self.meeting_title = prev.get("meeting_title")
            self.backend_history = (prev.get("backends") or []) + self.backend_history
            self.saved_speaker_map = prev.get("speaker_map") or {}
            self.expected_speakers = prev.get("expected_speakers")
            self.resumes = (prev.get("resumes") or []) + [
                {"at": datetime.now().isoformat(timespec="seconds"), "after_end": prev.get("ended_at")}]
            self._rec("session_resume", "system", {"backend": backend, "previous_end": prev.get("ended_at")})
        else:
            self._rec("session_start", "system", {"backend": backend, "name": name, "record": record})
        self.save()

    # ---- resume support -------------------------------------------------------
    def save_voices(self) -> None:
        """Keep the speaker voice prints so a resumed session still knows who Speaker 1 is."""
        t = self.speaker_tracker
        if t is None or not getattr(t, "centroids", None):
            return
        try:
            np.savez(str(self.dir / "speakers.npz"), centroids=np.stack(t.centroids),
                     counts=np.asarray(t.counts))
        except Exception:
            pass

    def load_voices(self) -> int:
        t, f = self.speaker_tracker, self.dir / "speakers.npz"
        if t is None or not f.exists():
            return 0
        try:
            z = np.load(str(f))
            t.centroids = [c for c in z["centroids"]]
            t.counts = [int(n) for n in z["counts"]]
            return len(t.centroids)
        except Exception:
            return 0

    # ---- actions (keyboard, socket and code all call these) -------------
    def pause(self, source: str = "code") -> bool:
        with self._lock:
            if self.paused_event.is_set():
                return False
            self.paused_event.set()
            self._pause_started = time.time()
            if self.recorder:
                self.recorder.enabled = False
        self._rec("pause", source, undo={"op": "resume"})
        self._emit("[⏸ RTT paused]")
        self._notify("Paused — nothing is transcribed or recorded.")
        self.save()
        return True

    def resume(self, source: str = "code") -> bool:
        with self._lock:
            if not self.paused_event.is_set():
                return False
            start = self._pause_started or time.time()
            self.pauses.append({"from": time.strftime("%H:%M:%S", time.localtime(start)),
                                "to": _now_hms(), "seconds": round(time.time() - start, 1)})
            self._pause_started = None
            self.paused_event.clear()
            self._discard_while_paused = False
            if self.recorder:
                self.recorder.enabled = True
        p = self.pauses[-1]
        self._rec("resume", source, {"paused_from": p["from"], "paused_to": p["to"]})
        self._emit(f"[▶ RTT resumed — paused {p['from']}–{p['to']}]")
        self._notify("Resumed.")
        self.save()
        return True

    def toggle_pause(self, source: str = "code") -> bool:
        return self.resume(source) if self.paused_event.is_set() else self.pause(source)

    def set_mic_muted(self, muted: bool, source: str = "code") -> None:
        with self._lock:
            changed = self.mic_muted != muted
            self.mic_muted = muted
        if changed:
            self._rec("mute" if muted else "unmute", source, undo={"op": "set_mic", "muted": not muted})
            self._emit("[🔇 mic muted — transcribing others only]" if muted else "[🎙 mic unmuted]")
            self._notify("Mic muted." if muted else "Mic unmuted.")
            self.save()

    def toggle_mic(self, source: str = "code") -> None:
        self.set_mic_muted(not self.mic_muted, source)

    def mark(self, note: str = "", source: str = "code") -> dict:
        m = {"at": _now_hms(), "elapsed": self.elapsed_str(), "note": note.strip(), "t": round(time.time(), 1)}
        with self._lock:
            self.marks.append(m)
        # Bracketed like every other RTT marker, so it can't be read as speech or as a person named Mark.
        line = f"[★ RTT bookmark{(': ' + m['note']) if m['note'] else ''}]"
        self._rec("mark", source, {"note": m["note"], "at": m["at"]},
                           undo={"op": "unmark", "t": m["t"], "text": line})
        self._emit(line)
        self._notify(f"Marked {m['at']}.")
        self.save()
        return m

    def stop(self, source: str = "code") -> None:
        if not self.stop_event.is_set():
            self._rec("stop", source)
        self.stop_event.set()

    # ---- backend switching (main loop performs the switch) ----------------
    BACKENDS = ("openai", "llmspeech", "mai", "vibe", "azure", "local")

    def request_switch(self, backend: str, source: str = "code") -> str:
        backend = backend.strip().lower()
        if backend not in self.BACKENDS:
            raise ValueError(f"unknown backend '{backend}'; use one of {', '.join(self.BACKENDS)}")
        self._rec("switch_backend", source, {"from": self.backend, "to": backend},
                           undo={"op": "switch", "backend": self.backend})
        self._switch_to = backend
        return backend

    def take_switch_request(self) -> Optional[str]:
        target, self._switch_to = self._switch_to, None
        return target

    def set_backend(self, backend: str) -> None:
        old = self.backend
        self.backend = backend
        self.backend_history.append({"backend": backend, "from": _now_hms()})
        self._emit(f"[⇄ backend {old} → {backend}]")
        self._notify(f"Now on {backend}.")
        self.save()

    def _rec(self, action: str, source: str, params: Optional[dict] = None, undo: Optional[dict] = None):
        """Ledger write; side effects of an undo are covered by the undo entry itself."""
        if self._undoing:
            return None
        return self.ledger.record(action, source, params, undo)

    def _new_dir(self, name: Optional[str]):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "")).strip("-")
        sid = f"{stamp}-{slug}" if slug else stamp
        d = self.sessions_root / sid
        n = 2
        while d.exists():               # two sessions in the same second
            d = self.sessions_root / f"{sid}-{n}"
            n += 1
        d.mkdir(parents=True, exist_ok=True)
        return d.name, d

    # ---- languages ------------------------------------------------------------------
    def lang(self) -> dict:
        from languages import lang_profile
        return lang_profile(self.backend, self.lang_cfg)

    # ---- new session without restarting ---------------------------------------------
    def new_session(self, name: Optional[str] = None, source: str = "code") -> dict:
        """Close this session and start a fresh one; capture, backend and settings keep running.

        Old session: pause closed, audio finished, summary finished in the background from a
        snapshot of its lines, session.json ended_at, folder named from the meeting title.
        New session: new folder, ledger, transcript file, summary, speaker voices, marks.
        """
        name = (name or "").strip() or None
        buf = self.buffer_ref
        old_lines = buf.all_lines() if buf is not None else []
        if self.paused_event.is_set():
            self.resume(source)
        self._rec("new_session", source, {"name": name})
        old_id = self.id
        # -- finish the old session
        if self.recorder:
            self.recorder.stop()
        self.save_voices()
        self.save()
        try:
            meta = json.loads((self.dir / "session.json").read_text(encoding="utf-8"))
            meta["ended_at"] = datetime.now().isoformat(timespec="seconds")
            (self.dir / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        self._rename_with_title()
        old_dir = self.dir
        if self.summarizer is not None and old_lines:
            final = self.summarizer.fork_final(old_lines, old_dir / "summary.md")
            threading.Thread(target=final.update_once, daemon=True).start()
        self.previous_sessions.append(str(old_dir))
        # -- start the new one
        with self._lock:
            self.started_at = time.time()
            self.name = name
            self.id, self.dir = self._new_dir(name)
            self.pauses, self.marks, self.redactions = [], [], []
            self.resumes = []
            self._pause_started = None
            self.backend_history = [{"backend": self.backend, "from": _now_hms()}]
            self.meeting_title = None
            self._discard_while_paused = False
        self.recorder = None
        if self.record_enabled:
            self.recorder = AudioRecorder(self.dir, self.sample_rate)
            self.recorder.start()
        if buf is not None:
            path = self.new_transcript_path() if callable(self.new_transcript_path) else None
            if path is not None and Path(path).exists():
                path = Path(str(path).replace(".txt", f"-{self.id[-6:]}.txt"))
            buf.start_new_file(path)
            self.transcript_path = path
            try:
                if path is not None:
                    (self.dir / "transcript.txt").symlink_to(Path(path).resolve())
            except OSError:
                pass
        if self.summarizer is not None:
            self.summarizer.reset(self.dir / "summary.md")
        if self.speaker_tracker is not None:
            self.speaker_tracker.reset()
        self.ledger = Ledger(self.dir / "actions.jsonl")
        self._rec("session_start", source, {"backend": self.backend, "name": name,
                                            "record": self.record_enabled, "previous": old_id})
        self._emit(f"[✦ RTT new session {self.id} — previous: {old_id}]")
        self._notify(f"New session {self.id}. Previous saved in {old_dir}.")
        self.save()
        if callable(self.on_new_session):
            self.on_new_session()
        return {"id": self.id, "dir": str(self.dir), "previous": str(old_dir)}

    # ---- chunk window -------------------------------------------------------------
    def set_chunk(self, value: str, source: str = "code") -> dict:
        """value: seconds ("20"), "+"/"-" (next/previous step), "auto" or "fixed"."""
        pol = self.chunk
        if pol is None:
            raise ValueError("Chunk size applies to chunked backends only (openai, llmspeech, mai, vibe).")
        before = {"seconds": pol.seconds, "mode": pol.mode}
        v = value.strip().lower()
        if v in ("+", "up", "bigger"):
            pol.step(+1)
        elif v in ("-", "down", "smaller"):
            pol.step(-1)
        elif v in ("auto", "fixed"):
            pol.set_mode(v)
        elif v == "toggle":
            pol.set_mode("fixed" if pol.mode == "auto" else "auto")
        else:
            pol.set(float(v))
        after = {"seconds": pol.seconds, "mode": pol.mode}
        if after != before:
            self._rec("chunk", source, {"from": before, "to": after}, undo={"op": "chunk", **before})
        self.save()
        return pol.snapshot()

    # ---- tidy: remove status-line bursts ------------------------------------------
    def tidy(self, source: str = "code") -> dict:
        """Remove pause/resume/mute/bookmark lines that came in bursts (keys fired by accident),
        and the bookmarks and zero-length pauses behind them. Speech is never touched. Undoable."""
        buf = self.buffer_ref
        if buf is None:
            return {"ok": False, "error": "no transcript"}
        lines = buf.raw_lines()
        idx = burst_marker_indices(lines)
        if not idx:
            return {"ok": True, "removed": 0}
        removed = buf.remove_indices(idx)
        stamps = {lines[i][1:9] for i in idx}
        with self._lock:
            marks_before, pauses_before = list(self.marks), list(self.pauses)
            self.marks = [m for m in self.marks if m.get("at") not in stamps]
            self.pauses = [p for p in self.pauses if not (p.get("seconds", 1) < 1 and p.get("from") in stamps)]
        self._rec("tidy", source, {"lines": len(removed), "marks": len(marks_before) - len(self.marks)},
                  undo={"op": "tidy", "lines": [[i, ln] for i, ln in removed],
                        "marks": marks_before, "pauses": pauses_before})
        self._notify(f"Tidied: removed {len(removed)} status lines that came in bursts.")
        self.save()
        return {"ok": True, "removed": len(removed)}

    # ---- transcription quality ---------------------------------------------------
    def set_expected_speakers(self, n: Optional[int], source: str = "code") -> dict:
        """How many people are in the call (None/0 = unknown). Stops one noisy voice
        from being split into Speaker 3, 4, 5…; already-split labels can be merged by
        renaming them to the same name."""
        n = int(n) if n else None
        if n is not None and not 1 <= n <= 20:
            raise ValueError("expected speakers must be 1–20 (0 = unknown)")
        t = self.speaker_tracker
        before = getattr(t, "max_speakers", None) if t is not None else self.expected_speakers
        self.expected_speakers = n
        if t is not None:
            t.set_max_speakers(n)
        self._rec("expected_speakers", source, {"from": before, "to": n})
        self._notify(f"Expecting {n} people — new voices join the closest known one." if n
                     else "Expected people: unknown (new voices allowed).")
        self.save()
        return {"expected_speakers": n}

    def quality(self, window: int = 60) -> dict:
        """Live signs of a bad transcription, from the recent lines and the voice tracker."""
        import re
        buf = self.buffer_ref
        lines = buf.tail_lines(window) if buf is not None else []
        speech = [m.group(2) for m in (re.match(r"\[[^\]]*\] Speaker ([^:]+): (.*)", ln) for ln in lines) if m]
        labels = [re.match(r"\[[^\]]*\] Speaker ([^:]+):", ln).group(1) for ln in lines
                  if re.match(r"\[[^\]]*\] Speaker ([^:]+):", ln)]
        n = len(speech)
        t = self.speaker_tracker
        voices = len(getattr(t, "centroids", []) or []) if t is not None else len(set(labels) - {"?"})
        q = {"lines": n, "voices": voices, "expected": self.expected_speakers,
             "short_pct": round(100 * sum(1 for x in speech if len(x.split()) <= 2) / n) if n else 0,
             "unknown_pct": round(100 * labels.count("?") / n) if n else 0}
        warn = []
        if n >= 20:
            if self.expected_speakers is None and voices >= 4:
                warn.append(f"{voices} voices — how many people? press k")
            if q["short_pct"] >= 40:
                warn.append(f"choppy ({q['short_pct']}% 1–2 word lines) — try ] bigger chunk")
            if q["unknown_pct"] >= 12:
                warn.append(f"{q['unknown_pct']}% unknown speaker")
        q["warnings"] = warn
        return q

    # ---- names from the meeting app's transcript ------------------------------
    def match_meeting_names(self, raw: str, use_ai: bool = True, ai_call=None, refine: bool = False) -> dict:
        """Preview from a pasted Teams/Zoom transcript (meetalign.align): names, and with
        refine=True (`T`) the app's wording + learnable words. Nothing is applied here."""
        from meetalign import align
        buf = self.buffer_ref
        if buf is None:
            return {"ok": False, "error": "no transcript"}
        resolver = (ai_call or "copilot") if use_ai else None
        r = align(buf.raw_lines(), raw, refine=refine, resolver=resolver)
        if not r.turns:
            return {"ok": False, "error": "no named turns found in the paste (copy the Teams/Zoom transcript panel)"}
        return {"ok": True, "summary": r.summary(), "changes": r.changes, "label_names": r.label_names,
                "lines_before": r.lines_before, "ai_filled": r.ai_filled, "ai_error": r.ai_error,
                "names": r.names, "turns": len(r.turns), "refined": len(r.fixes),
                "learn": r.learn, "action": "meeting_refine" if refine else "meeting_names"}

    def apply_meeting_names(self, preview: dict, source: str = "code", learn: bool = False) -> dict:
        buf = self.buffer_ref
        if buf is None or not preview.get("ok"):
            return {"ok": False, "error": preview.get("error", "nothing to apply")}
        current = buf.raw_lines()
        changes = {i: t for i, t in preview["changes"].items()
                   if i < len(current) and current[i] == preview["lines_before"][i]}
        if not changes:
            return {"ok": False, "error": "the transcript changed since the preview; press t again"}
        map_before = buf.speaker_map()
        old = buf.replace_lines(changes)
        labels = dict(preview["label_names"])   # later lines from these voices get the name too
        new_map = dict(map_before)
        new_map.update(labels)
        buf.set_speaker_map(new_map)
        learned = []
        if learn and preview.get("learn"):
            import corrections
            learned = corrections.learn(preview["learn"], source=f"session {self.id}")
            backend = self.backend_ref
            if backend is not None and hasattr(backend, "_corrections"):
                backend._corrections = corrections.Corrections.load()   # new lines use it at once
        self._rec(preview.get("action", "meeting_names"), source,
                  {"lines": len(old), "labels": labels, "summary": preview["summary"],
                   "learned": [f"{x['wrong']} → {x['right']}" for x in learned]},
                  undo={"op": "meeting_names", "old": {str(i): t for i, t in old.items()},
                        "new": {str(i): changes[i] for i in old}, "map": map_before,
                        "labels": labels})
        if self.summarizer is not None:
            for lab, name in labels.items():
                self.summarizer.apply_rename(lab, name)
        extra = f" · learned {len(learned)} word(s)" if learned else ""
        self._notify(f"Applied: {preview['summary']}{extra}")
        self.save()
        return {"ok": True, "lines": len(old), "labels": labels, "learned": learned}

    # ---- speaker names ---------------------------------------------------------
    def rename_speaker(self, src: str, dst: str, source: str = "code") -> int:
        buf = self.buffer_ref
        if buf is None:
            return 0
        if src.strip() == "?":
            self._notify("“?” means the voice was too short to identify; rename a numbered speaker instead.")
            return 0
        before = buf.speaker_map()
        is_display_name = src in before.values()
        n = buf.rename_speaker(src, dst)
        self._rec("rename_speaker", source, {"from": src, "to": dst, "lines": n},
                           undo={"op": "speaker_map", "map": before})
        if self.summarizer is not None:
            self.summarizer.apply_rename(src, dst, bare=is_display_name)
        self.save()
        return n

    # ---- undo ---------------------------------------------------------------------
    UNDO_LABELS = {"pause": "pause", "mute": "mute", "unmute": "unmute", "switch_backend": "backend switch",
                   "chunk": "chunk change", "meeting_names": "names from Teams/Zoom",
                   "meeting_refine": "text + names from Teams/Zoom"}

    def undo_preview(self) -> Optional[str]:
        """Short text of what undo would do now (None = nothing to undo)."""
        e = self.ledger.last_undoable(self._undo_applicable)
        if e is None:
            return None
        a, prm = e["action"], e.get("params", {})
        if a == "rename_speaker":
            return f"rename {prm.get('from')} → {prm.get('to')}"
        if a == "mark":
            return f"mark {prm.get('at', '')}".strip()
        if a == "switch_backend":
            return f"backend {prm.get('from')} → {prm.get('to')}"
        return self.UNDO_LABELS.get(a, a.replace("_", " "))

    def undo(self, entry_id: Optional[int] = None, source: str = "code") -> dict:
        e = self.ledger.get(entry_id) if entry_id else self.ledger.last_undoable(self._undo_applicable)
        if e is None or "undo" not in e:
            return {"ok": False, "error": "nothing to undo" if entry_id is None else f"entry {entry_id} is not undoable"}
        if e["id"] <= self.ledger.loaded_upto:
            return {"ok": False, "error": f"entry {e['id']} is from before the session was resumed; it can't be undone"}
        if e["id"] in self.ledger.reverted_ids():
            return {"ok": False, "error": f"entry {e['id']} was already undone"}
        u = e["undo"]
        op = u["op"]
        self._undoing = True
        try:
            result = self._apply_undo(u, op, source)
        finally:
            self._undoing = False
        if result is not None:
            return result
        self._rec("undo", source, {"target": e["id"], "action": e["action"]})
        self._notify(f"Undid #{e['id']} {e['action']}.")
        self.save()
        return {"ok": True, "undone": e}

    def _undo_applicable(self, e: dict) -> bool:
        """Skip actions whose effect is already gone (a pause that was resumed, a mute already undone)."""
        u = e["undo"]
        if u["op"] == "resume":
            return self.paused_event.is_set()
        if u["op"] == "set_mic":
            return self.mic_muted != u["muted"]
        if u["op"] == "switch":
            return self.backend != u["backend"]
        return True

    def _apply_undo(self, u: dict, op: str, source: str) -> Optional[dict]:
        if op == "resume":
            self.resume(source)
        elif op == "set_mic":
            self.set_mic_muted(u["muted"], source)
        elif op == "switch":
            self._switch_to = u["backend"]
        elif op == "unmark":
            with self._lock:
                self.marks = [m for m in self.marks if m.get("t") != u["t"]]
            if self.buffer_ref is not None:
                self.buffer_ref.remove_lines_ending_with(u["text"])
        elif op == "tidy":
            if self.buffer_ref is not None:
                self.buffer_ref.insert_lines([(int(i), ln) for i, ln in u["lines"]])
            with self._lock:
                self.marks, self.pauses = u["marks"], u["pauses"]
        elif op in ("meeting_names", "meeting_refine"):
            if self.buffer_ref is not None:
                cur = self.buffer_ref.raw_lines()
                back = {int(i): t for i, t in u["old"].items()
                        if int(i) < len(cur) and cur[int(i)] == u["new"][i]}
                self.buffer_ref.replace_lines(back)
                m = self.buffer_ref.speaker_map()
                for lab in u.get("labels", {}):            # only what this action set; later renames stay
                    if lab in u["map"]:
                        m[lab] = u["map"][lab]
                    else:
                        m.pop(lab, None)
                self.buffer_ref.set_speaker_map(m)
                self._notify(f"Names undone: {len(back)} of {len(u['old'])} lines restored.")
            if self.summarizer is not None:
                self.summarizer.rebuild()
        elif op == "chunk":
            if self.chunk is not None:
                self.chunk.mode = u["mode"]
                self.chunk.set(u["seconds"])
        elif op == "speaker_map":
            if self.buffer_ref is not None:
                self.buffer_ref.set_speaker_map(u["map"])
            if self.summarizer is not None:
                self.summarizer.rebuild()  # names inside the summary: rebuild from the corrected text
        else:
            return {"ok": False, "error": f"unknown undo op {op}"}
        return None

    # ---- off the record ------------------------------------------------------
    @staticmethod
    def parse_off_record(raw: str, now: Optional[float] = None) -> dict:
        """Turn what the user typed or pasted into off_the_record() kwargs.

        Accepts minutes ("", "2", "2m", "90s") or a transcript time ("09:15:09",
        "9:15", "[09:15:09]", or a whole pasted line "[09:15:09] Speaker 1: ...").
        A time means: delete from that line on. A time later than now is taken as
        yesterday (meeting crossed midnight). Raises ValueError on anything else."""
        import re
        now = time.time() if now is None else now
        s = (raw or "").strip()
        if not s:
            return {"minutes": 2.0}
        m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", s)
        if m:
            h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            if h > 23 or mi > 59 or se > 59:
                raise ValueError(f"not a valid time: {m.group(0)}")
            base = datetime.fromtimestamp(now).replace(hour=h, minute=mi, second=se, microsecond=0)
            ts = base.timestamp()
            if ts > now + 5:
                ts -= 86400
            return {"since": ts}
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins)?", s.lower())
        if m:
            v = float(m.group(1))
            return {"minutes": v / 60 if (m.group(2) or "m").startswith("s") else v}
        raise ValueError("enter minutes (e.g. 2) or a line's time (e.g. 09:15:09)")

    def accept_line(self, text: str) -> bool:
        """emit() filter: after off-the-record, drop speech that was already in flight.

        Chunked backends return text up to a chunk behind the audio. Once the user goes
        off the record we are paused, so any speech line arriving now belongs to audio
        from the deleted window. Session markers (starting with '[' or '★') still pass."""
        if self._discard_while_paused and self.paused_event.is_set():
            return text.startswith("[") or text.startswith("★")
        return True

    def off_the_record(self, minutes: float = 2.0, source: str = "code",
                       since: Optional[float] = None) -> dict:
        """Pause, then delete transcript, summary input and audio from a point on.

        The point is either the last `minutes`, or `since` (epoch seconds, e.g. the
        spoken time of a transcript line) — that line and everything after it go."""
        if since is not None:
            cutoff = float(since)
            minutes = max(0.0, (time.time() - cutoff) / 60)
        else:
            minutes = max(0.0, float(minutes))
            cutoff = time.time() - minutes * 60
        self.pause(source)
        self._discard_while_paused = True
        backend = self.backend_ref
        dropped_audio = 0
        if backend is not None and hasattr(backend, "_buffer"):
            try:
                dropped_audio = int(backend._buffer.size)
                backend._buffer = backend._buffer[:0]
            except Exception:
                pass
        with self._lock:
            self.marks = [m for m in self.marks if m.get("t", 0) < cutoff]
        removed = 0
        if self.buffer_ref is not None:
            removed = self.buffer_ref.redact_since(cutoff)
        audio_s = 0.0
        if self.recorder is not None:
            audio_s = self.recorder.redact_since(cutoff)
        if self.summarizer is not None:
            self.summarizer.rebuild()
        r = {"at": _now_hms(), "minutes": minutes, "lines_removed": removed,
             "audio_seconds_removed": round(audio_s, 1)}
        self.redactions.append(r)
        # Not undoable: the text and audio are gone by design.
        self._rec("off_the_record", source, dict(r))
        if since is not None:
            r["since"] = time.strftime("%H:%M:%S", time.localtime(cutoff))
            what = f"everything from {r['since']}"
        else:
            what = f"the last {minutes:g} min"
        self._emit(f"[✂ off the record — removed {what} ({removed} lines). Paused.]")
        self._notify(f"Removed {what}. Paused — press space to resume.")
        self.save()
        return r

    # ---- status ----------------------------------------------------------
    def active_seconds(self) -> float:
        """Time since the first start, minus the gaps while the session was stopped (resume)."""
        gap = 0.0
        for r in getattr(self, "resumes", []) or []:
            try:
                gap += max(0.0, (datetime.fromisoformat(r["at"]) - datetime.fromisoformat(r["after_end"])).total_seconds())
            except (KeyError, TypeError, ValueError):
                pass
        return max(0.0, time.time() - self.started_at - gap)

    def elapsed_str(self) -> str:
        s = int(self.active_seconds())
        return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    def status(self) -> dict:
        backend = self.backend_ref
        st = {
            "id": self.id, "pid": os.getpid(), "backend": self.backend,
            "chunk": self.chunk.snapshot() if self.chunk is not None else None,
            "lang": self.lang(),
            "speakers": self.speaker_tracker.snapshot() if self.speaker_tracker is not None else None,
            "chunk_label": self.chunk.label() if self.chunk is not None else None,
            "elapsed": self.elapsed_str(),
            "state": "paused" if self.paused_event.is_set() else "live",
            "mic_muted": self.mic_muted,
            "recording": bool(self.recorder and self.recorder.enabled and not self.paused_event.is_set()),
            "record_enabled": self.recorder is not None,
            "marks": len(self.marks),
            "meeting_title": self.meeting_title,
            "redactions": len(self.redactions),
            "transcript": str(Path(self.transcript_path).resolve()),
            "session_dir": str(self.dir.resolve()),
            "backend_healthy": not getattr(backend, "_unhealthy", False),
        }
        buf = self.buffer_ref
        if buf is not None:
            st["lines"] = buf.line_count()
            age = buf.seconds_since_last()
            st["last_text_age_s"] = None if age is None else round(age)
        if self.recorder:
            st["audio_seconds"] = {k: round(v) for k, v in self.recorder.seconds.items()}
            st["audio_dropped_blocks"] = self.recorder.dropped_blocks
        if self.summarizer is not None:
            st["summary"] = self.summarizer.status()
        return st

    def save(self) -> None:
        data = {
            "id": self.id, "name": self.name, "backend": self.backend, "pid": os.getpid(),
            "chunk": self.chunk.snapshot() if self.chunk is not None else None,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "transcript": str(Path(self.transcript_path).resolve()),
            "meeting_title": self.meeting_title,
            "pauses": self.pauses, "marks": self.marks, "mic_muted": self.mic_muted,
            "backends": self.backend_history, "redactions": self.redactions,
            "audio": self.recorder.paths() if self.recorder else [],
            "actions_ledger": str(self.dir / "actions.jsonl"),
            "resumes": getattr(self, "resumes", []),
            "expected_speakers": getattr(self, "expected_speakers", None),
            "speaker_map": self.buffer_ref.speaker_map() if self.buffer_ref is not None else {},
        }
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / "session.json")
        self.save_raw_lines()
        if self.recorder is not None:
            self.recorder.save_index()

    def save_raw_lines(self) -> None:
        """Lines with raw speaker labels + times, so resume never has to reverse renames."""
        buf = self.buffer_ref
        if buf is None or not hasattr(buf, "raw_snapshot"):
            return
        try:
            tmp = self.dir / "lines.json.tmp"
            tmp.write_text(json.dumps(buf.raw_snapshot(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.dir / "lines.json")
        except OSError:
            pass

    # ---- lifecycle ---------------------------------------------------------
    def start_control_server(self) -> Optional[str]:
        try:
            self._server = ControlServer(self)
            self._server.start()
            return str(SOCKET_PATH)
        except OSError as exc:
            self._notify(f"Control socket unavailable: {exc}")
            return None

    def close_fast(self) -> None:
        """Force-quit path: release the control socket and finish audio files, nothing slow."""
        self.save_raw_lines()
        if self._server:
            try:
                self._server.stop()
            except Exception:
                pass
        if self.recorder:
            try:
                self.recorder.stop()
            except Exception:
                pass

    def close(self) -> None:
        if self._pause_started is not None:
            self.pauses.append({"from": time.strftime("%H:%M:%S", time.localtime(self._pause_started)),
                                "to": _now_hms(), "seconds": round(time.time() - self._pause_started, 1)})
        if self.recorder:
            self.recorder.stop()
        if self.summarizer is not None:
            self.summarizer.stop()
        data_end = datetime.now().isoformat(timespec="seconds")
        self.save_voices()
        self.save()
        try:
            meta = json.loads((self.dir / "session.json").read_text(encoding="utf-8"))
            meta["ended_at"] = data_end
            (self.dir / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        if self._server:
            self._server.stop()
        self._rename_with_title()

    def set_meeting_title(self, title: Optional[str]) -> None:
        if title:
            self.meeting_title = title.strip()[:120]
            self._notify(f"Meeting: {self.meeting_title}")
            self.save()

    def _rename_with_title(self) -> None:
        """Give the folder a readable name at the end (renaming mid-session would move open audio files)."""
        if self.name or not self.meeting_title:
            return
        slug = "".join(c if c.isalnum() else "-" for c in self.meeting_title).strip("-")
        slug = "-".join(p for p in slug.split("-") if p)[:60]
        import re
        if not slug or self.dir.name.endswith(slug):   # already named (resumed session)
            return
        m = re.match(r"\d{8}-\d{6}(-\d+)?", self.dir.name)   # drop an older title slug
        base = m.group(0) if m else self.dir.name
        target = self.dir.with_name(f"{base}-{slug}")
        try:
            self.dir.rename(target)
            self.dir = target
        except OSError:
            pass


class ControlServer(threading.Thread):
    """Unix socket, one JSON command per line: {"cmd": "pause"} -> {"ok": true, ...}."""

    COMMANDS = ("status", "pause", "resume", "toggle", "mute", "unmute", "mark", "stop", "summarize",
                "offrecord", "speakers", "quality", "names", "refine", "tidy", "switch", "rename", "undo", "log", "chunk", "new")

    def __init__(self, session: Session) -> None:
        super().__init__(daemon=True)
        self.session = session
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            # Refuse to hijack a live session; clean up a stale socket.
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.5)
                probe.connect(str(SOCKET_PATH))
                probe.close()
                raise OSError("another RTT session already owns the control socket")
            except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
                SOCKET_PATH.unlink(missing_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(SOCKET_PATH))
        os.chmod(str(SOCKET_PATH), 0o600)
        self._sock.listen(4)
        self._sock.settimeout(0.5)
        self._running = True

    def handle(self, req: dict) -> dict:
        s = self.session
        cmd = req.get("cmd")
        if cmd == "status":
            return {"ok": True, "status": s.status()}
        if cmd == "pause":
            return {"ok": True, "changed": s.pause("ctl")}
        if cmd == "resume":
            return {"ok": True, "changed": s.resume("ctl")}
        if cmd == "toggle":
            s.toggle_pause("ctl")
            return {"ok": True, "state": s.status()["state"]}
        if cmd in ("mute", "unmute"):
            s.set_mic_muted(cmd == "mute", "ctl")
            return {"ok": True}
        if cmd == "mark":
            return {"ok": True, "mark": s.mark(str(req.get("note", "")), "ctl")}
        if cmd == "rename":
            return {"ok": True, "lines": s.rename_speaker(str(req.get("from", "")), str(req.get("to", "")), "ctl")}
        if cmd == "undo":
            tid = req.get("id")
            return s.undo(int(tid) if tid else None, "ctl")
        if cmd == "new":
            return {"ok": True, "session": s.new_session(str(req.get("name", "")), "ctl")}
        if cmd == "chunk":
            try:
                return {"ok": True, "chunk": s.set_chunk(str(req.get("value", "")), "ctl")}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        if cmd == "log":
            return {"ok": True, "entries": s.ledger.entries, "reverted": sorted(s.ledger.reverted_ids() - {None})}
        if cmd == "summarize":
            if s.summarizer is None:
                return {"ok": False, "error": "live summary is off (start with --summary)"}
            s.summarizer.request_now()
            return {"ok": True}
        if cmd == "speakers":
            try:
                return {"ok": True, **s.set_expected_speakers(int(req.get("n") or 0), "ctl")}
            except (TypeError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}
        if cmd in ("names", "refine"):
            prev = s.match_meeting_names(str(req.get("text", "")), use_ai=bool(req.get("ai", True)),
                                         refine=cmd == "refine")
            if not prev.get("ok") or req.get("dry_run"):
                return {k: v for k, v in prev.items() if k not in ("changes", "lines_before")}
            return {**s.apply_meeting_names(prev, "ctl", learn=bool(req.get("learn"))), "summary": prev["summary"]}
        if cmd == "tidy":
            return s.tidy("ctl")
        if cmd == "quality":
            return {"ok": True, "quality": s.quality()}
        if cmd == "offrecord":
            try:
                kw = (s.parse_off_record(str(req["at"])) if req.get("at") is not None
                      else {"minutes": float(req.get("minutes", 2))})
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "redaction": s.off_the_record(source="ctl", **kw)}
        if cmd == "switch":
            try:
                return {"ok": True, "switching_to": s.request_switch(str(req.get("backend", "")), "ctl")}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        if cmd == "stop":
            s.stop("ctl")
            return {"ok": True}
        return {"ok": False, "error": f"unknown command; use one of {', '.join(self.COMMANDS)}"}

    def run(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2)
                try:
                    raw = b""
                    while not raw.endswith(b"\n") and len(raw) < 65536:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                    resp = self.handle(json.loads(raw.decode("utf-8") or "{}"))
                except Exception as exc:  # malformed request must not kill the server
                    resp = {"ok": False, "error": repr(exc)[:200]}
                try:
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def stop(self) -> None:
        self._running = False
        try:
            self._sock.close()
        finally:
            SOCKET_PATH.unlink(missing_ok=True)


def send_command(cmd: dict, timeout: float = 3.0) -> dict:
    """Client helper used by rtt_ctl.py."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCKET_PATH))
    s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
    raw = b""
    while not raw.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        raw += chunk
    s.close()
    return json.loads(raw.decode("utf-8"))
