#!/usr/bin/env python3
"""Real-time Teams transcription MVP with local Whisper and optional Azure backends."""
from __future__ import annotations

import os
import platform
import queue
import select
import shutil
import signal
import subprocess
import sys
import threading
import json
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple, Union, TYPE_CHECKING

from datetime import datetime

import numpy as np
import sounddevice as sd
import soundfile as sf
import typer
import av
from av.audio.resampler import AudioResampler
from faster_whisper import WhisperModel

if TYPE_CHECKING:  # pragma: no cover - typing aid
    import azure.cognitiveservices.speech as speechsdk  # type: ignore
else:  # pragma: no cover - runtime import
    try:  # Optional dependency for Azure backend
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        speechsdk = None  # type: ignore

try:
    from azure.identity import DefaultAzureCredential
except ImportError:
    DefaultAzureCredential = None  # type: ignore

try:
    from azure.identity import AzureCliCredential
except ImportError:
    AzureCliCredential = None  # type: ignore

from chunked_backends import (
    OpenAITranscribeBackend,
    LLMSpeechBackend,
    MAITranscribeBackend,
    VibeVoiceLocalBackend,
    process_chunked_backend,
)

app = typer.Typer(add_completion=False, help="Stream live audio into local or Azure speech recognizers.")


def _build_aad_credential():
    """Return the AAD credential used to mint cognitiveservices tokens.

    When RTT_AAD_SUBSCRIPTION is set, pin token minting to that subscription via
    AzureCliCredential(subscription=...). This is LOAD-BEARING: if the Azure
    resource lives in one tenant but the `az` CLI active context drifts to a
    subscription in another tenant, an unpinned DefaultAzureCredential mints a
    token for the wrong tenant, and the resource rejects it with HTTP 500 "Unable to get resource
    information" — a cross-tenant rejection that masquerades as a server error,
    NOT a 401. The resource has disableLocalAuth=true, so API keys are not an
    option. Pinning the SUBSCRIPTION (not just tenant_id — that still follows the
    active account and fails) makes RTT immune to `az` drift: no need to switch
    `az` context before every launch. Proven: with `az` active on another tenant,
    an unpinned mint returns 500 while the pinned subscription returns 200.

    Falls back to DefaultAzureCredential when the var is unset or AzureCliCredential
    is unavailable, preserving prior behavior.
    """
    sub = os.environ.get("RTT_AAD_SUBSCRIPTION", "").strip()
    if sub and AzureCliCredential is not None:
        typer.echo(f"Pinning AAD token to subscription {sub[:8]}… (drift-proof)")
        return AzureCliCredential(subscription=sub)
    if DefaultAzureCredential is not None:
        return DefaultAzureCredential()
    raise RuntimeError("azure-identity is not installed.")


def ensure_vibe_server(url: str, wait_s: int = 180) -> None:
    """Start the local VibeVoice server (speech-bench/vibe_server.py) if it isn't running."""
    import requests as _rq
    health = url.rstrip("/") + "/health"
    try:
        _rq.get(health, timeout=3).raise_for_status()
        return
    except Exception:
        pass
    bench = Path(os.environ.get("RTT_VIBE_HOME", str(Path(__file__).resolve().parent.parent / "speech-bench")))
    py = bench / ".venv-vv/bin/python"
    if not py.exists():
        raise RuntimeError(f"VibeVoice env not found at {py}")
    typer.echo("Starting local VibeVoice server (first load ~10 s)…")
    log = open(bench / "vibe_server.log", "ab")
    subprocess.Popen([str(py), "vibe_server.py"], cwd=str(bench), stdout=log, stderr=log,
                     start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            _rq.get(health, timeout=2).raise_for_status()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("VibeVoice server did not become ready")

def _speaker_tracking_reason() -> str:
    from speakers import SpeakerTracker
    t = SpeakerTracker()
    t.load()
    return t.error or "unavailable"


def stop_vibe_server(url: str, wait_s: float = 8.0) -> bool:
    """Terminate the process listening on the VibeVoice port (only if it is vibe_server.py)."""
    from urllib.parse import urlsplit
    port = urlsplit(url).port or 8765
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
    except Exception:
        return False
    stopped = False
    for pid_s in out:
        pid = int(pid_s)
        try:
            cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout
        except Exception:
            continue
        if "vibe_server.py" not in cmd:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + wait_s
            while time.time() < deadline:
                os.kill(pid, 0)
                time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stopped = True
    return stopped


BACKEND_LOCAL = "local"
BACKEND_AZURE = "azure"
BACKEND_OPENAI = "openai"        # Azure OpenAI gpt-4o-transcribe-diarize (chunked)
BACKEND_LLMSPEECH = "llmspeech"  # Azure Speech LLM Speech enhancedMode (chunked)
BACKEND_VIBE = "vibe"            # Local VibeVoice-ASR 7B via speech-bench/vibe_server.py (chunked, no cloud)
BACKEND_MAI = "mai"              # Azure Speech MAI-Transcribe-2 enhancedMode + diarization (chunked)


def resume_hint(session, buffer) -> str:
    """What to print on exit so the session can be picked up again (like `hermes --resume`)."""
    title = session.meeting_title or session.name
    secs = int(session.active_seconds())
    lines = buffer.line_count() if buffer is not None else 0
    out = ["", "Resume this session with:", f"  rtt --resume {session.dir.name}"]
    if title:
        out.append(f'  rtt -c "{title}"')
    out.append("  rtt -c                (latest session)")
    out += ["", f"Session:        {session.dir.name}"]
    if title:
        out.append(f"Title:          {title}")
    out.append(f"Duration:       {secs // 3600}h {secs % 3600 // 60:02d}m {secs % 60:02d}s")
    out.append(f"Lines:          {lines}" + (f" ({len(session.marks)} marks)" if session.marks else ""))
    return "\n".join(out)


def _normalize_resume_argv(argv: List[str]) -> List[str]:
    """Let `--resume` and `-c` be used without a value (= latest session)."""
    out = list(argv)
    i = 0
    while i < len(out):
        a = out[i]
        if a in ("--resume", "-c", "--continue"):
            nxt = out[i + 1] if i + 1 < len(out) else None
            if nxt is None or nxt.startswith("-"):
                out.insert(i + 1, "")
            i += 2
            continue
        i += 1
    return out


def default_transcript_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("transcripts") / f"transcript-{timestamp}.txt"


class TranscriptBuffer:
    def __init__(self, file_path: Optional[Path]) -> None:
        self._lines: List[str] = []
        self._file_path = file_path
        self._lock = threading.Lock()
        # Index into `_lines` used for Ctrl+E delta copies (since last Ctrl+S).
        # Starts at 0 so Ctrl+E before the first Ctrl+S returns the full transcript.
        self._delta_baseline: int = 0
        # Speaker rename map: "Guest-1" -> "Hawk Ticehurst". Applied at write/render time.
        self._speaker_map: dict = {}
        self._last_add: Optional[float] = None
        self._times: List[float] = []  # wall-clock time each line was added (for redaction)
        if file_path:
            file_path.parent.mkdir(parents=True, exist_ok=True)

    def _apply_speaker_map(self, line: str) -> str:
        if not self._speaker_map:
            return line
        out = line
        for src, dst in self._speaker_map.items():
            # Match "Speaker Guest-2:" or "Speaker Guest-2 " patterns.
            out = out.replace(f"Speaker {src}:", f"Speaker {dst}:")
            out = out.replace(f"Speaker {src} ", f"Speaker {dst} ")
        return out

    def add(self, line: str, t: Optional[float] = None) -> None:
        with self._lock:
            self._lines.append(line)
            self._last_add = time.time()
            self._times.append(t if t is not None else self._last_add)
            if self._file_path:
                with self._file_path.open("a", encoding="utf-8") as handle:
                    handle.write(self._apply_speaker_map(line) + "\n")

    def known_speakers(self) -> List[str]:
        """Return distinct speaker labels seen so far (e.g. ['Guest-1', 'Guest-2'])."""
        speakers: List[str] = []
        seen = set()
        with self._lock:
            for ln in self._lines:
                # Format: "[HH:MM:SS] Speaker XXX: text"
                idx = ln.find("Speaker ")
                if idx < 0:
                    continue
                tail = ln[idx + len("Speaker "):]
                colon = tail.find(":")
                if colon < 0:
                    continue
                name = tail[:colon].strip()
                if name and name not in seen:
                    seen.add(name)
                    speakers.append(name)
        return speakers

    def rename_speaker(self, src: str, dst: str) -> int:
        """Map src->dst going forward AND rewrite the file with substitutions applied.
        Returns the number of past lines updated."""
        if not src or not dst:
            return 0
        with self._lock:
            # Clicking a name that is itself a rename target ("Amit") re-points the original label(s).
            originals = [k for k, v in self._speaker_map.items() if v == src]
            if originals:
                for k in originals:
                    self._speaker_map[k] = dst
                if any(f"Speaker {src}:" in ln for ln in self._lines):
                    self._speaker_map[src] = dst   # lines written with the name itself (meeting names)
                src = originals[0]
            self._speaker_map[src] = dst
            updated = 0
            for ln in self._lines:
                if f"Speaker {src}:" in ln or f"Speaker {src} " in ln:
                    updated += 1
            if self._file_path and self._file_path.exists():
                rendered = [self._apply_speaker_map(ln) for ln in self._lines]
                # Rewrite atomically.
                tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
                tmp.write_text("\n".join(rendered) + ("\n" if rendered else ""), encoding="utf-8")
                tmp.replace(self._file_path)
            return updated

    def speaker_map(self) -> dict:
        with self._lock:
            return dict(self._speaker_map)

    def set_speaker_map(self, mapping: dict) -> None:
        """Replace the whole rename map (used by undo) and rewrite the file."""
        with self._lock:
            self._speaker_map = dict(mapping)
            self._rewrite_locked()

    def remove_lines_ending_with(self, text: str) -> int:
        with self._lock:
            keep = [i for i, ln in enumerate(self._lines) if not ln.endswith(text)]
            removed = len(self._lines) - len(keep)
            if removed:
                self._lines = [self._lines[i] for i in keep]
                self._times = [self._times[i] for i in keep]
                self._delta_baseline = min(self._delta_baseline, len(self._lines))
                self._rewrite_locked()
            return removed

    def raw_snapshot(self) -> List[list]:
        with self._lock:
            return [[round(t, 3), ln] for t, ln in zip(self._times, self._lines)]

    def load_raw(self, rows: List[list], speaker_map: Optional[dict] = None) -> int:
        """Resume from saved raw lines (exact labels and times)."""
        with self._lock:
            self._times = [float(t) for t, _ in rows]
            self._lines = [str(ln) for _, ln in rows]
            self._speaker_map = dict(speaker_map or {})
            self._delta_baseline = len(self._lines)
            self._last_add = time.time()
        return len(self._lines)

    def remove_indices(self, indices) -> List[tuple]:
        """Remove lines by index; returns [(index, raw line)] for undo."""
        drop = set(indices)
        with self._lock:
            removed = [(i, self._lines[i]) for i in sorted(drop) if 0 <= i < len(self._lines)]
            keep = [i for i in range(len(self._lines)) if i not in drop]
            self._lines = [self._lines[i] for i in keep]
            self._times = [self._times[i] for i in keep]
            self._delta_baseline = min(self._delta_baseline, len(self._lines))
            self._rewrite_locked()
        return removed

    def insert_lines(self, items) -> None:
        """Put removed lines back at their old positions (ascending index order)."""
        with self._lock:
            for i, ln in sorted(items):
                i = min(i, len(self._lines))
                t = self._times[i - 1] if i > 0 and self._times else time.time()
                self._lines.insert(i, ln)
                self._times.insert(i, t)
            self._rewrite_locked()

    def raw_lines(self) -> List[str]:
        with self._lock:
            return list(self._lines)

    def replace_lines(self, changes: dict) -> dict:
        """{index: new raw text} → applied where the line is unchanged; returns {index: old text}."""
        old = {}
        with self._lock:
            for i, new in changes.items():
                if 0 <= i < len(self._lines):
                    old[i] = self._lines[i]
                    self._lines[i] = new
            if old:
                self._rewrite_locked()
        return old

    def _rewrite_locked(self) -> None:
        if self._file_path:
            rendered = [self._apply_speaker_map(ln) for ln in self._lines]
            tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            tmp.write_text("\n".join(rendered) + ("\n" if rendered else ""), encoding="utf-8")
            tmp.replace(self._file_path)

    def redact_since(self, cutoff_epoch: float) -> int:
        """Delete every line added at or after `cutoff_epoch`; rewrite the file. Returns lines removed."""
        with self._lock:
            keep = [i for i, t in enumerate(self._times) if t < cutoff_epoch]
            removed = len(self._lines) - len(keep)
            if not removed:
                return 0
            self._lines = [self._lines[i] for i in keep]
            self._times = [self._times[i] for i in keep]
            self._delta_baseline = min(self._delta_baseline, len(self._lines))
            if self._file_path:
                rendered = [self._apply_speaker_map(ln) for ln in self._lines]
                tmp = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
                tmp.write_text("\n".join(rendered) + ("\n" if rendered else ""), encoding="utf-8")
                tmp.replace(self._file_path)
            return removed

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)

    def seconds_since_last(self) -> Optional[float]:
        return None if self._last_add is None else time.time() - self._last_add

    def tail_lines(self, n: int) -> List[str]:
        with self._lock:
            return [self._apply_speaker_map(ln) for ln in self._lines[-n:]]

    def start_new_file(self, file_path: Path) -> None:
        """Begin a new transcript (new session): clear lines, times, renames and copy baseline."""
        with self._lock:
            self._lines, self._times = [], []
            self._speaker_map = {}
            self._delta_baseline = 0
            self._last_add = None
            self._file_path = file_path
            if file_path:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.touch()   # exists at once, so livertt / tail -f can follow it

    def load_existing(self, speaker_map: Optional[dict] = None, day_start: Optional[float] = None) -> int:
        """Resume: read the transcript file back into memory without rewriting it.

        The file holds renamed text ("Speaker Amit:"); undo the rename map so lines are raw
        labels again and future renames keep working. Line times come from "[HH:MM:SS]" on
        the session's start day (rolling over midnight). Returns lines loaded."""
        import re
        from datetime import datetime as _dt
        if not self._file_path or not self._file_path.exists():
            return 0
        smap = dict(speaker_map or {})
        back = {}
        for src, dst in smap.items():
            back.setdefault(dst, src)
        base = _dt.fromtimestamp(day_start if day_start is not None else time.time())
        lines, times, last = [], [], None
        for ln in self._file_path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            for dst, src in back.items():
                ln = ln.replace(f"Speaker {dst}:", f"Speaker {src}:").replace(f"Speaker {dst} ", f"Speaker {src} ")
            m = re.match(r"\[(\d{2}):(\d{2}):(\d{2})\]", ln)
            if m:
                t = base.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)),
                                 microsecond=0).timestamp()
                while last is not None and t < last - 12 * 3600:
                    t += 86400
            else:
                t = last if last is not None else base.timestamp()
            last = t
            lines.append(ln)
            times.append(t)
        with self._lock:
            self._lines, self._times = lines, times
            self._speaker_map = smap
            self._delta_baseline = len(lines)
            self._last_add = time.time()
        return len(lines)

    def all_lines(self) -> List[str]:
        with self._lock:
            return [self._apply_speaker_map(ln) for ln in self._lines]

    def snapshot(self) -> str:
        with self._lock:
            return "\n".join(self._apply_speaker_map(ln) for ln in self._lines)

    def _delta_snapshot_locked(self) -> str:
        baseline = max(0, min(self._delta_baseline, len(self._lines)))
        if baseline >= len(self._lines):
            return ""
        return "\n".join(self._apply_speaker_map(ln) for ln in self._lines[baseline:])

    def copy_full_to_clipboard(self) -> None:
        text = self.snapshot()
        if not text.strip():
            typer.echo("No transcript available to copy yet.")
            return
        if copy_text_to_clipboard(text):
            with self._lock:
                # Mark baseline AFTER copying full transcript: deltas begin from here.
                self._delta_baseline = len(self._lines)
            typer.echo("Transcript copied to clipboard (full, Ctrl+S).")
        else:
            typer.echo("Clipboard copy failed. Install pbcopy/xclip/clip or disable the hotkey.", err=True)

    def copy_delta_to_clipboard(self) -> None:
        with self._lock:
            text = self._delta_snapshot_locked()
        if not text.strip():
            typer.echo("No new transcript since last Ctrl+S.")
            return

        if copy_text_to_clipboard(text):
            typer.echo("Transcript delta copied to clipboard (Ctrl+E).")
        else:
            typer.echo("Clipboard copy failed. Install pbcopy/xclip/clip or disable the hotkey.", err=True)


