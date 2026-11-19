# Session controls

Status: shipped 2026-11-05 (branch feat/vibevoice-local).

## Why
RTT started fast but gave no control over a running session: pause was a hidden Ctrl+P, nothing
showed state, nothing outside the terminal could drive it.

## Behavior
- One `Session` (`session.py`) owns state and actions; keys, mouse, `rtt-ctl` and the meeting
  watcher all call the same methods, so behaviour is identical whatever the surface.
- Pause / resume (space, `rtt-ctl pause|resume|toggle`): queued audio is dropped while paused;
  nothing is transcribed or recorded. Marker lines `[⏸ RTT paused]` and
  `[▶ RTT resumed — paused A–B]`.
- Mute (u, `rtt-ctl mute|unmute`): silences only your mic (mixed mode); others keep being
  transcribed; mic track not recorded while muted. Marker `[🔇 mic muted — transcribing others only]` / `[🎙 mic unmuted]`.
- Bookmarks (m, M with note, `rtt-ctl mark [note]`): line `[★ RTT bookmark: note]`. Bracketed like
  every system marker so it is never read as speech or as a person (it used to be `★ MARK:`; a
  speaker named Mark made that ambiguous). The summarizer prompt says bracketed RTT lines are not
  speech.
- New session without restarting (`N` → name prompt, Enter = no name, Esc = cancel;
  `rtt-ctl new [name]`): capture, backend, chunk and language settings keep running. The old
  session is closed properly — pause closed, audio finished (FLAC), `ended_at`, folder named from
  the meeting title, and its summary finished in the background from a snapshot of its own lines
  (the last good summary is written first, so a failed final call loses nothing). The new session
  gets a new folder, ledger, transcript file (`transcripts/transcript-<time>.txt`, created at
  once so livertt follows it), empty summary, forgotten speaker voices and renames, no marks, live
  (not paused). First line `[✦ RTT new session <id> — previous: <id>]`. Ledger: `new_session` at
  the end of the old ledger, `session_start` with `previous` at the start of the new one. The
  calendar title lookup restarts.
- Status (`rtt-ctl status`): JSON with state, elapsed, backend, chunk, speakers, recording, mic,
  marks, backend health, last-text age, summary state, meeting title.

## Interfaces
- Control socket `$RTT_RUN_DIR/rtt.sock` (default `~/.rtt/run`), mode 0600, JSON line protocol
  `{"cmd": ...}`; refuses to replace a socket that a live session still answers on.
- `rtt-ctl` (rtt_ctl.py / rtt-ctl.sh): see openspec/README.md for the command list.
- `RTT_FILE_REALTIME=1` plays `--input-file` at speaking speed (needed to exercise live controls on
  a recording: in fast file mode the input ends in seconds and stops the key listener).

## Files and data
`sessions/<YYYYMMDD-HHMMSS>[-name]/session.json` holds pauses, marks (with epoch `t`), backend
history, redactions, chunk policy, speaker stats, audio paths, meeting title, `ended_at`, and since
2026-11-09 `pid` (owner process, used by resume), `speaker_map`, `expected_speakers`, `resumes`.
`speakers.npz` holds voice prints (written on close and new session) so a resumed session keeps them. The live
transcript stays at `transcripts/transcript-*.txt` (livertt depends on that path).

## Implementation
`Session` (state, actions, `_rec` → ledger), `ControlServer`, `send_command`, `TranscriptBuffer`
(transcribe.py: lines, per-line spoken times, speaker map, redaction, rewrite).

## Verification
tests/test_new_session.py (rollover: old folder complete with audio, summary, ended_at and
ledger; new folder, transcript file, empty map, hook; `N` key with a Hebrew name; `rtt-ctl new`).
Real PTY run: `N` "פגישה-שנייה" then `rtt-ctl new third` — three folders, each with its own
transcript file (13 / 9 / 2 lines), ended_at and a Hebrew summary; elapsed time reset in the
status bar. tests/test_session.py (markers, marks/mute, socket roundtrip, key mapping incl. Hebrew input,
q q stop), tests/test_session_more.py. Real PTY runs of run-openai.sh and run-filetest.sh with the
screen rendered by pyte; `rtt-ctl status` against the live session.

## Limits
One live session per `RTT_RUN_DIR`. Tests and parallel sessions must use their own RTT_RUN_DIR.
