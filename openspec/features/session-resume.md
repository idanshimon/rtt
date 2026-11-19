# Session resume

Status: shipped 2026-11-09.

## Why
A meeting continues after RTT was stopped (break, crash, laptop closed). Starting fresh splits one
meeting into two folders and loses speaker names. Pick the same session up again, like `hermes --resume`.

## Behavior
- On every exit RTT prints:
  ```
  Resume this session with:
    rtt --resume 20261109-100352
    rtt -c "Team weekly"        (when the session has a name or meeting title)
    rtt -c                      (latest session)
  Session / Title / Duration / Lines
  ```
- `--resume <id>` reopens that folder. `--resume` or `-c` with no value = the most recently active
  stopped session (latest of start, end, resume).
  `-c <text>` = the latest session whose folder name, name or meeting title contains the text
  (an id prefix also works).
- A session another RTT is still running is never picked; naming it explicitly is an error. A
  session with `ended_at` is stopped even if its process lives on (closed with `N`).
- Resumed: same folder, same transcript file (appended), lines back in memory (copy, off the record
  and undo work across the gap), rename map, speaker voice prints (Speaker 1 is still the same
  person and keeps their name), marks, pauses, redactions, meeting title, backend history, ledger
  (ids continue), the summary (continues from where it stopped). Lines come back with their raw
  labels and exact times from `lines.json`; only if that file is older than the transcript (crash)
  are they rebuilt from the transcript file by reversing the rename map (lossy when two labels were
  renamed to one name). Audio: a `<track>.wav` left by a crash is folded into `<track>.flac` first;
  new audio is appended on close (the gap itself is not in the audio).
- `z` / undo only reaches actions from this run; earlier entries stay in the ledger but can't be undone.
- Duration and the status-bar clock count active time (gaps between stop and resume excluded).
- A new meeting title replaces the old title in the folder name.
- A `[▶ RTT session resumed — stopped at HH:MM:SS]` marker goes into the transcript; session.json
  gets a `resumes` entry and the ledger a `session_resume` action.
- The backend comes from the command used (`rtt -c` = openai, `rttheb -c` = llmspeech, …).

## Interfaces
`--resume [id]`, `-c/--continue [text]` (transcribe.py; `Field("resume")` in config_schema.py).
Not allowed with `--input-file`.

## Files and data
session.json gains `pid`, `speaker_map`, `resumes`, `expected_speakers`. `sessions/<id>/speakers.npz`
holds voice centroids and counts (close, new session). `lines.json` = `[[t, raw line], …]` (every
session save, close and force quit). `audio_index.json` = per track `[[wall time, audio seconds], …]`
for cutting audio by time (written on every session save and on close, so a crash keeps it).

## Implementation
`session.find_session`, `session.session_is_running` (pid alive and a transcribe.py; for older
sessions without a pid: never closed and a transcribe.py process started within 5 s of it),
`Session(resume_dir=...)`, `Session.save_voices/load_voices`, `Ledger` loads existing entries,
`AudioRecorder.stop` appends to existing FLAC, `TranscriptBuffer.load_existing` (undoes the rename
map so lines hold raw labels again; times from `[HH:MM:SS]` on the start day, rolling past midnight),
`LiveSummarizer.resume_from`, `transcribe.resume_hint`, `_normalize_resume_argv` (value-less flags).
A resumed folder already named after its meeting is not renamed twice.

## Verification
tests/test_resume.py: argv, find (latest / id / prefix / title), full stop-then-resume round trip
(lines, raw labels, names, voices, marks, ledger ids, audio 1 s + 0.5 s, no double rename, hint),
summary continuation, live sessions skipped (real child process), pid-less older sessions.
Real CLI run 2026-11-09: `rtt --resume 20261109-100352` with live BlackHole capture: 4 lines and
1 voice restored, new speech by the known voice came out as the renamed "Amit", marks and ledger
continued, exit printed the resume block.

Review fixes 2026-11-09 (independent review): crash WAV kept, ended_at wins over a live pid,
lossless raw lines, latest by activity, undo barrier, active duration, title slug replaced; second review: audio position after a cut into an earlier
run, `N` clears resume gaps —
tests/test_resume.py, tests/test_audio_redact.py.

## Limits
Voice matching can mistake a new
person for a known voice (same threshold as live tracking). Chunk window and mute state are not restored.