class ClipboardHotkeyListener(threading.Thread):
    def __init__(
        self,
        buffer: TranscriptBuffer,
        stop_event: threading.Event,
        paused_event: threading.Event,
        actions=None,
    ) -> None:
        super().__init__(daemon=True)
        self._actions = actions
        self._buffer = buffer
        self._stop_event = stop_event
        self._paused_event = paused_event
        self._stdin_fd: int = sys.stdin.fileno() if sys.stdin.isatty() else -1
        self._orig_termios = None

    def run(self) -> None:
        if not sys.stdin.isatty():
            typer.echo("Clipboard hotkey disabled: stdin is not attached to a TTY.", err=True)
            return
        if os.name == "nt":
            self._run_windows()
        else:
            self._run_posix()

    def restore_terminal(self) -> None:
        """Put the terminal back in normal (echo, line) mode; safe to call more than once."""
        if os.name == "nt" or self._orig_termios is None or self._stdin_fd < 0:
            return
        try:
            import termios
            termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._orig_termios)
        except Exception:
            pass
        try:
            sys.__stdout__.write("\033[?2004l")
            sys.__stdout__.flush()
        except Exception:
            pass

    def _prompt_rename(self) -> None:
        """Pause raw-mode input, prompt user for src=dst, apply, resume."""
        speakers = self._buffer.known_speakers()
        is_windows = os.name == "nt"
        # Restore canonical mode on POSIX so input() works normally.
        if not is_windows and self._orig_termios is not None:
            try:
                import termios
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._orig_termios)
            except Exception:
                pass
        try:
            sys.stdout.write("\n\u270f\ufe0f  Rename speaker. ")
            if speakers:
                sys.stdout.write(f"Known: {', '.join(speakers)}\n")
            else:
                sys.stdout.write("(No speakers labeled yet.)\n")
            sys.stdout.write('src=dst (e.g. "Guest-2=Hawk Ticehurst") or blank to cancel: ')
            sys.stdout.flush()
            try:
                raw = sys.stdin.readline().rstrip("\n")
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if not raw.strip():
                typer.echo("Rename cancelled.")
                return
            if "=" not in raw:
                typer.echo("\u26a0\ufe0f  Invalid format. Use src=dst (e.g. Guest-2=Hawk Ticehurst).", err=True)
                return
            src, _, dst = raw.partition("=")
            src = src.strip()
            dst = dst.strip()
            if not src or not dst:
                typer.echo("\u26a0\ufe0f  Both src and dst are required.", err=True)
                return
            if speakers and src not in speakers:
                typer.echo(f"\u26a0\ufe0f  '{src}' not in known speakers ({', '.join(speakers)}). Mapping anyway.")
            count = self._buffer.rename_speaker(src, dst)
            typer.echo(f"\u2705 Renamed {src} \u2192 {dst} ({count} past lines updated, future lines auto-mapped).")
        finally:
            # Re-enter raw mode on POSIX.
            if not is_windows and self._orig_termios is not None:
                try:
                    import termios
                    new_attrs = termios.tcgetattr(self._stdin_fd)
                    new_attrs[3] &= ~(termios.ECHO | termios.ICANON)
                    new_attrs[0] &= ~termios.IXON
                    termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new_attrs)
                except Exception:
                    pass

    def _run_windows(self) -> None:
        try:
            import msvcrt  # type: ignore
        except ImportError:
            typer.echo("Clipboard hotkey disabled: msvcrt unavailable on this platform.", err=True)
            return
        while not self._stop_event.is_set():
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                ch = msvcrt.getwch()  # type: ignore[attr-defined]
                if self._actions is not None:
                    self._actions.handle(ch.encode("utf-8"))
                elif ch == "\x05":  # Ctrl+E
                    self._buffer.copy_delta_to_clipboard()
                elif ch == "\x13":  # Ctrl+S
                    self._buffer.copy_full_to_clipboard()
                elif ch == "\x10":  # Ctrl+P
                    if self._paused_event.is_set():
                        self._paused_event.clear()
                        typer.echo("Transcription resumed (Ctrl+P).")
                    else:
                        self._paused_event.set()
                        typer.echo("Transcription paused (Ctrl+P).")
                elif ch == "\x12":  # Ctrl+R
                    self._prompt_rename()
            time.sleep(0.05)

    def _run_posix(self) -> None:
        import termios
        import tty

        if self._stdin_fd < 0:
            typer.echo("Clipboard hotkey disabled: stdin file descriptor unavailable.", err=True)
            return
        try:
            self._orig_termios = termios.tcgetattr(self._stdin_fd)
        except termios.error as exc:  # pragma: no cover - environment specific
            typer.echo(f"Clipboard hotkey disabled: cannot access terminal attributes ({exc}).", err=True)
            return

        new_attrs = termios.tcgetattr(self._stdin_fd)
        new_attrs[3] &= ~(termios.ECHO | termios.ICANON)
        new_attrs[0] &= ~termios.IXON  # disable software flow control so Ctrl+S is delivered
        termios.tcsetattr(self._stdin_fd, termios.TCSANOW, new_attrs)
        if self._actions is not None:
            try:                                   # bracketed paste: ESC[200~ … ESC[201~
                sys.__stdout__.write("\033[?2004h")  # type: ignore[union-attr]
                sys.__stdout__.flush()  # type: ignore[union-attr]
            except Exception:
                pass

        try:
            while not self._stop_event.is_set():
                rlist, _, _ = select.select([self._stdin_fd], [], [], 0.1)
                if self._actions is not None and self._stdin_fd not in rlist:
                    self._actions.tick()
                if self._stdin_fd in rlist:
                    # Read everything waiting: a paste arrives as one block, so it can be told apart
                    # from typing (one key per read) instead of firing every letter as a hotkey.
                    ch = os.read(self._stdin_fd, 65536) if self._actions is not None else os.read(self._stdin_fd, 1)
                    if self._actions is not None:
                        self._actions.handle(ch)
                    elif ch == b"\x05":
                        self._buffer.copy_delta_to_clipboard()
                    elif ch == b"\x13":
                        self._buffer.copy_full_to_clipboard()
                    elif ch == b"\x10":
                        if self._paused_event.is_set():
                            self._paused_event.clear()
                            typer.echo("Transcription resumed (Ctrl+P).")
                        else:
                            self._paused_event.set()
                            typer.echo("Transcription paused (Ctrl+P).")
                    elif ch == b"\x12":  # Ctrl+R
                        self._prompt_rename()
        finally:
            if self._orig_termios is not None:
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self._orig_termios)


