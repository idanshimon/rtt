# Live summary

Status: shipped 2026-11-05.

## Why
During a meeting there was no running picture of what was decided and who owes what.

## Behavior
- Right panel of the split view: Summary · Decisions · Action items · Open questions.
- Incremental: each update sends the current summary plus only the new lines; every
  `RTT_SUMMARY_INTERVAL` seconds (60) or on `n`. A final update runs at stop (bounded, see
  stopping-and-shutdown). Errors are shown; the last good summary is kept; transcription is never
  affected.
- Copy: `s` whole summary, `a` action items, `d` decisions.
- Prompt rules: transcripts are ASR (errors, generic labels); numbered speakers are tracked by
  voice; "Speaker ?" is unidentified; bracketed RTT lines are not speech; bookmarks mark important
  moments; never invent owners, dates or numbers; write in the meeting's main language.
- Speaker renames rewrite existing bullets (see speaker-tracking).

## Interfaces
`--summary/--no-summary` (on), `RTT_SUMMARY`, `RTT_SUMMARY_MODEL` (gpt-6.1-terra),
`RTT_SUMMARY_INTERVAL` (60), `RTT_COPILOT_GH_USER` (empty = the active `gh` account), `RTT_COPILOT_URL`.

## Implementation
`summarizer.LiveSummarizer`: GitHub Copilot `POST /responses` (the model is not available on
`/chat/completions`, which returns 400), token from `gh auth token -u <user>` (never stored),
refreshed on 401. `parse_sections` / `render_sections` tolerate small format drift.
`apply_rename`, `rebuild` (from the full transcript, used after off-the-record and rename undo).
Output: `sessions/<id>/summary.md`.

## Verification
6–8 s per update on a 10-minute Hebrew 4-speaker transcript. Real check that a speaker named
Mark and bookmarks are kept apart. Unit tests: section parsing, incremental update with failure,
rename application.

## Limits
Needs network and a Copilot-entitled gh account. Quality follows the transcript quality.
