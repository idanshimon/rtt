# Off the record

Status: shipped 2026-11-06.

## Why
Something said in a meeting should not be kept: remove it everywhere RTT stored it.

## Behavior
`o` opens a prompt. It takes either:
- minutes: `2`, `2m`, `90s` (Enter alone = 2), or
- a line's time: `09:15:09`, `9:15`, `[09:15:09]`, or a whole pasted line. That line and
  everything after it are deleted. A time later than now means yesterday.
While the prompt is open you can scroll the transcript (wheel, PgUp/PgDn, arrows, Home/End) and
click any line: its time fills the prompt and the message line says how many lines will go.
Enter confirms, Esc cancels. Same from a script: `rtt-ctl offrecord 3`, `rtt-ctl offrecord 2min` or
`rtt-ctl offrecord 09:15:09` (anything not a plain number is parsed by the session).
Then:
1. Pause.
2. Delete transcript lines spoken in the last N minutes (file rewritten), bookmarks in that window,
   the audio of every track from that moment on, and the backend's buffered (not yet sent) audio.
   Audio is cut by capture time through `audio_index.json`, so pauses (nothing recorded) don't
   shift the cut, and a cut reaching before a resume also trims the earlier runs' FLAC on close.
   When everything is cut, no audio file is left.
3. Rebuild the summary from what remains.
4. While still paused, drop any speech that returns late from chunks already sent; system markers
   still pass.
Logged in the ledger, not undoable (the material is gone by design).

## Implementation
`Session.parse_off_record` (minutes or time → `minutes=` / `since=`),
`Session.off_the_record(minutes, source, since=None)`, `KeyActions.click` (picks the time when the
off-record prompt is open; uses `SplitView.row_map`), `TranscriptBuffer.redact_since` (uses spoken times),
`AudioRecorder.redact_since` (+ `position_at`, index), `Session.accept_line`.

## Verification
tests/test_session_more.py: text, file, marks, audio length (5 s recorded, 2 s cut → 3 s) and
in-flight lines. tests/test_off_record_pick.py: parsing, delete-from-time, scroll + click a line +
Enter, typed time and bad input. Real PTY run: 22 lines removed, summary rebuilt.

tests/test_audio_redact.py: cut across a 60 s pause, crash WAV kept, cut into an earlier run,
everything cut.

## Limits
Audio cut precision is about ±0.5 s (index every second, interpolated).
Copies already pasted elsewhere, and text already sent to the summary model, cannot be recalled.