def copy_text_to_clipboard(text: str) -> bool:
    if not text:
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        if os.name == "nt":
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
            return True
        for cmd in ("wl-copy", "xclip", "xsel"):
            if shutil.which(cmd):
                subprocess.run([cmd], input=text.encode("utf-8"), check=True)
                return True
        typer.echo("Clipboard copy not available: install wl-copy or xclip/xsel.", err=True)
    except Exception as exc:
        typer.echo(f"Clipboard copy failed: {exc}", err=True)
    return False

def list_devices() -> None:
    devices = sd.query_devices()
    default_input = sd.default.device[0]
    typer.echo("Available CoreAudio input devices:\n")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        marker = "*" if idx == default_input else " "
        typer.echo(f"{marker} [{idx:>2}] {dev['name']} (max {dev['max_input_channels']} ch)")


def resolve_device(device_option: Optional[str]) -> Optional[int]:
    if device_option is None:
        return None
    devices = sd.query_devices()
    try:
        idx = int(device_option)
        if idx < 0 or idx >= len(devices):
            raise ValueError
        return idx
    except ValueError:
        matches = [i for i, dev in enumerate(devices) if device_option.lower() in dev["name"].lower()]
        if not matches:
            raise typer.BadParameter(f"No device matches '{device_option}'.")
        if len(matches) > 1:
            names = ", ".join(devices[i]["name"] for i in matches)
            raise typer.BadParameter(f"Multiple matches for '{device_option}': {names}. Use an index.")
        return matches[0]


