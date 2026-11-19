# Action ledger and undo

Status: shipped 2026-11-06.

## Why
User corrections (who a speaker is, what was removed, what changed) should be recorded so they can
be reverted, and so an agent reading the session knows what the user fixed.

## Behavior
- `sessions/<id>/actions.jsonl`, append-only, one JSON object per action:
  `{id, at, action, source, params, undo?}`. Sources: key, mouse, ctl, ctrl-c, system.
- Actions: session_start, pause, resume, mute, unmute, mark, rename_speaker, chunk,
  switch_backend, new_session, off_the_record, copy_transcript, copy_summary, summarize_now, stop, undo,
  session_resume (a resumed session; ids continue from the saved file), expected_speakers (`k`),
  meeting_names (`t`), meeting_refine (`T`, undoable; learned words stay), tidy (undoable), paste, names_preview (each `t` result, incl. errors), and screen actions (not
  undoable): help (open/close), focus (Tab), scroll (only when the transcript leaves live view or
  returns to it), pick_line_time (click in the off-the-record prompt), prompt_cancelled (with what
  was typed), invalid_input (which prompt, what was typed), stop_armed (first q).
- Undo button: the first footer line starts with `[ ↶ Undo: <what> ]` (e.g. `rename 4 → Leila`,
  `pause`, `names from Teams/Zoom`) whenever something is undoable; clicking it = `z`
  (logged with source `mouse`). Hidden while a prompt is open. `Session.undo_preview()`.
- Undo: `z` or the Undo button (last), `rtt-ctl undo [id]`. Reversible: rename (previous speaker map; summary rebuilt),
  mark (mark and its line removed), mute, pause, chunk, switch, meeting_names (lines that still
  hold the applied text go back; speaker map restored). Not reversible: off_the_record (by design),
  expected_speakers (set `k` again; 0 = unknown).
- Undo appends `{"action": "undo", "params": {"target": id}}`; nothing is deleted. Side effects of
  an undo are not logged as new undoable actions. "Undo last" skips actions whose effect is
  already gone (a pause already resumed, a mute already undone).
- `rtt-ctl log` prints the ledger with `[undoable]` / `(undone)`.

## Implementation
`session.Ledger`, `Session._rec`, `Session.undo` / `_apply_undo` / `_undo_applicable`.

## Verification
tests/test_undo_button.py: button hidden/shown with the right text, click undoes (source mouse), click
beside it does nothing, every screen action logged and none of them undoable. Render check: button
drawn on the same screen row the click handler uses.
tests/test_ledger.py (sources, order, undo of each kind, no undo ping-pong, specific id, already
undone, off_the_record not undoable, rtt-ctl log/undo). Real PTY run: ledger with mouse rename,
switch, off-record; `z` undid the switch and skipped the stale pause.
