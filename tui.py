"""Split-view terminal UI for RTT (rich).

    ┌ ● LIVE 0:12:41 · openai · ● REC · mic on · summary 12:40 ───────────────┐
    │ transcript (tail)                   │ Summary / Decisions / Action items │
    │                                     │ / Open questions                   │
    └ space pause · m mark · u mute · c copy · s copy summary · ? help ───────┘

The UI only renders; all actions go through Session / TranscriptBuffer /
LiveSummarizer. Key input is read by the existing hotkey listener thread, which
calls `KeyActions.handle()`; this module defines that mapping for both TUI and
plain (scrolling) mode so the keys are identical in both.
"""
from __future__ import annotations

import codecs
import io
import re
import sys
import threading
import time
from collections import deque
from typing import Callable, List, Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

HELP = [
    ("space", "pause / resume"), ("m", "mark moment"), ("M", "mark with note"),
    ("u", "mute / unmute my mic"), ("c", "copy full transcript"), ("e", "copy new since last c"),
    ("s", "copy summary"), ("a", "copy action items"), ("d", "copy decisions"),
    ("n", "update summary now"), ("r", "rename speaker (or click a line)"),
    ("[ / ]", "smaller / bigger chunk window (chunked backends)"),
    ("\\", "chunk window auto (cut at pauses, adapt to backend speed) / fixed"),
    ("z", "undo last action (rename, mark, mute, pause, switch)"),
    ("N", "new session (asks for a name; capture keeps running)"),
    ("t", "name speakers from a Teams/Zoom transcript you copied (clipboard)"),
    ("T", "same paste: names + the app's wording for matched lines; L learns misheard names"),
    ("k", "how many people are in the call (stops one voice becoming Speaker 3, 4, 5…)"),
    ("o", "off the record: delete last N min + pause"), ("b", "switch backend live"), ("q q", "stop session (or Ctrl+C twice)"), ("Tab", "switch panel (transcript / summary)"),
    ("PgUp/PgDn", "scroll the active panel (↑/↓ one line, Home/End; End = live)"),
    ("wheel", "scroll the panel under the mouse"), ("? / Esc", "this help / close it"),
]
FOOTER = "space pause · m mark · u mute · N new session · o off-record · t names · T refine · k people · b backend · [ ] chunk · z undo · c copy · s summary · Tab panel · PgUp/PgDn scroll · click a name to rename · ? all keys"