def audio_chunks_from_device(
    capture_queue: queue.Queue[np.ndarray],
    stop_event: threading.Event,
    paused_event: threading.Event,
    samplerate: int,
    block_duration: float,
    device_index: Optional[int],
    use_loopback: bool = False,
    tap: Optional[Callable[[str, np.ndarray], None]] = None,
) -> sd.InputStream:
    blocksize = max(256, int(samplerate * block_duration))

    def callback(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status:
            typer.echo(f"PortAudio status: {status}", err=True)
        if paused_event.is_set():
            return
        if tap is not None:
            tap("audio", indata)
        capture_queue.put(indata.copy())

    stream_kwargs: dict = dict(
        samplerate=samplerate,
        channels=1,
        dtype="float32",
        device=device_index,
        blocksize=blocksize,
        callback=callback,
    )

    # WASAPI loopback (Windows): captures the OUTPUT of a device as if it were an input.
    # Lets us record what's playing through the speakers without virtual cables/drivers.
    if use_loopback:
        try:
            stream_kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
        except AttributeError:
            typer.echo(
                "Warning: WASAPI loopback requested but unavailable on this platform. "
                "Falling back to direct device capture.",
                err=True,
            )

    stream = sd.InputStream(**stream_kwargs)
    stream.start()
    return stream


class _Decimator:
    """Stateful integer-factor downsampler: windowed-sinc low-pass FIR + keep every Nth sample.
    Carries filter history across blocks so block joins add no clicks; a block of N*k samples
    returns exactly k samples."""

    def __init__(self, factor: int, taps: int = 97) -> None:
        self.factor = factor
        n = np.arange(taps) - (taps - 1) / 2
        cutoff = 0.45 / factor                      # cycles/sample, just under the new Nyquist
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        self._h = (h / h.sum()).astype(np.float32)
        self._hist = np.zeros(taps - 1, dtype=np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        buf = np.concatenate([self._hist, x])
        y = np.convolve(buf, self._h, mode="valid")  # len(x) outputs, aligned to x
        self._hist = buf[-(len(self._h) - 1):]
        return y[self.factor - 1::self.factor].astype(np.float32)


class MixedDeviceCapture:
    """Captures from a primary device (system audio) and a mic, mixes them in real time.

    Each stream pushes into its own ring buffer. A consumer thread pulls equal-sized
    blocks from both and sums them (with simple clipping protection) into capture_queue.
    If one stream falls behind, the other plays through alone for that block, so the
    transcript never stalls waiting on a silent device.
    """

    def __init__(
        self,
        capture_queue: "queue.Queue[np.ndarray]",
        stop_event: threading.Event,
        paused_event: threading.Event,
        samplerate: int,
        block_duration: float,
        primary_device: Optional[int],
        mic_device: Optional[int],
        use_loopback: bool = False,
        mic_gain: float = 1.0,
        tap: Optional[Callable[[str, np.ndarray], None]] = None,
        mic_muted: Optional[Callable[[], bool]] = None,
        hq_mic_rate: Optional[int] = None,
    ) -> None:
        self.tap = tap
        self.mic_muted = mic_muted or (lambda: False)
        self.capture_queue = capture_queue
        self.stop_event = stop_event
        self.paused_event = paused_event
        self.samplerate = samplerate
        self.blocksize = max(256, int(samplerate * block_duration))
        self.mic_gain = mic_gain

        self._primary_buf: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._mic_buf: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)

        primary_kwargs = dict(
            samplerate=samplerate, channels=1, dtype="float32",
            device=primary_device, blocksize=self.blocksize,
            callback=self._make_cb(self._primary_buf),
        )
        if use_loopback:
            try:
                primary_kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
            except AttributeError:
                typer.echo("Warning: WASAPI loopback unavailable; using direct capture.", err=True)

        self._primary_stream = sd.InputStream(**primary_kwargs)
        # With --record, the mic opens ONCE at hq_mic_rate (an exact multiple of the capture
        # rate): the raw audio is recorded as "mic_hq" (voice-training quality) and a
        # stateful low-pass + decimation feeds the normal-rate mixer. A second stream on the
        # same device starves the first in Core Audio, so never open the mic twice.
        self._decim = None
        mic_rate, mic_block, mic_cb = samplerate, self.blocksize, self._make_cb(self._mic_buf)
        if tap is not None and hq_mic_rate and hq_mic_rate % samplerate == 0 and hq_mic_rate > samplerate:
            self._decim = _Decimator(hq_mic_rate // samplerate)
            mic_rate, mic_block = hq_mic_rate, self.blocksize * self._decim.factor
            mic_cb = self._make_hq_mic_cb()
        try:
            self._mic_stream = sd.InputStream(
                samplerate=mic_rate, channels=1, dtype="float32",
                device=mic_device, blocksize=mic_block, callback=mic_cb,
            )
        except Exception as exc:
            if self._decim is None:
                raise
            typer.echo(f"Note: mic at {mic_rate} Hz unavailable ({exc}); recording mic at {samplerate} Hz only.", err=True)
            self._decim = None
            self._mic_stream = sd.InputStream(
                samplerate=samplerate, channels=1, dtype="float32",
                device=mic_device, blocksize=self.blocksize,
                callback=self._make_cb(self._mic_buf),
            )

        self._mixer_thread = threading.Thread(target=self._mixer_loop, daemon=True)


    def _make_cb(self, buf: "queue.Queue[np.ndarray]"):
        def cb(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
            if status:
                typer.echo(f"PortAudio status: {status}", err=True)
            if self.paused_event.is_set():
                return
            try:
                buf.put_nowait(indata.copy())
            except queue.Full:
                # Drop oldest block to keep latency bounded.
                try:
                    buf.get_nowait()
                    buf.put_nowait(indata.copy())
                except queue.Empty:
                    pass
        return cb

    def _make_hq_mic_cb(self):
        normal = self._make_cb(self._mic_buf)

        def cb(indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
            if self.paused_event.is_set():
                return
            raw = indata[:, 0]
            if self.tap is not None and not self.mic_muted():
                self.tap("mic_hq", raw.copy())
            normal(self._decim.process(raw).reshape(-1, 1), frames // self._decim.factor, time_info, status)
        return cb

    def _drain_block(self, buf: "queue.Queue[np.ndarray]") -> Optional[np.ndarray]:
        try:
            return buf.get(timeout=0.5)
        except queue.Empty:
            return None

    def _mixer_loop(self) -> None:
        zeros = np.zeros((self.blocksize, 1), dtype=np.float32)
        while not self.stop_event.is_set():
            primary = self._drain_block(self._primary_buf)
            mic = self._drain_block(self._mic_buf)
            if primary is None and mic is None:
                continue
            if primary is None:
                primary = zeros
            if mic is None:
                mic = zeros
            # Align block lengths defensively
            n = min(primary.shape[0], mic.shape[0])
            if self.tap is not None:
                # Separate tracks: "others" (meeting audio) and "mic" (you).
                self.tap("others", primary[:n])
                if not self.mic_muted():
                    self.tap("mic", mic[:n])
            gain = 0.0 if self.mic_muted() else self.mic_gain
            mixed = primary[:n] + (mic[:n] * gain)
            np.clip(mixed, -1.0, 1.0, out=mixed)
            self.capture_queue.put(mixed)

    def start(self) -> None:
        self._primary_stream.start()
        self._mic_stream.start()
        self._mixer_thread.start()

    def stop(self) -> None:
        for s in (self._primary_stream, self._mic_stream):
            try:
                s.stop()
                s.close()
            except Exception:
                pass


SOUND_FILE_EXTS = {".wav", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".aifc"}


def _audio_chunks_soundfile(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    frames_per_block = max(256, int(samplerate * block_duration))
    with sf.SoundFile(path, mode="r") as handle:
        if handle.samplerate != samplerate:
            raise typer.BadParameter(
                f"Input file sample rate {handle.samplerate} Hz does not match target {samplerate} Hz."
            )
        while True:
            data = handle.read(frames_per_block, dtype="float32", always_2d=True)
            if not len(data):
                break
            if data.shape[1] > 1:
                data = np.mean(data, axis=1, keepdims=True)
            yield data


def _audio_chunks_av(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    frames_per_block = max(256, int(samplerate * block_duration))
    buffer = np.empty((0,), dtype=np.float32)
    try:
        with av.open(str(path)) as container:
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            if audio_stream is None:
                raise typer.BadParameter(f"No audio stream found in {path}.")
            audio_stream.thread_type = "AUTO"
            resampler = AudioResampler(format="flt", layout="mono", rate=samplerate)
            for frame in container.decode(audio_stream):
                resampled_frames = resampler.resample(frame)
                if not resampled_frames:
                    continue
                if not isinstance(resampled_frames, list):
                    resampled_frames = [resampled_frames]
                for resampled in resampled_frames:
                    arr = resampled.to_ndarray()
                    if arr.ndim > 1:
                        arr = np.mean(arr, axis=0)
                    arr = arr.astype(np.float32)
                    buffer = np.concatenate((buffer, arr))
                    while buffer.shape[0] >= frames_per_block:
                        chunk = buffer[:frames_per_block]
                        buffer = buffer[frames_per_block:]
                        yield chunk.reshape(-1, 1)
    except av.AVError as exc:
        raise typer.BadParameter(f"Failed to decode {path.name}: {exc}") from exc
    if buffer.size:
        yield buffer.reshape(-1, 1)


def audio_chunks_from_file(path: Path, samplerate: int, block_duration: float) -> Iterable[np.ndarray]:
    suffix = path.suffix.lower()
    if suffix in SOUND_FILE_EXTS:
        try:
            yield from _audio_chunks_soundfile(path, samplerate, block_duration)
            return
        except Exception as exc:  # pragma: no cover - fallback path
            typer.echo(f"soundfile could not read {path.name}: {exc}. Falling back to PyAV.", err=True)
    yield from _audio_chunks_av(path, samplerate, block_duration)


class LocalWhisperBackend:
    def __init__(
        self,
        model_size: str,
        compute: str,
        language: Optional[str],
        beam_size: int,
    ) -> None:
        self.model = WhisperModel(model_size, device="auto", compute_type=compute)
        self.language = language
        self.beam_size = beam_size

    def transcribe_window(self, audio_window: np.ndarray, sample_rate: int) -> List[str]:
        audio_window = audio_window.astype(np.float32)
        if np.max(np.abs(audio_window)) < 1e-5:
            return []
        kwargs = dict(
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
        )
        lang = self.language
        try:
            segments, _ = self.model.transcribe(audio_window, language=lang, **kwargs)
        except ValueError as exc:
            if lang is None and "max() arg is an empty sequence" in str(exc):
                segments, _ = self.model.transcribe(audio_window, language="en", **kwargs)
            else:
                raise
        lines: List[str] = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                lines.append(text)
        return lines


class AzureSpeechBackend:
    def __init__(
        self,
        region: Optional[str],
        endpoint: Optional[str],
        language: str,
        sample_rate: int,
        enable_speaker_labels: bool,
        key: Optional[str] = None,
        auth_token: Optional[str] = None,
        auto_detect_languages: Optional[List[str]] = None,
        token_provider=None,
    ) -> None:
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is not installed.")
        if not key and not auth_token:
            raise ValueError("Azure Speech backend requires either a subscription key or an auth token.")

        if key:
            if endpoint:
                speech_config = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
            else:
                if not region:
                    raise ValueError("Azure Speech backend requires either region or endpoint.")
                speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        else:
            if endpoint:
                speech_config = speechsdk.SpeechConfig(endpoint=endpoint)
            else:
                if not region:
                    raise ValueError("Azure Speech backend requires either region or endpoint.")
                speech_config = speechsdk.SpeechConfig(auth_token=auth_token, region=region)
            speech_config.authorization_token = auth_token

        # Language: use auto-detect if multiple languages requested, else single language
        self._auto_detect_config: Optional[speechsdk.languageconfig.AutoDetectSourceLanguageConfig] = None
        if auto_detect_languages and len(auto_detect_languages) > 1:
            self._auto_detect_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=auto_detect_languages
            )
            # Enable CONTINUOUS language detection (re-detects per segment, not just at start)
            # This is the key setting for Hebrew/English code-switching mid-sentence
            try:
                speech_config.set_property(
                    property_id=speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode,
                    value="Continuous",
                )
                typer.echo("Azure language ID mode: Continuous (best for mixed Hebrew/English)", err=True)
            except AttributeError:
                # Older SDK versions may not have this PropertyId — falls back to AtStart detection
                typer.echo("Azure language ID mode: AtStart (upgrade SDK for Continuous mode)", err=True)
            typer.echo(f"Azure auto-detect languages: {', '.join(auto_detect_languages)}", err=True)
        else:
            speech_config.speech_recognition_language = language
        if enable_speaker_labels:
            diarize_property = getattr(
                speechsdk.PropertyId,
                "SpeechServiceResponse_DiarizeIntermediateResults",
                None,
            )
            if diarize_property is not None:
                speech_config.set_property(property_id=diarize_property, value="true")
        self._enable_speaker_labels = enable_speaker_labels
        self._use_conversation = enable_speaker_labels
        self._conversation_transcriber = None
        self._speech_recognizer = None
        self._speech_config = speech_config
        self._token_provider = token_provider
        self._sample_rate = sample_rate
        self._build_recognizer()
        self._text_queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        self._session_stopped_event = threading.Event()
        self._auth_error_event = threading.Event()
        # --- Token auto-refresh (AAD) ---
        # AAD tokens expire ~60-90 min. The Speech SDK silently rotates its
        # WebSocket connection mid-session; if the authorization_token is stale
        # at that moment the upgrade fails with HTTP 401 and the session is
        # canceled. We proactively refresh authorization_token on the live
        # speech_config every few minutes so a fresh token is always present.
        self._speech_config = speech_config
        self._token_provider = token_provider
        self._sample_rate = sample_rate
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_interval = 540  # seconds (9 min — well under the ~60 min TTL)

    def _on_session_started(self, evt) -> None:
        session_id = getattr(evt, "session_id", None) or getattr(evt, "sessionId", None)
        if session_id:
            typer.echo(f"Azure session started (id={session_id})", err=True)
        else:
            typer.echo("Azure session started", err=True)

    def _on_recognized(self, evt) -> None:
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            self._text_queue.put(evt.result.text)

    def _on_transcribed(self, evt) -> None:
        text = evt.result.text.strip()
        if text:
            speaker = evt.result.speaker_id
            if speaker:
                self._text_queue.put(f"Speaker {speaker}: {text}")
            else:
                self._text_queue.put(text)

    def _on_canceled(self, evt) -> None:
        reason = getattr(evt, "reason", None)
        error_details = getattr(evt, "error_details", None) or getattr(evt, "errorDetails", None)
        error_code = getattr(evt, "error_code", None) or getattr(evt, "errorCode", None)

        if hasattr(evt, "result"):
            try:
                details = speechsdk.CancellationDetails(evt.result)
                reason = getattr(details, "reason", reason)
                error_code = getattr(details, "error_code", error_code)
                error_details = getattr(details, "error_details", error_details)
            except Exception:
                pass

        # Detect auth/connection failures (e.g. expired AAD token → 401 on
        # WebSocket upgrade) so the worker loop can transparently re-auth and
        # resume instead of leaving the session permanently deaf.
        details_str = str(error_details or "")
        is_auth_error = (
            "401" in details_str
            or "Authentication" in details_str
            or "Forbidden" in details_str
            or "403" in details_str
        )
        if is_auth_error:
            self._auth_error_event.set()
            # Refresh the token immediately so the next start() uses a valid one.
            self._refresh_token(force=True)

        typer.echo(f"Azure canceled (reason={reason}, code={error_code}): {error_details}", err=True)

    def _on_session_stopped(self, evt) -> None:
        session_id = getattr(evt, "session_id", None) or getattr(evt, "sessionId", None)
        if session_id:
            typer.echo(f"Azure session stopped (id={session_id})", err=True)
        else:
            typer.echo("Azure session stopped", err=True)
        self._session_stopped_event.set()

    def _build_recognizer(self) -> None:
        """(Re)build the push stream + recognizer/transcriber and wire events.

        Called once at init and again by restart() after an auth-error cancel.
        A PushAudioInputStream cannot be reused after the session is canceled,
        so we always create a fresh one here."""
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=self._sample_rate, bits_per_sample=16, channels=1
        )
        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)
        self._conversation_transcriber = None
        self._speech_recognizer = None
        if self._enable_speaker_labels:
            if self._auto_detect_config:
                self._conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                    auto_detect_source_language_config=self._auto_detect_config,
                )
            else:
                self._conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                )
            self._conversation_transcriber.transcribed.connect(self._on_transcribed)
            self._conversation_transcriber.canceled.connect(self._on_canceled)
            self._conversation_transcriber.session_stopped.connect(self._on_session_stopped)
            self._conversation_transcriber.session_started.connect(self._on_session_started)
        else:
            if self._auto_detect_config:
                self._speech_recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self._speech_config,
                    audio_config=audio_config,
                    auto_detect_source_language_config=self._auto_detect_config,
                )
            else:
                self._speech_recognizer = speechsdk.SpeechRecognizer(
                    speech_config=self._speech_config, audio_config=audio_config
                )
            self._speech_recognizer.recognized.connect(self._on_recognized)
            self._speech_recognizer.canceled.connect(self._on_canceled)
            self._speech_recognizer.session_stopped.connect(self._on_session_stopped)
            self._speech_recognizer.session_started.connect(self._on_session_started)
        self._apply_phrase_list()

    def _apply_phrase_list(self) -> None:
        """Domain terms as a Speech SDK phrase list (rebuilt with the recognizer on restart)."""
        try:
            from corrections import phrase_list
            terms = phrase_list()
            rec = self._conversation_transcriber if self._use_conversation else self._speech_recognizer
            if terms and rec is not None:
                grammar = speechsdk.PhraseListGrammar.from_recognizer(rec)
                for t in terms:
                    grammar.addPhrase(t)
                self.phrase_count = len(terms)
        except Exception as exc:          # never block transcription over a hint list
            typer.echo(f"Phrase list not applied: {exc}", err=True)

    def restart(self) -> bool:
        """Tear down the canceled recognizer and stand up a fresh one with a
        valid token. Used by the worker to auto-recover from an auth-error
        cancel without losing the rest of the meeting. Returns True on success."""
        # Token was already refreshed in _on_canceled; refresh once more to be
        # safe (handles the case where the cancel-time refresh failed).
        self._refresh_token(force=True)
        # Best-effort teardown of the dead recognizer.
        try:
            if self._use_conversation and self._conversation_transcriber is not None:
                self._conversation_transcriber.stop_transcribing_async().get()
            elif self._speech_recognizer is not None:
                self._speech_recognizer.stop_continuous_recognition_async().get()
        except Exception:
            pass
        try:
            self.push_stream.close()
        except Exception:
            pass
        # Reset the stopped flag and rebuild from scratch.
        self._session_stopped_event.clear()
        try:
            self._build_recognizer()
            if self._use_conversation:
                assert self._conversation_transcriber is not None
                self._conversation_transcriber.start_transcribing_async().get()
            else:
                assert self._speech_recognizer is not None
                self._speech_recognizer.start_continuous_recognition_async().get()
            return True
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Azure restart failed: {exc}", err=True)
            return False

    def _refresh_token(self, force: bool = False) -> bool:
        """Fetch a fresh AAD token and apply it to the live speech_config.

        Returns True if a new token was applied. Safe to call repeatedly; on
        key-auth (no token_provider) it is a no-op.
        """
        if self._token_provider is None:
            return False
        try:
            new_token = self._token_provider()
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Azure token refresh failed: {exc}", err=True)
            return False
        if not new_token:
            return False
        try:
            self._speech_config.authorization_token = new_token
            if force:
                typer.echo("Azure token refreshed (post-auth-error).", err=True)
            else:
                typer.echo("Azure token refreshed (proactive).", err=True)
            return True
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"Failed to apply refreshed Azure token: {exc}", err=True)
            return False

    def _refresh_loop(self) -> None:
        """Background thread: proactively refresh the AAD token on an interval
        so the SDK's silent WebSocket reconnects always see a valid token."""
        while not self._stop_event.wait(self._refresh_interval):
            self._refresh_token(force=False)

    def start(self) -> None:
        # Apply a fresh token before connecting, then keep it fresh in the
        # background so mid-session reconnects never present an expired token.
        self._refresh_token(force=False)
        if self._token_provider is not None and self._refresh_thread is None:
            self._refresh_thread = threading.Thread(
                target=self._refresh_loop, daemon=True, name="azure-token-refresh"
            )
            self._refresh_thread.start()
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.start_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.start_continuous_recognition_async().get()

    def stop(self) -> None:
        self._stop_event.set()
        # Close the push stream to signal end-of-audio, giving Azure a
        # chance to finalize any pending utterances before we stop the recognizer.
        try:
            self.push_stream.close()
        except Exception:
            pass  # already closed
        if self._use_conversation:
            assert self._conversation_transcriber is not None
            self._conversation_transcriber.stop_transcribing_async().get()
        else:
            assert self._speech_recognizer is not None
            self._speech_recognizer.stop_continuous_recognition_async().get()

    def push_audio(self, audio: np.ndarray) -> None:
        audio = np.squeeze(audio)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        self.push_stream.write(pcm.tobytes())

    def drain_text(self) -> List[str]:
        lines: List[str] = []
        while True:
            try:
                lines.append(self._text_queue.get_nowait())
            except queue.Empty:
                break
        return lines


