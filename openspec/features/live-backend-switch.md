# Live backend switch

Status: shipped 2026-11-06.

## Why
Compare backends or recover from a failing one without stopping the meeting.

## Behavior
`b` (pick list: arrows move, Enter picks, Esc cancels; typing a name or prefix also works) or `rtt-ctl switch <openai|llmspeech|mai|vibe|azure|local>`. The new backend is built
first (failure keeps the current one); the old one sends its buffered audio and stops; the new one
reads the same capture queue. Marker `[⇄ backend a → b]`; history in session.json. Chunk policy and
speaker tracker carry over. `vibe` starts its server if needed. Undoable (switches back).

## Implementation
`build_worker(backend, worker_stop)` in transcribe.py, one stop event per worker; main loop takes
`Session.take_switch_request()`; capture queue headroom ~60 s.

## Verification
tests: request validation, marker. Real PTY run: openai → llmspeech mid-session, transcription
continued; undo returned to openai.

## Limits
Audio captured during the handover is buffered, not lost, but the first chunk of the new backend
may be late by the handover time.