class LogSink(io.TextIOBase):
    """Replaces stdout/stderr while the full-screen UI runs; keeps recent messages."""

    def __init__(self, ui: "SplitView") -> None:
        self._ui = ui
        self._pending = ""

    def write(self, s: str) -> int:
        self._pending += s
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._ui.log(line.strip())
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class SplitView:
    def __init__(self, session, buffer, summarizer=None, refresh_per_second: float = 4) -> None:
        self.session = session
        self.buffer = buffer
        self.summarizer = summarizer
        self.messages: deque = deque(maxlen=50)
        self.prompt: Optional[str] = None   # text shown while the user types into a prompt
        self.show_help = False
        self.click_map: list = []        # [(first_row, last_row, speaker_label)] of visible lines
        self.undo_button = None          # (first_col0, end_col0) on footer_row, when undo is possible
        self.speaker_colors: dict = {}   # display name -> color, in order of first appearance
        self.color_text = False          # also tint wrapped continuation rows
        self.footer_row = 0
        self.row_map: list = []          # [(first_row, last_row, line_text)] of every visible line
        self.transcript_width = 0
        # Scrolling: focus is the panel Tab/PgUp/PgDn act on; None top = follow the live end.
        self.focus = "tx"
        self.tx_top: Optional[int] = None
        self.sum_top = 0
        self.tx_page = 10
        self.sum_page = 10
        self._tx_rows_total = 0
        self._sum_rows_total = 0
        self._real_out = sys.stdout
        self._real_err = sys.stderr
        self.console = Console(file=self._real_out)
        self._live: Optional[Live] = None
        self._rps = refresh_per_second

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._live = Live(self._render(), console=self.console, screen=True,
                          auto_refresh=False, redirect_stdout=False, redirect_stderr=False)
        self._live.start()
        # SGR mouse reporting: clicks arrive on stdin as ESC [ < b ; x ; y M
        self._real_out.write("\033[?1000h\033[?1006h")
        self._real_out.flush()
        sink = LogSink(self)
        sys.stdout = sink  # type: ignore[assignment]
        sys.stderr = sink  # type: ignore[assignment]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._live is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        sys.stdout = self._real_out
        sys.stderr = self._real_err
        self._real_out.write("\033[?1006l\033[?1000l")
        self._real_out.flush()
        self._live.stop()
        self._live = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._live.update(self._render(), refresh=True)
            except Exception as exc:  # rendering must never kill the session
                self.messages.append(f"ui error: {exc}")
            self._stop.wait(1.0 / self._rps)

    def log(self, msg: str) -> None:
        self.messages.append(f"{time.strftime('%H:%M:%S')} {msg}")

    # ---- scrolling --------------------------------------------------------------
    def toggle_focus(self) -> str:
        self.focus = "sum" if self.focus == "tx" else "tx"
        return self.focus

    def scroll(self, panel: str, rows: int) -> None:
        """rows < 0 = up (older), > 0 = down. Transcript returns to live when it reaches the end."""
        if panel == "tx":
            total, page = self._tx_rows_total, self.tx_page
            live_top = max(0, total - page)
            top = live_top if self.tx_top is None else self.tx_top
            top = max(0, min(live_top, top + rows))
            self.tx_top = None if top >= live_top else top
        else:
            total, page = self._sum_rows_total, self.sum_page
            self.sum_top = max(0, min(max(0, total - page), self.sum_top + rows))

    def scroll_home(self, panel: str) -> None:
        if panel == "tx":
            self.tx_top = 0 if self._tx_rows_total > self.tx_page else None
        else:
            self.sum_top = 0

    def scroll_end(self, panel: str) -> None:
        if panel == "tx":
            self.tx_top = None
        else:
            self.sum_top = max(0, self._sum_rows_total - self.sum_page)

    def speaker_color(self, label: str) -> Optional[str]:
        if label in ("?", None):
            return "grey50"
        c = self.speaker_colors.get(label)
        if c is None:
            c = SPEAKER_COLORS[len(self.speaker_colors) % len(SPEAKER_COLORS)]
            self.speaker_colors[label] = c
        return c

    @staticmethod
    def _wrap(text: str, width: int, cont_indent: int = 0) -> List[str]:
        """Wrap at word boundaries (long words still break). Rows after the first fit in
        width - cont_indent; the caller draws the indent."""
        import textwrap
        out = []
        for part in text.split("\n"):
            if not part:
                out.append("")
                continue
            first = textwrap.wrap(part, width, break_on_hyphens=False) or [""]
            out.append(first[0])
            rest = part[len(first[0]):].strip()
            if rest:
                out += textwrap.wrap(rest, max(10, width - cont_indent), break_on_hyphens=False)
        return out

    # ---- rendering -----------------------------------------------------------
    def _header(self) -> Text:
        st = self.session.status()
        t = Text()
        if st["state"] == "paused":
            t.append(" ⏸ PAUSED ", style="bold black on yellow")
        else:
            t.append(" ● LIVE ", style="bold white on red")
        t.append(f" {st['elapsed']} · {st['backend']}")
        lg = st.get("lang") or {}
        if lg.get("label"):
            good = lg.get("he") and lg.get("en")
            t.append(f" · {lg['label']}", style="green" if good else "yellow")
        if st.get("chunk_label"):
            t.append(f" · {st['chunk_label']}", style="cyan")
        if st.get("meeting_title"):
            t.append(f" · {st['meeting_title'][:40]}", style="bold")
        if st.get("record_enabled"):
            t.append(" · ")
            t.append("● REC" if st["recording"] else "REC off", style="bold red" if st["recording"] else "dim")
        t.append(" · ")
        t.append("mic muted" if st["mic_muted"] else "mic on", style="yellow" if st["mic_muted"] else "")
        if not st.get("backend_healthy", True):
            t.append(" · BACKEND DOWN", style="bold white on red")
        age = st.get("last_text_age_s")
        if age is not None:
            t.append(f" · last text {age}s ago", style="red" if age > 120 and st["state"] == "live" else "dim")
        t.append(f" · ★ {st['marks']}")
        q = self.session.quality() if hasattr(self.session, "quality") else {}
        if q.get("expected"):
            t.append(f" · {q['expected']} people", style="dim")
        for w in q.get("warnings", []):
            t.append(f" · ⚠ {w}", style="bold black on yellow")
            key = next((k for k in ("voices", "choppy", "unknown") if k in w), w)
            seen = getattr(self.session, "_quality_warned", None)
            if seen is not None and key not in seen:     # say it once in the message line too
                seen.add(key)
                self.log(f"Transcription quality: {w}")
        if self.summarizer is not None:
            s = self.summarizer.status()
            label = "summarizing…" if s["busy"] else (f"summary {s['last_update']}" if s["last_update"] else "summary pending")
            t.append(f" · {label}", style="red" if s["error"] else "cyan")
        return t

    def _transcript_panel(self, height: int, width: int) -> Panel:
        lines = self.buffer.all_lines()
        usable = max(1, height - 2)
        inner_w = max(10, width - 4)
        rows = []   # (text, style, speaker_label, line_index, first_row_of_line)
        for li, ln in enumerate(lines):
            style = "bold yellow" if ln.find("[★ RTT bookmark") >= 0 else (
                "dim" if any(m in ln for m in ("[⏸", "[▶", "[🔇", "[🎙", "[⇄", "[✂", "[✦")) else "")
            label = speaker_of(ln)
            indent = CONT_INDENT if label else 0
            shown = ln.replace(f"Speaker {label}:", f"{label}:", 1) if label else ln   # names, not "Speaker"
            for si, seg in enumerate(self._wrap(shown, inner_w, indent)):
                rows.append((seg, style, label, li, si == 0))
        self._tx_rows_total, self.tx_page = len(rows), usable
        live_top = max(0, len(rows) - usable)
        top = live_top if self.tx_top is None else min(self.tx_top, live_top)
        view = rows[top:top + usable]
        # Screen geometry for mouse clicks: header row 1, panel border row 2, content from row 3,
        # content columns start at 3 (border + 1 padding).
        click_map, last_li = [], None
        row_map = []
        for i, (_, _, label, li, _) in enumerate(view):
            y = 3 + i
            if row_map and row_map[-1][3] == li:
                row_map[-1] = (row_map[-1][0], y, row_map[-1][2], li)
            else:
                row_map.append((y, y, lines[li], li))
            if label:
                y = 3 + i
                if click_map and li == last_li:
                    click_map[-1] = (click_map[-1][0], y, label)   # wrapped row of the same line
                else:
                    click_map.append((y, y, label))
            last_li = li
        self.click_map = click_map
        self.row_map = [(a, b, t) for a, b, t, _ in row_map]
        self.transcript_width = width
        body = Text(no_wrap=True, overflow="crop")
        for seg, style, label, _, first in view:
            color = self.speaker_color(label) if label and not style else None
            if color and first and f"{label}:" in seg:
                pre, _, rest = seg.partition(f"{label}:")
                body.append(pre, style="dim")
                body.append(f"{label}:", style=f"italic {color}" if label == "?" else f"bold {color}")
                body.append(rest + "\n")
            elif color and not first:
                body.append(" " * (CONT_INDENT - 2) + "▏ ", style=color)    # bar in the speaker's color
                body.append(seg + "\n", style=color if self.color_text else "")
            else:
                body.append(seg + "\n", style=style)
        title = "Transcript"
        if self.tx_top is not None:
            below = len(rows) - (top + len(view))
            title += f" · scrolled · {below} rows below · End = live"
        focused = self.focus == "tx"
        return Panel(body, title=("▶ " if focused else "") + title,
                     border_style="bold bright_blue" if focused else "blue")

    def _summary_panel(self, height: int = 0, width: int = 0) -> Panel:
        focused = self.focus == "sum"
        border = "bold bright_cyan" if focused else "cyan"
        if self.summarizer is None:
            return Panel(Text("Live summary is off.\nStart with --summary to enable.", style="dim"),
                         title="Summary", border_style="grey50")
        inner_w = max(10, width - 4) if width else 60
        usable = max(1, height - 2) if height else 1000
        secs = self.summarizer.sections
        rows = []   # (text, style)
        for name in ("Summary", "Decisions", "Action items", "Open questions"):
            rows.append((name, "bold cyan"))
            items = secs.get(name) or []
            if not items:
                rows.append(("  —", "dim"))
            for it in items:
                for k, seg in enumerate(self._wrap(it, inner_w - 2)):
                    rows.append((("• " if k == 0 else "  ") + seg, ""))
        err = self.summarizer.status()["error"]
        if err:
            rows.append(("", ""))
            rows += [(seg, "red") for seg in self._wrap(f"summary error: {err}", inner_w)]
        self._sum_rows_total, self.sum_page = len(rows), usable
        self.sum_top = max(0, min(self.sum_top, max(0, len(rows) - usable)))
        view = rows[self.sum_top:self.sum_top + usable]
        body = Text(no_wrap=True, overflow="crop")
        for seg, style in view:
            body.append(seg + "\n", style=style)
        title = f"Live summary · {self.summarizer.model}"
        hidden_above, hidden_below = self.sum_top, len(rows) - (self.sum_top + len(view))
        if hidden_above or hidden_below:
            title += f" · ↑{hidden_above} ↓{hidden_below}"
        return Panel(body, title=("▶ " if focused else "") + title, border_style=border)

    def _footer(self) -> Text:
        t = Text()
        self.undo_button = None
        if self.prompt is None and hasattr(self.session, "undo_preview"):
            what = self.session.undo_preview()
            if what:
                label = f"[ ↶ Undo: {what[:40]} ]"
                t.append(label, style="bold black on cyan")
                t.append("  ")
                self.undo_button = (0, len(label))       # columns on the first footer row
        if self.prompt is not None:
            t.append(self.prompt + "▏", style="bold")
            t.append("   (Enter to confirm, Esc to cancel)", style="dim")
        else:
            t.append(FOOTER, style="dim")
        if self.messages:
            t.append("\n" + self.messages[-1], style="green")
        return t

    def _render(self) -> Layout:
        w, h = self.console.size
        root = Layout()
        root.split_column(Layout(name="head", size=1), Layout(name="body"), Layout(name="foot", size=2))
        self.footer_row = h - 1                          # 1-based screen row of the first footer line
        root["head"].update(self._header())
        if self.show_help:
            txt = Text()
            for k, v in HELP:
                txt.append(f"  {k:<7}", style="bold")
                txt.append(f"{v}\n")
            root["body"].update(Panel(txt, title="Keys (Esc or ? to close)"))
        else:
            body_h = max(5, h - 3)
            left_w = int(w * 0.58)
            root["body"].split_row(Layout(name="tx", ratio=58), Layout(name="sum", ratio=42))
            root["body"]["tx"].update(self._transcript_panel(body_h, left_w))
            root["body"]["sum"].update(self._summary_panel(body_h, w - left_w))
        root["foot"].update(self._footer())
        return root