def output_writer(buffer: TranscriptBuffer, echo: Callable[[], bool] = lambda: True,
                  accept: Callable[[str], bool] = lambda text: True):
    lock = threading.Lock()
    try:   # domain-term casing on EVERY backend (chunked ones already did it; it is idempotent)
        from corrections import load_vocabulary
        vocab = load_vocabulary()
    except Exception:
        vocab = None

    def emit(text: str) -> None:
        if not accept(text):
            return
        if vocab is not None and len(vocab) and not str(text).startswith(("[", "★")):
            fixed = vocab.normalize(str(text))
            if fixed != str(text):
                text = text.with_text(fixed) if hasattr(text, "with_text") else fixed
        # Lines from chunked backends carry the time they were SPOKEN (chunk start + segment
        # offset); fall back to arrival time for everything else.
        spoken = getattr(text, "t", None)
        now = time.time()
        when = spoken if spoken is not None and now - 3600 < spoken <= now + 5 else now
        timestamp = time.strftime("%H:%M:%S", time.localtime(when))
        line = f"[{timestamp}] {text}"
        with lock:
            if echo():
                typer.echo(line)
            buffer.add(line, t=when)

    return emit


def process_local_backend(
    backend: LocalWhisperBackend,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    emit,
    samplerate: int,
    window_seconds: float,
) -> None:
    samples_per_window = max(1024, int(samplerate * window_seconds))
    buffer = np.empty((0,), dtype=np.float32)
    while not stop_event.is_set() or not audio_queue.empty():
        if paused_event.is_set():
            buffer = np.empty((0,), dtype=np.float32)
            while True:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    break
            time.sleep(0.1)
            continue
        try:
            chunk = audio_queue.get(timeout=0.2)
            chunk = np.squeeze(chunk)
            buffer = np.concatenate((buffer, chunk))
        except queue.Empty:
            continue
        while buffer.shape[0] >= samples_per_window:
            window = buffer[:samples_per_window]
            buffer = buffer[samples_per_window:]
            lines = backend.transcribe_window(window, samplerate)
            for line in lines:
                emit(line)
    if buffer.size:
        lines = backend.transcribe_window(buffer, samplerate)
        for line in lines:
            emit(line)


def process_azure_backend(
    backend: AzureSpeechBackend,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    emit,
) -> None:
    backend.start()
    try:
        while not stop_event.is_set() or not audio_queue.empty():
            # If the session was canceled by an auth error (e.g. expired AAD
            # token → 401 on WebSocket reconnect), transparently re-establish
            # the recognizer instead of going permanently deaf. The token was
            # already refreshed in _on_canceled; restart the recognizer here.
            if backend._auth_error_event.is_set() and not stop_event.is_set():
                typer.echo("Azure: auth error detected — re-establishing session...", err=True)
                resumed = backend.restart()
                if resumed:
                    emit("[RTT: session auto-recovered after auth error]")
                    backend._auth_error_event.clear()
                else:
                    typer.echo("Azure: auto-recovery failed, retrying in 3s...", err=True)
                    time.sleep(3)
                    continue
            if paused_event.is_set():
                while True:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        break
                backend.drain_text()
                time.sleep(0.1)
                continue
            try:
                chunk = audio_queue.get(timeout=0.2)
                backend.push_audio(chunk)
            except queue.Empty:
                pass
            for line in backend.drain_text():
                emit(line)
        # All audio has been pushed. Close the push stream to signal end-of-audio
        # so Azure can finalize any pending utterances, then keep draining results
        # until the session fully stops.
        backend.push_stream.close()
        while not backend._session_stopped_event.wait(timeout=0.2):
            for line in backend.drain_text():
                emit(line)
        # Final drain after session has stopped
        for line in backend.drain_text():
            emit(line)
    except Exception as exc:
        typer.echo(f"Azure backend worker failed: {exc}", err=True)
        stop_event.set()
    finally:
        backend.stop()
        # Drain any results produced during stop()
        for line in backend.drain_text():
            emit(line)


def read_file_into_queue(
    input_file: Path,
    samplerate: int,
    block_duration: float,
    audio_queue: "queue.Queue[np.ndarray]",
    stop_event: threading.Event,
    paused_event: threading.Event,
    skip_seconds: float,
    max_seconds: Optional[float],
) -> None:
    delivered = 0.0
    skipped = 0.0
    realtime = os.environ.get("RTT_FILE_REALTIME", "0").lower() in ("1", "true", "yes", "on")
    blocks = iter(audio_chunks_from_file(input_file, samplerate, block_duration))
    while True:
        if stop_event.is_set():
            break
        if paused_event.is_set():
            time.sleep(0.1)
            continue
        try:
            block = next(blocks)
        except StopIteration:
            break
        block_duration_sec = block.shape[0] / samplerate
        if skip_seconds > 0 and skipped < skip_seconds:
            remaining_skip = skip_seconds - skipped
            if remaining_skip >= block_duration_sec:
                skipped += block_duration_sec
                continue
            start_index = int(remaining_skip * samplerate)
            block = block[start_index:]
            block_duration_sec = block.shape[0] / samplerate
            skipped = skip_seconds
            if block_duration_sec <= 0:
                continue
        if max_seconds is not None:
            remaining = max_seconds - delivered
            if remaining <= 0:
                break
            allowed_samples = int(remaining * samplerate)
            if block.shape[0] > allowed_samples:
                block = block[:allowed_samples]
                block_duration_sec = block.shape[0] / samplerate
        audio_queue.put(block)
        delivered += block_duration_sec
        if realtime:
            # Play the file at speaking speed (demos / testing live controls on a recording).
            time.sleep(block_duration_sec)
        if max_seconds is not None and delivered >= max_seconds:
            break
    typer.echo("Finished streaming file audio.")
    stop_event.set()


def validate_azure_config(key: Optional[str], region: Optional[str], endpoint: Optional[str]) -> None:
    if not region and not endpoint:
        raise typer.BadParameter(
            "Azure backend requires either AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT (env or CLI)."
        )


def print_runtime_config(cfg, transcript_path: Path) -> None:
    """Print an allowlisted view of resolved options; never dump credentials/argv."""
    from urllib.parse import urlsplit

    backend = cfg["backend"]
    values = {"backend": backend,
              "env_file": os.environ.get("RTT_LOADED_ENV_FILE") or "not loaded by run.sh (process environment + CLI defaults)",
              "input": str(cfg["input_file"]) if cfg["input_file"] else (cfg["input_device"] or "system default"),
              "sample_rate": cfg["sample_rate"],
              "block_seconds": cfg["block_duration"],
              "output": str(transcript_path.resolve())}
    values["audio_destination"] = ("on this device (Whisper)" if backend == BACKEND_LOCAL else
                                   "configured VibeVoice server" if backend == BACKEND_VIBE else
                                   "configured Azure resource")
    summary_service = "configured summary endpoint" if os.environ.get("RTT_COPILOT_URL") else "GitHub Copilot"
    values["live_summary"] = (f"ON: sends transcript text to {summary_service} (opt out: --no-summary)"
                              if cfg["summary"] else "OFF: no transcript sent for summaries (enable: --summary)")
    if cfg["input_file"]:
        values.update(skip_seconds=cfg["skip_seconds"], max_seconds=cfg["max_seconds"])
    else:
        values.update(loopback=cfg["use_loopback"], include_mic=cfg["include_mic"])
        if cfg["include_mic"]:
            values.update(mic=cfg["mic_device"] or "system default", mic_gain=cfg["mic_gain"])
    endpoint = None
    if backend == BACKEND_LOCAL:
        values.update(model=cfg["model_size"], compute=cfg["compute"], beam_size=cfg["beam_size"],
                      language=cfg["language"] or "auto", window_seconds=cfg["window_seconds"])
    elif backend == BACKEND_VIBE:
        values.update(auth="none (local loopback)", model="VibeVoice-ASR 7B (local MPS)",
                      chunk_seconds=cfg["chunk_seconds"],
                      endpoint=os.environ.get("RTT_VIBE_URL", "http://127.0.0.1:8765"),
                      prompt="set" if os.environ.get("RTT_VIBE_PROMPT") else "none")
    else:
        key = cfg["azure_key"] or os.environ.get("AZURE_SPEECH_KEY")
        values["auth"] = "API key (hidden)" if key else "Azure AD"
        if not key:
            sub = os.environ.get("RTT_AAD_SUBSCRIPTION", "").strip()
            values["aad_subscription"] = sub if sub and AzureCliCredential is not None else "unpinned (DefaultAzureCredential)"
        if backend == BACKEND_AZURE:
            endpoint = cfg["azure_endpoint"] or os.environ.get("AZURE_SPEECH_ENDPOINT")
            values.update(region=cfg["azure_region"] or os.environ.get("AZURE_SPEECH_REGION") or "not set",
                          language=cfg["azure_languages"] or cfg["language"] or "en-US",
                          speaker_labels=cfg["azure_speaker_labels"])
        else:
            values["chunk_seconds"] = cfg["chunk_seconds"]
            if backend == BACKEND_OPENAI:
                endpoint = cfg["openai_endpoint"] or os.environ.get("RTT_OPENAI_ENDPOINT")
                values.update(deployment=cfg["openai_deployment"], language=cfg["language"] or "auto",
                              temperature=cfg["openai_temperature"])
            else:
                endpoint = (cfg["llmspeech_endpoint"] or os.environ.get("RTT_LLMSPEECH_ENDPOINT")
                            or os.environ.get("AZURE_SPEECH_ENDPOINT"))
                if backend == BACKEND_MAI:
                    endpoint = cfg["mai_endpoint"] or os.environ.get("RTT_MAI_ENDPOINT") or endpoint
                    values.update(model=cfg["mai_model"], locales=cfg["mai_locales"] or "auto")
                else:
                    values.update(model="LLM Speech enhancedMode", locales=cfg["llmspeech_locales"] or "auto")
        # Strip URL userinfo, query and fragment even if a credential-bearing URL is supplied.
        if endpoint:
            try:
                url = urlsplit(endpoint)
                values["endpoint"] = f"{url.scheme}://{url.hostname}/" if url.scheme and url.hostname else "set (invalid URL hidden)"
            except ValueError:
                values["endpoint"] = "set (invalid URL hidden)"
        else:
            values["endpoint"] = "not set"
    typer.echo("RTT configuration:")
    for name, value in values.items():
        typer.echo(f"  {name}: {value}")


