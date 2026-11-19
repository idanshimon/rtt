# Paste safety and tidy

Status: shipped 2026-11-10.

## Why
On 2026-11-10 a Teams transcript was pasted (Cmd+V) into the RTT window three times. RTT read it one
byte at a time, so every letter ran as a key: in 55 s, 386 actions — 117 bookmarks, 41 pauses and
resumes, mic muted (and left muted), 83 summary copies, 43 transcript copies, and two wrong name
matches (`c` put RTT's own transcript on the clipboard, then `t` matched it as if it were Teams).

## Behavior
- Pasted text never runs as keys.
  - Bracketed paste is turned on (`ESC[?2004h`, off on exit): terminals wrap a paste in
    `ESC[200~ … ESC[201~`, and RTT collects it as one paste.
  - Terminals without bracketed paste: input is read in blocks; 12+ characters in one read cannot
    be typing and are a paste. A paste split over several reads is joined (finished after 0.25 s idle).
- With a prompt open, the paste goes into the prompt as one line (newlines become spaces).
- Otherwise, if it is a Teams/Zoom transcript (2+ turns), RTT asks: `Pasted a Teams/Zoom transcript
  (N turns, M people) — Enter = names · T + Enter = text + names · Esc = ignore`. So pasting into RTT
  is now another way to do `t` / `T` (no clipboard round-trip).
- Any other paste is ignored with a message.
- Each paste is logged (`paste`: chars, lines, into prompt or screen).
- Tidy: `rtt-ctl tidy` removes pause/resume/mute/unmute/bookmark lines that came in bursts (4+
  within 3 s of each other; speech in between is ignored and never removed), plus the bookmarks and
  sub-second pauses behind them. Undoable (`tidy`). After a session ended:
  `rtt-ctl tidy --file transcripts/transcript-….txt`.

## Interfaces
`rtt-ctl tidy [--file F]`. No new keys.

## Implementation
`ClipboardHotkeyListener._run_posix` (block reads, bracketed paste on/off; `restore_terminal` turns
it off), `KeyActions.handle` / `_add_paste` / `_finish_paste` / `tick`, `_handle_sequence`
(200~/201~), `_names_from_text`; `session.burst_marker_indices`, `tidy_file`, `Session.tidy`,
`TranscriptBuffer.remove_indices` / `insert_lines`.

## Verification
tests/test_paste.py: bracketed paste runs no keys and offers names; split paste and burst without
markers; paste into a prompt; plain text ignored; Enter on the paste prompt runs names; typing still
works. tests/test_tidy.py: bursts found, lone pause and real bookmark kept, speech kept, live tidy +
undo, file tidy. Real PTY run of the CLI (isolated socket, file input) with the exact 2026-11-10 paste,
bracketed and as a plain burst: prompt shown, actions = session_start, paste, prompt_cancelled, stop;
mic on, 0 marks. Dry run on the real 2026-11-10 transcript: 186 burst lines found (14:47:40–14:48:12),
0 speech lines.

## Limits
A paste containing escape characters in a terminal without bracketed paste falls back to key
handling. The live session from 2026-11-10 was restored by hand (unmute, 2 name undos, 117 bookmark
undos); its burst lines need `rtt-ctl tidy --file` after it ends (that RTT predates `tidy`).