# Distinct, readable on dark backgrounds; yellow is left for bookmarks, dim for markers.
CONT_INDENT = 11    # wrapped rows start under the speech, after "[hh:mm:ss] "
# Ordered so neighbours differ strongly (the first speakers get the most distinct pairs).
SPEAKER_COLORS = ["bright_cyan", "orange1", "bright_magenta", "chartreuse2", "deep_sky_blue1",
                  "light_coral", "medium_purple1", "gold3", "spring_green2", "hot_pink",
                  "turquoise2", "dark_orange"]


def speaker_of(line: str) -> Optional[str]:
    """'[10:00:00] Speaker Amit: hi' -> 'Amit' (the label as displayed)."""
    i = line.find("Speaker ")
    if i < 0 or i > 14:
        return None
    rest = line[i + 8:]
    j = rest.find(":")
    if j <= 0:
        return None
    return rest[:j].strip() or None


class KeyActions:
    """Maps keystrokes to actions. Shared by full-screen and plain modes.

    `notify(msg)` reports results (UI message line or plain echo). Text prompts
    (mark note, rename) are collected here byte-by-byte so the raw-mode reader
    never needs canonical input, and UTF-8 (Hebrew) input works.
    """

    def __init__(self, session, buffer, summarizer, copy: Callable[[str], bool],
                 notify: Callable[[str], None], ui: Optional[SplitView] = None) -> None:
        self.s, self.buffer, self.summ = session, buffer, summarizer
        self.copy, self.notify, self.ui = copy, notify, ui
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._prompt_label: Optional[str] = None
        self._prompt_buf = ""
        self._prompt_done: Optional[Callable[[str], None]] = None
        self._choices: list = []             # options for a pick-list prompt (arrows + Enter)
        self._choice_i = 0
        self._q_armed_at = 0.0
        self._esc: Optional[str] = None      # pending escape sequence
        self._pending_prompt = None          # (label, on_done) from a worker thread; opened in tick()
        self._pasting: Optional[list] = None # inside ESC[200~ … ESC[201~
        self._paste_parts: list = []
        self._paste_at = 0.0
        self._esc_at = 0.0

    # prompt helpers
    def _open_prompt(self, label: str, on_done: Callable[[str], None],
                     choices: Optional[list] = None, current: Optional[str] = None) -> None:
        self._prompt_label, self._prompt_buf, self._prompt_done = label, "", on_done
        self._choices = list(choices or [])
        self._choice_i = self._choices.index(current) if current in self._choices else 0
        self._show_prompt()

    def _move_choice(self, step: int) -> None:
        if self._choices:
            self._choice_i = (self._choice_i + step) % len(self._choices)
            self._prompt_buf = ""                # arrows override anything typed
            self._show_prompt()

    def _match_typed(self) -> None:
        """Typing a prefix of an option moves the highlight to it."""
        typed = self._prompt_buf.strip().lower()
        if typed:
            for i, c in enumerate(self._choices):
                if c.lower().startswith(typed):
                    self._choice_i = i
                    break

    def _show_prompt(self) -> None:
        if self._choices:
            opts = "  ".join(f"[{c}]" if i == self._choice_i else c for i, c in enumerate(self._choices))
            text = f"{self._prompt_label} ←/→: {opts}" + (f"  typed: {self._prompt_buf}" if self._prompt_buf else "")
        else:
            text = f"{self._prompt_label}: {self._prompt_buf}"
        if self.ui is not None:
            self.ui.prompt = text
        else:
            sys.stdout.write("\r\033[K" + text)
            sys.stdout.flush()

    def _close_prompt(self, submit: bool) -> None:
        done, value = self._prompt_done, self._prompt_buf
        if submit and self._choices:
            typed = value.strip().lower()
            exact = [c for c in self._choices if c.lower() == typed]
            pref = [c for c in self._choices if typed and c.lower().startswith(typed)]
            if not typed:
                value = self._choices[self._choice_i]
            elif exact:
                value = exact[0]
            elif len(pref) == 1:
                value = pref[0]
        self._choices, self._choice_i = [], 0
        if not submit:
            self._log("prompt_cancelled", {"prompt": (self._prompt_label or "")[:60], "typed": value[:60]})
        self._prompt_label = self._prompt_done = None
        self._prompt_buf = ""
        if self.ui is not None:
            self.ui.prompt = None
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if submit and done:
            done(value)
        elif not submit:
            self.notify("Cancelled.")

    PASTE_MIN = 12   # this many characters in one read cannot be typing

    def handle(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if self._pasting is None and "\x1b" not in text and len(text) >= self.PASTE_MIN:
            self._add_paste(text)          # terminal without bracketed paste: a burst = a paste
            return
        for ch in text:
            self._feed(ch)

    def _add_paste(self, text: str) -> None:
        """Collect pasted text; a paste split over several reads is joined (finished in tick())."""
        self._paste_parts.append(text)
        self._paste_at = time.time()

    def _finish_paste(self) -> None:
        text = "".join(self._paste_parts).replace("\r\n", "\n").replace("\r", "\n")
        self._paste_parts = []
        if not text.strip():
            return
        self._log("paste", {"chars": len(text), "lines": text.count("\n") + 1,
                            "into": "prompt" if self._prompt_label else "screen"})
        if self._prompt_label is not None:              # into the open prompt, as one line
            self._prompt_buf += " ".join(text.split())
            self._show_prompt()
            return
        from meetalign import parse_meeting
        turns = parse_meeting(text)
        if len(turns) >= 2:
            people = len({t.name for t in turns})
            self._open_prompt(f"Pasted a Teams/Zoom transcript ({len(turns)} turns, {people} people) — "
                              "Enter = names · T + Enter = text + names · Esc = ignore",
                              lambda v, raw=text: self._names_from_text(raw, refine=v.strip().lower() == "t"))
        else:
            self.notify("Paste ignored: pasted text never runs as keys. Paste a Teams/Zoom transcript "
                        "to match names, or open a prompt first.")

    def tick(self) -> None:
        """Called when no input arrived for a moment: a lone ESC becomes a real Esc key."""
        if self._paste_parts and self._pasting is None and time.time() - self._paste_at > 0.25:
            self._finish_paste()
        pending, self._pending_prompt = self._pending_prompt, None
        if pending is not None:
            if self._prompt_label is None:
                self._open_prompt(*pending)
            else:
                self._pending_prompt = pending   # wait until the user closes the current prompt
        if self._esc == "\x1b" and time.time() - self._esc_at > 0.15:
            self._esc = None
            self._handle_char("\x1b")

    def _feed(self, ch: str) -> None:
        if self._esc is None:
            if ch == "\x1b":
                self._esc, self._esc_at = ch, time.time()
                return
            if self._pasting is not None:
                self._pasting.append(ch)
                return
            self._handle_char(ch)
            return
        seq = self._esc + ch
        if seq == "\x1b\x1b":          # double Esc = Esc
            self._esc = None
            self._handle_char("\x1b")
            return
        if len(seq) == 2 and ch not in "[O":
            self._esc = None             # Esc followed by a normal key
            self._handle_char("\x1b")
            self._handle_char(ch)
            return
        if len(seq) > 2 and (ch.isalpha() or ch == "~" or len(seq) > 32):
            self._esc = None
            live_before = self.ui is None or self.ui.tx_top is None
            self._handle_sequence(seq)
            live_after = self.ui is None or self.ui.tx_top is None
            if live_before != live_after:        # log leaving / returning to live, not every row
                self._log("scroll", {"transcript": "live" if live_after else "scrolled back"})
            return
        self._esc = seq

    def _handle_sequence(self, seq: str) -> None:
        if seq == "\x1b[200~":                   # bracketed paste start
            self._pasting = []
            return
        if seq == "\x1b[201~":                   # bracketed paste end
            text, self._pasting = "".join(self._pasting or []), None
            self._add_paste(text)
            self._paste_at = 0.0                 # complete: finish on the next tick
            return
        if self._pasting is not None:
            self._pasting.append(seq)
            return
        # SGR mouse: ESC [ < button ; x ; y (M press | m release)
        if seq.startswith("\x1b[<") and seq[-1] in "Mm":
            try:
                b, x, y = (int(p) for p in seq[3:-1].split(";"))
            except ValueError:
                return
            if seq[-1] == "M" and b == 0:
                self.click(x, y)
            elif seq[-1] == "M" and b in (64, 65) and self.ui is not None:   # wheel up / down
                panel = "tx" if x <= self.ui.transcript_width else "sum"
                self.ui.scroll(panel, -3 if b == 64 else 3)
            return
        if self._prompt_label is not None and self._choices:    # pick-list: arrows move the highlight
            if seq in ("\x1b[A", "\x1bOA", "\x1b[D", "\x1bOD"):
                self._move_choice(-1)
                return
            if seq in ("\x1b[B", "\x1bOB", "\x1b[C", "\x1bOC"):
                self._move_choice(1)
                return
        ui = self.ui
        if ui is None:
            return
        # Scrolling stays available while a prompt is open (e.g. to find a line for
        # off-the-record); typed characters still go to the prompt.
        page = lambda: max(1, (ui.tx_page if ui.focus == "tx" else ui.sum_page) - 2)
        if seq == "\x1b[5~":                      # Page Up
            ui.scroll(ui.focus, -page())
        elif seq == "\x1b[6~":                    # Page Down
            ui.scroll(ui.focus, page())
        elif seq in ("\x1b[A", "\x1bOA"):         # arrow up / down: one row
            ui.scroll(ui.focus, -1)
        elif seq in ("\x1b[B", "\x1bOB"):
            ui.scroll(ui.focus, 1)
        elif seq in ("\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~"):   # Home
            ui.scroll_home(ui.focus)
        elif seq in ("\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~"):   # End
            ui.scroll_end(ui.focus)
        # other sequences are ignored

    def click(self, x: int, y: int) -> None:
        """Left click: on a transcript line with a speaker → rename that speaker.
        While the off-the-record prompt is open, a click on any line picks that line's time."""
        ui = self.ui
        if ui is None:
            return
        if (self._prompt_label is None and ui.undo_button and y == ui.footer_row
                and ui.undo_button[0] < x <= ui.undo_button[1]):
            r = self.s.undo(source="mouse")
            if not r.get("ok"):
                self.notify(r.get("error", "Nothing to undo."))
            return
        if x > ui.transcript_width:
            return
        if self._prompt_done == self._off_record:
            for first, last, text in ui.row_map:
                if first <= y <= last:
                    m = re.search(r"\d{1,2}:\d{2}:\d{2}", text)
                    if m:
                        self._prompt_buf = m.group(0)
                        self._show_prompt()
                        self._log("pick_line_time", {"time": m.group(0)})
                        self.notify(f"From {m.group(0)} on: {self._count_since(m.group(0))} "
                                    "lines will be deleted. Enter to confirm, Esc to cancel.")
                    else:
                        self.notify("That line has no time — pick another line.")
                    return
            return
        if self._prompt_label is not None:
            return
        for first, last, label in ui.click_map:
            if first <= y <= last:
                self._open_prompt(f"Rename speaker \u201c{label}\u201d to",
                                  lambda v, src=label: self._rename_to(src, v, "mouse"))
                return

    def _rename_to(self, src: str, dst: str, source: str = "key") -> None:
        dst = dst.strip()
        if not dst:
            self.notify("Cancelled (empty name).")
            return
        n = self.s.rename_speaker(src, dst, source) if hasattr(self.s, "rename_speaker") else self.buffer.rename_speaker(src, dst)
        self.notify(f"Renamed {src} → {dst} ({n} past lines).")

    def _handle_char(self, ch: str) -> None:
        if self._prompt_label is not None:
            if ch in ("\r", "\n"):
                self._close_prompt(True)
            elif ch == "\x1b":
                self._close_prompt(False)
            elif ch in ("\x7f", "\x08"):
                self._prompt_buf = self._prompt_buf[:-1]
                self._match_typed()
                self._show_prompt()
            elif ch.isprintable():
                self._prompt_buf += ch
                self._match_typed()
                self._show_prompt()
            return
        if ch == "\x1b":                      # Esc closes the help screen
            if self.ui is not None and self.ui.show_help:
                self.ui.show_help = False
                self._log("help", {"open": False})
            return
        if ch == "\t" and self.ui is not None:
            self._log("focus", {"panel": "summary" if self.ui.focus == "tx" else "transcript"})
            f = self.ui.toggle_focus()
            self.notify(f"{'Summary' if f == 'sum' else 'Transcript'} panel: PgUp/PgDn, ↑/↓, Home/End, mouse wheel.")
            return
        # legacy control keys keep working
        mapping = {"\x10": " ", "\x13": "c", "\x05": "e", "\x12": "r"}
        ch = mapping.get(ch, ch)
        if ch == " ":
            self.s.toggle_pause("key")
        elif ch == "m":
            self.s.mark(source="key")
        elif ch == "M":
            self._open_prompt("Mark note", lambda v: self.s.mark(v, "key"))
        elif ch == "u":
            self.s.toggle_mic("key")
        elif ch == "z":
            r = self.s.undo(source="key")
            if not r.get("ok"):
                self.notify(r.get("error", "Nothing to undo."))
        elif ch == "c":
            self.buffer.copy_full_to_clipboard()
            self._log("copy_transcript", {"part": "full"})
        elif ch == "e":
            self.buffer.copy_delta_to_clipboard()
            self._log("copy_transcript", {"part": "new"})
        elif ch in ("s", "a", "d"):
            self._copy_summary(ch)
        elif ch == "n":
            if self.summ is None:
                self.notify("Live summary is off (start with --summary).")
            else:
                self.summ.request_now()
                self._log("summarize_now", {})
                self.notify("Updating summary…")
        elif ch == "r":
            known = ", ".join(self.buffer.known_speakers()) or "none yet"
            self.notify(f"Known speakers: {known}")
            self._open_prompt("Rename (e.g. 0=Amit)", self._rename)
        elif ch == "t":
            self._names_from_clipboard()
        elif ch == "T":
            self._names_from_clipboard(refine=True)
        elif ch == "k":
            q = self.s.quality() if hasattr(self.s, "quality") else {}
            self._open_prompt(f"How many people in this call? (now {q.get('voices', '?')} voices; 0 = unknown)",
                              self._expected_speakers)
        elif ch == "o":
            self._open_prompt("Off the record — minutes, or a line's time; click a line to pick it (Enter = 2)",
                              self._off_record)
            self.notify("Scroll up (wheel / PgUp) and click the first line to delete, or paste its time.")
        elif ch in ("[", "]", "\\"):
            val = {"[": "-", "]": "+", "\\": "toggle"}[ch]
            try:
                self.s.set_chunk(val, "key")
            except ValueError as exc:
                self.notify(str(exc))
        elif ch == "N":
            self._open_prompt("New session — name (Enter = no name, Esc = cancel)",
                              self._new_session)
        elif ch == "b":
            self._open_prompt("Switch backend", self._switch, choices=list(self.s.BACKENDS),
                              current=getattr(self.s, "backend", None))
        elif ch == "q":
            now = time.time()
            if now - self._q_armed_at < 3:
                self.notify("Stopping…")
                self.s.stop("key")
            else:
                self._q_armed_at = now
                self._log("stop_armed", {})
                self.notify("Press q again within 3 s to stop the session.")
        elif ch == "?":
            if self.ui is not None:
                self.ui.show_help = not self.ui.show_help
                self._log("help", {"open": self.ui.show_help})
            else:
                self.notify(" · ".join(f"{k} {v}" for k, v in HELP))

    def _new_session(self, name: str) -> None:
        # Closing the old session finishes audio files; keep the keyboard responsive meanwhile.
        self.notify("Starting a new session…")
        threading.Thread(target=self.s.new_session, args=(name, "key"), daemon=True).start()

    def _invalid(self, what: str, raw: str, msg: str) -> None:
        self._log("invalid_input", {"for": what, "typed": raw[:60]})
        self.notify(msg)

    def _log(self, action: str, params: dict) -> None:
        led = getattr(self.s, "ledger", None)
        if led is not None:
            led.record(action, "key", params)

    def _names_from_clipboard(self, refine: bool = False) -> None:
        import subprocess
        try:
            raw = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
        except Exception as exc:
            self.notify(f"Could not read the clipboard: {exc}")
            return
        self._names_from_text(raw, refine)

    def _names_from_text(self, raw: str, refine: bool = False) -> None:
        self.notify(("Refining text and names" if refine else "Matching names")
                    + " from the copied transcript (Copilot for short lines)…")

        def work():
            prev = self.s.match_meeting_names(raw, refine=refine)
            self._log("names_preview", {"ok": bool(prev.get("ok")), "summary": prev.get("summary"),
                                        "error": prev.get("error"), "ai_error": prev.get("ai_error"),
                                        "paste_chars": len(raw)})
            if not prev.get("ok"):
                self.notify(prev.get("error", "No names found."))
                return
            if not prev["changes"]:
                self.notify(f"No RTT lines matched {', '.join(prev['names'])} — is it the same time range?")
                return
            note = f" (AI skipped: {prev['ai_error']})" if prev.get("ai_error") else ""
            self.notify(prev["summary"] + note)
            # Prompt state belongs to the key thread: tick() opens it there.
            learn = prev.get("learn") or []
            if learn:
                words = ", ".join(f"{h}→{r}" for h, r in learn[:6]) + (" …" if len(learn) > 6 else "")
                self.notify(prev["summary"] + note + f" · new words: {words}")
                label = "Apply? Enter = yes · L + Enter = yes and learn the new words · Esc = no"
            else:
                label = "Apply? Enter = yes, Esc = no"
            self._pending_prompt = (label, lambda v: self._apply_names(prev, learn=v.strip().lower() == "l"))
        threading.Thread(target=work, daemon=True).start()

    def _apply_names(self, prev: dict, learn: bool = False) -> None:
        r = self.s.apply_meeting_names(prev, "key", learn=learn)
        if not r.get("ok"):
            self.notify(r.get("error", "Names not applied."))

    def _expected_speakers(self, raw: str) -> None:
        try:
            self.s.set_expected_speakers(int(raw.strip() or 0), "key")
        except ValueError:
            self._invalid("expected_speakers", raw, "Enter a number of people, e.g. 2 (0 = unknown)")

    def _count_since(self, raw: str) -> int:
        try:
            kw = self.s.parse_off_record(raw)
            cutoff = kw["since"] if "since" in kw else time.time() - kw["minutes"] * 60
            times = getattr(self.buffer, "_times", [])
            return sum(1 for t in list(times) if t >= cutoff)
        except Exception:
            return 0

    def _off_record(self, raw: str) -> None:
        try:
            kw = self.s.parse_off_record(raw)
        except ValueError as exc:
            self._invalid("off_the_record", raw, str(exc))
            return
        self.s.off_the_record(source="key", **kw)
        if self.ui is not None:
            self.ui.scroll_end("tx")

    def _switch(self, raw: str) -> None:
        try:
            self.s.request_switch(raw, "key")
        except ValueError as exc:
            self._invalid("switch_backend", raw, str(exc))

    def _rename(self, raw: str) -> None:
        if "=" not in raw:
            self._invalid("rename", raw, "Use src=dst, e.g. 0=Amit")
            return
        src, _, dst = raw.partition("=")
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            self._invalid("rename", raw, "Both sides are required, e.g. 0=Amit")
            return
        self._rename_to(src, dst, "key")

    def _copy_summary(self, which: str) -> None:
        if self.summ is None:
            self.notify("Live summary is off (start with --summary).")
            return
        from summarizer import render_sections
        secs = self.summ.sections
        if which == "s":
            text, label = render_sections(secs), "summary"
        elif which == "a":
            text, label = "\n".join(f"- {i}" for i in secs.get("Action items", [])), "action items"
        else:
            text, label = "\n".join(f"- {i}" for i in secs.get("Decisions", [])), "decisions"
        if not text.strip():
            self.notify(f"No {label} yet.")
        elif self.copy(text):
            self._log("copy_summary", {"part": label})
            self.notify(f"Copied {label}.")