@app.command()
def main(
    show_config: bool = typer.Option(False, "--show-config", help="Print resolved settings and exit without capture or authentication."),
    backend: str = typer.Option(BACKEND_LOCAL, "--backend", help="local, azure, openai, llmspeech, or mai"),
    model_size: str = typer.Option("base", "--model-size", help="Whisper model size for local backend."),
    compute: str = typer.Option("auto", "--compute", help="Whisper compute type: auto, cpu, metal, cuda."),
    beam_size: int = typer.Option(1, "--beam-size", min=1, max=5, help="Beam size for decoding."),
    language: Optional[str] = typer.Option(None, "--language", help="Language hint like en, en-US."),
    input_device: Optional[str] = typer.Option(
        os.environ.get("RTT_INPUT_DEVICE"), "--input-device", help="Device index or name substring."
    ),
    list_devices_flag: bool = typer.Option(False, "--list-devices", help="Only list devices and exit."),
    use_loopback: bool = typer.Option(
        os.environ.get("RTT_USE_LOOPBACK", "") == "1",
        "--loopback/--no-loopback",
        help="WASAPI loopback (Windows): capture the OUTPUT of --input-device instead of its input. Lets you record system audio with zero virtual-cable setup.",
    ),
    include_mic: bool = typer.Option(
        os.environ.get("RTT_INCLUDE_MIC", "") == "1",
        "--include-mic/--no-include-mic",
        help="Mix the microphone into the captured audio so your own voice appears in the transcript. Honors RTT_INCLUDE_MIC=1.",
    ),
    mic_device: Optional[str] = typer.Option(
        os.environ.get("RTT_MIC_DEVICE"),
        "--mic-device",
        help="Mic device name/index for --include-mic. Defaults to the system default input.",
    ),
    mic_gain: float = typer.Option(
        float(os.environ.get("RTT_MIC_GAIN", "1.0")),
        "--mic-gain",
        help="Gain applied to the mic before mixing (0.0–2.0 sensible range).",
    ),
    sample_rate: int = typer.Option(16000, "--sample-rate", help="Capture sample rate (Hz)."),
    block_duration: float = typer.Option(0.5, "--block-duration", help="Capture block size in seconds."),
    window_seconds: float = typer.Option(2.5, "--window", help="Whisper window size in seconds."),
    input_file: Optional[Path] = typer.Option(None, "--input-file", exists=True, help="Stream audio from a WAV/FLAC/MP3 file."),
    skip_seconds: float = typer.Option(0.0, "--skip-seconds", min=0.0, help="Skip this many seconds when reading --input-file."),
    max_seconds: Optional[float] = typer.Option(None, "--max-seconds", min=0.1, help="Limit file playback to this many seconds."),
    output_file: Optional[Path] = typer.Option(None, "--output-file", help="Append transcripts to this file."),
    azure_key: Optional[str] = typer.Option(None, "--azure-key", help="Override AZURE_SPEECH_KEY."),
    azure_region: Optional[str] = typer.Option(None, "--azure-region", help="Override AZURE_SPEECH_REGION."),
    azure_endpoint: Optional[str] = typer.Option(None, "--azure-endpoint", help="Override AZURE_SPEECH_ENDPOINT."),
    azure_resource_id: Optional[str] = typer.Option(None, "--azure-resource-id", help="Override AZURE_SPEECH_RESOURCE_ID (required for Azure AD auth)."),
    azure_speaker_labels: bool = typer.Option(
        False,
        "--azure-speaker-labels/--no-azure-speaker-labels",
        help="Enable Azure Conversation Transcriber speaker diarization (Azure backend only).",
    ),
    azure_languages: Optional[str] = typer.Option(
        None,
        "--azure-languages",
        help="Comma-separated language codes for auto-detection, e.g. 'en-US,he-IL'. Overrides --language for Azure backend.",
    ),
    chunk_seconds: float = typer.Option(
        10.0,
        "--chunk-seconds",
        min=3.0,
        max=60.0,
        help="Chunk window (seconds) for openai/llmspeech backends. Larger = more accurate but higher latency.",
    ),
    chunk_mode: str = typer.Option(
        os.environ.get("RTT_CHUNK_MODE", "fixed"),
        "--chunk-mode",
        help="Chunked backends: 'fixed' (cut every --chunk-seconds) or 'auto' (cut at pauses, grow when the backend falls behind). Change live with [ ] and \\.",
    ),
    openai_endpoint: Optional[str] = typer.Option(
        None,
        "--openai-endpoint",
        help="Azure OpenAI resource endpoint for the 'openai' backend (or set RTT_OPENAI_ENDPOINT).",
    ),
    openai_deployment: str = typer.Option(
        "gpt-4o-transcribe-diarize",
        "--openai-deployment",
        help="Deployment name for the 'openai' backend.",
    ),
    openai_temperature: float = typer.Option(
        0.0,
        "--openai-temperature",
        min=0.0,
        max=1.0,
        help="Sampling temperature for the 'openai' backend. 0.0 = deterministic (best for jargon/numbers).",
    ),
    llmspeech_endpoint: Optional[str] = typer.Option(
        None,
        "--llmspeech-endpoint",
        help="Azure Speech resource endpoint for the 'llmspeech' backend (or set RTT_LLMSPEECH_ENDPOINT / AZURE_SPEECH_ENDPOINT).",
    ),
    llmspeech_locales: Optional[str] = typer.Option(
        None,
        "--llmspeech-locales",
        help="Comma-separated locales to bias the 'llmspeech' backend, e.g. 'en-US,he-IL'. Omit for multilingual auto-detect.",
    ),
    mai_endpoint: Optional[str] = typer.Option(
        None,
        "--mai-endpoint",
        help="Azure Speech resource endpoint for the 'mai' backend (or set RTT_MAI_ENDPOINT / RTT_LLMSPEECH_ENDPOINT / AZURE_SPEECH_ENDPOINT).",
    ),
    mai_model: str = typer.Option(
        "MAI-Transcribe-2",
        "--mai-model",
        help="MAI transcription model for the 'mai' backend (e.g. MAI-Transcribe-2, MAI-Transcribe-1.5).",
    ),
    mai_locales: Optional[str] = typer.Option(
        None,
        "--mai-locales",
        help="Comma-separated locales to bias the 'mai' backend, e.g. 'en-US,he-IL'. Omit for auto-detect.",
    ),
    record: bool = typer.Option(
        os.environ.get("RTT_RECORD", "0").lower() in ("1", "true", "yes", "on"),
        "--record/--no-record",
        help="Save session audio to sessions/<id>/ (separate others/mic tracks when mixing the mic). Off by default.",
    ),
    summary: bool = typer.Option(
        os.environ.get("RTT_SUMMARY", "1").lower() in ("1", "true", "yes", "on"),
        "--summary/--no-summary",
        help="Live summary via GitHub Copilot (RTT_SUMMARY_MODEL, default gpt-6.1-terra). Sends transcript text to Copilot.",
    ),
    tui: bool = typer.Option(
        os.environ.get("RTT_TUI", "1").lower() in ("1", "true", "yes", "on"),
        "--tui/--no-tui",
        help="Full-screen split view (transcript | summary) with single-key controls. Needs a terminal.",
    ),
    session_name: Optional[str] = typer.Option(
        None, "--session-name", help="Name for this session's folder, e.g. 'team-weekly'.",
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Resume a stopped session by id (e.g. 20261109-090803). No value = the latest.",
    ),
    speakers: Optional[int] = typer.Option(
        int(os.environ["RTT_SPEAKERS"]) if os.environ.get("RTT_SPEAKERS", "").isdigit() else None,
        "--speakers", help="How many people are in the call. Stops one noisy voice being split into many speakers (k changes it live).",
    ),
    continue_: Optional[str] = typer.Option(
        None, "-c", "--continue",
        help="Resume the latest session, or the latest whose name / meeting title contains this text.",
    ),
) -> None:
    if list_devices_flag:
        list_devices()
        raise typer.Exit()

    if input_file and input_device:
        raise typer.BadParameter("Use either --input-file or --input-device, not both.")

    if backend not in {BACKEND_LOCAL, BACKEND_AZURE, BACKEND_OPENAI, BACKEND_LLMSPEECH, BACKEND_MAI, BACKEND_VIBE}:
        raise typer.BadParameter("--backend must be 'local', 'azure', 'openai', 'llmspeech', or 'mai'.")
    if azure_speaker_labels and backend != BACKEND_AZURE:
        raise typer.BadParameter("--azure-speaker-labels is only valid with --backend azure.")

    resume_dir: Optional[Path] = None
    resume_meta: dict = {}
    if resume is not None or continue_ is not None:
        from session import find_session
        from session import session_is_running
        q = resume if resume is not None else continue_
        exact = Path("sessions") / (q or "")
        if q and (exact / "session.json").exists() and session_is_running(
                json.loads((exact / "session.json").read_text(encoding="utf-8"))):
            raise typer.BadParameter(f"session {q} is still running in another RTT; stop it first.")
        try:
            resume_dir = find_session(Path("sessions"), q)
        except FileNotFoundError as exc:
            raise typer.BadParameter(str(exc))
        resume_meta = json.loads((resume_dir / "session.json").read_text(encoding="utf-8"))
        if input_file:
            raise typer.BadParameter("--resume / -c is for live capture, not --input-file.")
        if output_file is None and resume_meta.get("transcript"):
            output_file = Path(resume_meta["transcript"])
    transcript_path = output_file or default_transcript_path()
    print_runtime_config(locals(), transcript_path)
    if show_config:
        raise typer.Exit()
    if output_file is None:
        typer.echo(f"Autosaving transcript to {transcript_path} (use --output-file to override).")
    transcript_buffer = TranscriptBuffer(transcript_path)
    resumed_lines = 0
    if resume_dir is not None:
        try:
            day0 = datetime.fromisoformat(resume_meta.get("started_at", "")).timestamp()
        except ValueError:
            day0 = None
        raw_rows = None
        try:
            raw_rows = json.loads((resume_dir / "lines.json").read_text(encoding="utf-8"))
            on_disk = sum(1 for ln in Path(transcript_path).read_text(encoding="utf-8").splitlines() if ln.strip())
            if len(raw_rows) != on_disk:      # lines.json older than the file (crash): don't trust it
                raw_rows = None
        except (OSError, ValueError):
            raw_rows = None
        if raw_rows is not None:
            resumed_lines = transcript_buffer.load_raw(raw_rows, resume_meta.get("speaker_map") or {})
        else:
            resumed_lines = transcript_buffer.load_existing(resume_meta.get("speaker_map") or {}, day0)
    ui_holder: dict = {"ui": None}
    emit = output_writer(
        transcript_buffer, echo=lambda: ui_holder["ui"] is None,
        accept=lambda text: ui_holder.get("session") is None or ui_holder["session"].accept_line(text),
    )
    audio_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=120)  # ~60 s headroom for backend switches
    stop_event = threading.Event()
    paused_event = threading.Event()

    # ---- session layer: state, control socket, recording, summary, UI ----
    from session import Session
    from tui import KeyActions, SplitView

    def _notify(msg: str) -> None:
        ui = ui_holder["ui"]
        if ui is not None:
            ui.log(msg)
        else:
            typer.echo(msg)

    session = Session(
        backend=backend, transcript_path=transcript_path, paused_event=paused_event,
        stop_event=stop_event, emit=emit, name=session_name, record=bool(record and not input_file),
        sample_rate=sample_rate, notify=_notify, resume_dir=resume_dir,
    )
    session.buffer_ref = transcript_buffer
    ui_holder["session"] = session
    from chunk_policy import ChunkPolicy

    def _chunk_changed(pol) -> None:
        if pol.mode == "auto" and abs(pol.current - pol.seconds) >= 0.5:
            _notify(f"Chunk window now {pol.current:.0f}s (auto: backend was falling behind).")
        else:
            _notify(f"Chunk window: {pol.label().replace('chunk ', '')}.")

    session.chunk = ChunkPolicy(chunk_seconds, mode=chunk_mode, on_change=_chunk_changed)
    from speakers import SpeakerTracker
    speaker_tracker = SpeakerTracker.create()
    session.speaker_tracker = speaker_tracker
    if resume_dir is not None:
        voices = session.load_voices()
        if speakers is None and session.expected_speakers:
            speakers = session.expected_speakers
        title = session.meeting_title or session.name or ""
        typer.echo(f"Resuming session {session.id}" + (f" — {title}" if title else "")
                   + f": {resumed_lines} lines, {voices} known voices, stopped at {resume_meta.get('ended_at') or '?'}.")
        emit(f"[▶ RTT session resumed — stopped at {(resume_meta.get('ended_at') or '?')[-8:]}]")
    if speakers:
        session.expected_speakers = speakers
        if speaker_tracker is not None:
            speaker_tracker.set_max_speakers(speakers)
    if speaker_tracker is None:
        why = "off (RTT_SPEAKER_TRACKING=0)" if os.environ.get("RTT_SPEAKER_TRACKING", "1") in ("0", "false", "no", "off") \
            else _speaker_tracking_reason()
        typer.echo(f"Speaker tracking across chunks: {why}. Labels restart in every chunk.")
    else:
        typer.echo("Speaker tracking across chunks: on (TitaNet-small; Speaker 1, 2, … are stable for the whole session).")
    if not session_name and not input_file and os.environ.get("RTT_CALENDAR", "workiq") == "workiq":
        from session import title_when_in_call
        threading.Thread(target=title_when_in_call, args=(session, stop_event), daemon=True).start()
    if record and input_file:
        typer.echo("--record ignored in file mode (the input file is already the recording).")
    sock = session.start_control_server()
    typer.echo(f"Session {session.id} → {session.dir}" + (f" · control: rtt-ctl ({sock})" if sock else ""))
    if session.recorder:
        typer.echo("● Recording audio to the session folder (--no-record to disable).")

    summarizer = None
    if summary:
        from summarizer import LiveSummarizer
        summarizer = LiveSummarizer(transcript_buffer.all_lines, session.dir / "summary.md")
        session.summarizer = summarizer
        if resume_dir is not None and summarizer.resume_from(session.dir / "summary.md", resumed_lines):
            typer.echo("Continuing the saved summary.")
        summarizer.start()
        typer.echo(f"Live summary on ({summarizer.model}, every {summarizer.interval:.0f}s).")

    use_tui = bool(tui and sys.stdin.isatty() and sys.stdout.isatty())
    session.lang_cfg = {"language": language, "azure_languages": azure_languages,
                        "llmspeech_locales": llmspeech_locales, "mai_locales": mai_locales}
    session.new_transcript_path = default_transcript_path

    def _after_new_session() -> None:
        if not input_file and os.environ.get("RTT_CALENDAR", "workiq") == "workiq":
            from session import title_when_in_call
            threading.Thread(target=title_when_in_call, args=(session, stop_event), daemon=True).start()

    session.on_new_session = _after_new_session
    ui = SplitView(session, transcript_buffer, summarizer) if use_tui else None
    actions = KeyActions(session, transcript_buffer, summarizer, copy_text_to_clipboard, _notify, ui=ui)
    hotkey_listener = ClipboardHotkeyListener(transcript_buffer, stop_event, paused_event, actions=actions)
    hotkey_listener.start()
    if ui is not None:
        ui.start()
        ui_holder["ui"] = ui
    else:
        typer.echo("Keys: space pause · m mark · u mute mic · c/e copy · s/a/d copy summary · n summarize · r rename · qq stop · ? help")

    capture_thread: Optional[threading.Thread] = None
    stream: Optional[sd.InputStream] = None

    ctrl_c = {"armed_at": 0.0}

    def force_quit() -> None:
        """Last resort: restore the terminal, stop what we started, exit now."""
        try:
            ui = ui_holder.get("ui")
            if ui is not None:
                ui.stop()
        except Exception:
            pass
        try:
            hotkey_listener.restore_terminal()
        except Exception:
            pass
        try:
            session.save()
            session.close_fast()
        except Exception:
            pass
        if os.environ.get("RTT_VIBE_KEEP", "0") != "1" and "vibe" in [b["backend"] for b in session.backend_history]:
            stop_vibe_server(os.environ.get("RTT_VIBE_URL", "http://127.0.0.1:8765"), wait_s=1.0)
        sys.stdout.write("\nRTT force-quit.\n")
        sys.stdout.flush()
        os._exit(130)

    def handle_interrupt(signum, frame):
        if signum == signal.SIGTERM:
            stop_event.set()
            return
        if stop_event.is_set():
            # Already shutting down and the user insists: quit immediately.
            force_quit()
        now = time.time()
        if now - ctrl_c["armed_at"] <= 3.0:
            _notify("Stopping…")
            session.stop("ctrl-c")
        else:
            ctrl_c["armed_at"] = now
            _notify("Press Ctrl+C again within 3 s to stop (q q also works).")

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    try:
        if input_file:
            capture_thread = threading.Thread(
                target=read_file_into_queue,
                args=(
                    input_file,
                    sample_rate,
                    block_duration,
                    audio_queue,
                    stop_event,
                    paused_event,
                    skip_seconds,
                    max_seconds,
                ),
                daemon=True,
            )
            capture_thread.start()
        else:
            device_index = resolve_device(input_device)
            if include_mic:
                mic_index = resolve_device(mic_device) if mic_device else None
                primary_name = sd.query_devices(device_index)["name"] if device_index is not None else "system default"
                mic_name = sd.query_devices(mic_index)["name"] if mic_index is not None else "system default"
                typer.echo(f"🎙️  Mixing: capture=[{primary_name}] + mic=[{mic_name}] (gain={mic_gain})")
                mixer = MixedDeviceCapture(
                    audio_queue, stop_event, paused_event, sample_rate, block_duration,
                    primary_device=device_index, mic_device=mic_index,
                    use_loopback=use_loopback, mic_gain=mic_gain,
                    tap=lambda track, block: session.recorder.write(track, block) if session.recorder else None,
                    mic_muted=lambda: session.mic_muted,
                    hq_mic_rate=48000 if record else None,
                )
                mixer.start()
                stream = mixer  # has .stop() interface used during cleanup
            else:
                stream = audio_chunks_from_device(
                    audio_queue, stop_event, paused_event, sample_rate, block_duration, device_index, use_loopback=use_loopback,
                    tap=lambda track, block: session.recorder.write(track, block) if session.recorder else None,
                )

        def build_worker(backend: str, worker_stop: threading.Event):
            """Create (not start) the transcription worker for `backend`.

            Used at startup and for mid-session backend switches; each worker has its
            own stop event so a switch can retire one backend while capture continues."""
            chunked_backend = None
            if backend == BACKEND_LOCAL:
                typer.echo(f"Starting local Whisper backend ({model_size})")
                local_backend = LocalWhisperBackend(model_size, compute, language, beam_size)
                worker = threading.Thread(
                    target=process_local_backend,
                    args=(local_backend, audio_queue, worker_stop, paused_event, emit, sample_rate, window_seconds),
                    daemon=True,
                )
            elif backend == BACKEND_AZURE:
                key = azure_key or os.environ.get("AZURE_SPEECH_KEY")
                region = azure_region or os.environ.get("AZURE_SPEECH_REGION")
                endpoint = azure_endpoint or os.environ.get("AZURE_SPEECH_ENDPOINT")
                validate_azure_config(key, region, endpoint)

                auth_token = None
                token_provider = None
                if not key:
                    if DefaultAzureCredential is None:
                        raise typer.BadParameter(
                            "No AZURE_SPEECH_KEY set and azure-identity is not installed. "
                            "Install it with: pip install azure-identity"
                        )
                    resource_id = azure_resource_id or os.environ.get("AZURE_SPEECH_RESOURCE_ID")
                    if not resource_id:
                        raise typer.BadParameter(
                            "Azure AD auth requires AZURE_SPEECH_RESOURCE_ID (the full ARM resource ID). "
                            "Set it in .env or pass --azure-resource-id."
                        )
                    typer.echo("No API key found — authenticating with Azure AD...")
                    try:
                        credential = _build_aad_credential()

                        # Reusable closure so the backend can mint a FRESH token on
                        # demand (proactive refresh + post-401 recovery). AAD tokens
                        # expire ~60 min; without this the SDK's silent WebSocket
                        # reconnect presents a stale token and the session 401s.
                        def token_provider(_rid=resource_id, _cred=credential):
                            tok = _cred.get_token("https://cognitiveservices.azure.com/.default")
                            return f"aad#{_rid}#{tok.token}"

                        auth_token = token_provider()
                    except Exception as exc:
                        typer.echo(f"Azure AD authentication failed: {exc}", err=True)
                        raise typer.Exit(code=1)
                    typer.echo("Azure AD token acquired successfully.")

                typer.echo("Starting Azure Speech backend")
                auto_detect_langs: Optional[List[str]] = None
                if azure_languages:
                    auto_detect_langs = [l.strip() for l in azure_languages.split(",") if l.strip()]
                try:
                    azure_backend = AzureSpeechBackend(
                        region,
                        endpoint,
                        language or "en-US",
                        sample_rate,
                        azure_speaker_labels,
                        key=key,
                        auth_token=auth_token,
                        auto_detect_languages=auto_detect_langs,
                        token_provider=token_provider if not key else None,
                    )
                except Exception as exc:
                    typer.echo(f"Failed to initialize Azure Speech backend: {exc}", err=True)
                    raise typer.Exit(code=1)
                worker = threading.Thread(
                    target=process_azure_backend,
                    args=(azure_backend, audio_queue, worker_stop, paused_event, emit),
                    daemon=True,
                )
            elif backend == BACKEND_VIBE:
                # Local VibeVoice server on loopback: no Azure auth, nothing leaves the Mac.
                vibe_url = os.environ.get("RTT_VIBE_URL", "http://127.0.0.1:8765")
                vibe_prompt = os.environ.get("RTT_VIBE_PROMPT") or None
                try:
                    ensure_vibe_server(vibe_url)
                except Exception as exc:
                    typer.echo(f"VibeVoice server not reachable at {vibe_url}: {exc}", err=True)
                    raise typer.Exit(code=1)
                typer.echo(f"Starting local VibeVoice backend ({vibe_url}, chunk={chunk_seconds:.0f}s)")
                chunked_backend = VibeVoiceLocalBackend(
                    sample_rate=sample_rate, chunk_seconds=chunk_seconds,
                    url=vibe_url, prompt=vibe_prompt,
                )
                worker = threading.Thread(
                    target=process_chunked_backend,
                    args=(chunked_backend, audio_queue, worker_stop, paused_event, emit),
                    daemon=True,
                )
            else:
                # Chunked HTTP backends (openai / llmspeech). Both micro-batch audio
                # into --chunk-seconds windows and POST each chunk. Auth reuses the
                # same AAD token_provider discipline as the Speech SDK backend: a
                # callable that mints a FRESH bearer token on demand (~60 min TTL).
                cog_key = azure_key or os.environ.get("AZURE_SPEECH_KEY")
                cog_token_provider: Optional[
                    Callable[[], Union[str, Tuple[str, float]]]
                ] = None
                if not cog_key:
                    if DefaultAzureCredential is None:
                        raise typer.BadParameter(
                            "No API key set and azure-identity is not installed. "
                            "Install it with: pip install azure-identity"
                        )
                    typer.echo("No API key found — authenticating with Azure AD...")
                    try:
                        _cred = _build_aad_credential()

                        # Return (token, expires_on) so the backend refreshes on the
                        # token's REAL expiry, not a blind wall-clock TTL. Critical
                        # because DefaultAzureCredential often falls through to the
                        # shared `az` CLI token, which can be handed over already
                        # aged (<30 min life) — a fixed 30-min TTL would then let it
                        # die mid-meeting before the scheduled refresh ever fires.
                        def _mint_cog_token(_c=_cred):
                            tok = _c.get_token("https://cognitiveservices.azure.com/.default")
                            return (tok.token, float(tok.expires_on))

                        _mint_cog_token()  # fail fast if credentials are unusable
                        cog_token_provider = _mint_cog_token
                    except Exception as exc:
                        typer.echo(f"Azure AD authentication failed: {exc}", err=True)
                        raise typer.Exit(code=1)
                    typer.echo("Azure AD token acquired successfully.")

                if backend == BACKEND_OPENAI:
                    oai_endpoint = openai_endpoint or os.environ.get("RTT_OPENAI_ENDPOINT")
                    if not oai_endpoint:
                        raise typer.BadParameter(
                            "The 'openai' backend requires --openai-endpoint or RTT_OPENAI_ENDPOINT "
                            "(the Azure OpenAI resource endpoint)."
                        )
                    typer.echo(
                        f"Starting Azure OpenAI transcribe backend "
                        f"(deployment={openai_deployment}, chunk={chunk_seconds:.0f}s)"
                    )
                    try:
                        chunked_backend = OpenAITranscribeBackend(
                            endpoint=oai_endpoint,
                            deployment=openai_deployment,
                            sample_rate=sample_rate,
                            chunk_seconds=chunk_seconds,
                            language=language,
                            temperature=openai_temperature,
                            token_provider=cog_token_provider,
                            api_key=cog_key,
                        )
                    except Exception as exc:
                        typer.echo(f"Failed to initialize Azure OpenAI backend: {exc}", err=True)
                        raise typer.Exit(code=1)
                else:  # BACKEND_LLMSPEECH or BACKEND_MAI
                    lls_endpoint = (
                        llmspeech_endpoint
                        or os.environ.get("RTT_LLMSPEECH_ENDPOINT")
                        or os.environ.get("AZURE_SPEECH_ENDPOINT")
                    )
                    if backend == BACKEND_MAI:
                        mai_ep = (
                            mai_endpoint
                            or os.environ.get("RTT_MAI_ENDPOINT")
                            or lls_endpoint
                        )
                        if not mai_ep:
                            raise typer.BadParameter(
                                "The 'mai' backend requires --mai-endpoint, RTT_MAI_ENDPOINT, "
                                "RTT_LLMSPEECH_ENDPOINT, or AZURE_SPEECH_ENDPOINT "
                                "(the Azure Speech resource endpoint)."
                            )
                        mai_locales_list: Optional[List[str]] = None
                        if mai_locales:
                            mai_locales_list = [l.strip() for l in mai_locales.split(",") if l.strip()]
                        typer.echo(
                            f"Starting Azure MAI transcription backend "
                            f"(model={mai_model}, chunk={chunk_seconds:.0f}s, "
                            f"locales={mai_locales_list or 'auto'})"
                        )
                        try:
                            chunked_backend = MAITranscribeBackend(
                                endpoint=mai_ep,
                                sample_rate=sample_rate,
                                chunk_seconds=chunk_seconds,
                                model=mai_model,
                                locales=mai_locales_list,
                                token_provider=cog_token_provider,
                                api_key=cog_key,
                            )
                        except Exception as exc:
                            typer.echo(f"Failed to initialize Azure MAI backend: {exc}", err=True)
                            raise typer.Exit(code=1)
                    else:  # BACKEND_LLMSPEECH
                        if not lls_endpoint:
                            raise typer.BadParameter(
                                "The 'llmspeech' backend requires --llmspeech-endpoint, "
                                "RTT_LLMSPEECH_ENDPOINT, or AZURE_SPEECH_ENDPOINT "
                                "(the Azure Speech resource endpoint)."
                            )
                        locales_list: Optional[List[str]] = None
                        if llmspeech_locales:
                            locales_list = [l.strip() for l in llmspeech_locales.split(",") if l.strip()]
                        typer.echo(
                            f"Starting Azure LLM Speech backend "
                            f"(chunk={chunk_seconds:.0f}s, locales={locales_list or 'auto'})"
                        )
                        try:
                            chunked_backend = LLMSpeechBackend(
                                endpoint=lls_endpoint,
                                sample_rate=sample_rate,
                                chunk_seconds=chunk_seconds,
                                locales=locales_list,
                                token_provider=cog_token_provider,
                                api_key=cog_key,
                            )
                        except Exception as exc:
                            typer.echo(f"Failed to initialize Azure LLM Speech backend: {exc}", err=True)
                            raise typer.Exit(code=1)

                worker = threading.Thread(
                    target=process_chunked_backend,
                    args=(chunked_backend, audio_queue, worker_stop, paused_event, emit),
                    daemon=True,
                )
            if chunked_backend is not None:
                chunked_backend.attach_policy(session.chunk)
                chunked_backend.tracker = speaker_tracker
            return worker, chunked_backend

        worker_stop = threading.Event()
        worker, chunked_backend = build_worker(backend, worker_stop)
        session.backend_ref = chunked_backend
        worker.start()
        typer.echo("Press Ctrl+C (or q q) to stop.")
        while True:
            worker.join(timeout=0.5)
            if stop_event.is_set():
                worker_stop.set()
                _notify("Finishing the last audio chunk… (Ctrl+C to quit now)")
                worker.join(timeout=float(os.environ.get("RTT_STOP_FLUSH_TIMEOUT", "15")))
                if worker.is_alive():
                    _notify("Last chunk did not finish in time; stopping without it.")
                break
            if not worker.is_alive():
                break
            target = session.take_switch_request()
            if not target:
                continue
            if target == session.backend:
                _notify(f"Already on {target}.")
                continue
            _notify(f"Switching backend {session.backend} → {target}…")
            new_stop = threading.Event()
            try:
                new_worker, new_chunked = build_worker(target, new_stop)
            except (typer.Exit, typer.BadParameter, Exception) as exc:  # keep the current backend
                _notify(f"Switch to {target} failed; staying on {session.backend} ({exc!r})"[:200])
                continue
            worker_stop.set()
            worker.join(timeout=180)  # old backend flushes its buffered audio first
            worker, worker_stop = new_worker, new_stop
            session.backend_ref = new_chunked
            session.set_backend(target)
            worker.start()
    finally:
        stop_event.set()
        # 1) stop audio capture first so nothing new arrives during shutdown
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if capture_thread and capture_thread.is_alive():
            capture_thread.join(timeout=1)
        # 2) leave the full-screen UI and give the terminal back
        if ui_holder["ui"] is not None:
            ui_holder["ui"].stop()
            ui_holder["ui"] = None
        hotkey_listener.join(timeout=1)
        hotkey_listener.restore_terminal()
        typer.echo("Stopping RTT… (Ctrl+C to force quit)")
        # 3) one last summary update, bounded so exit never hangs on the network
        if summarizer is not None:
            summarizer.stop()
            limit = float(os.environ.get("RTT_FINAL_SUMMARY_TIMEOUT", "15"))
            typer.echo(f"Writing final summary (up to {limit:.0f} s)…")
            t = threading.Thread(target=summarizer.update_once, daemon=True)
            t.start()
            t.join(timeout=limit)
            if t.is_alive():
                typer.echo("Final summary skipped (timed out); summary.md has the last good version.")
        session.close()
        # 4) shut down the local VibeVoice server if this session used it
        used_vibe = "vibe" in [b["backend"] for b in session.backend_history]
        if used_vibe and os.environ.get("RTT_VIBE_KEEP", "0") != "1":
            vibe_url = os.environ.get("RTT_VIBE_URL", "http://127.0.0.1:8765")
            typer.echo("Stopping local VibeVoice server (RTT_VIBE_KEEP=1 to keep it)…")
            stop_vibe_server(vibe_url)
        typer.echo(f"Session saved: {session.dir}")
        typer.echo(resume_hint(session, transcript_buffer))


if __name__ == "__main__":
    sys.argv[1:] = _normalize_resume_argv(sys.argv[1:])
    app()
