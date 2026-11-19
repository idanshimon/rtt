# Transcription quality: catch it live, adapt

Status: shipped 2026-11-09.

## Why
A 2-person call (Tim walking outdoors in New York, 2026-11-09) came out with 11 speaker labels,
8% "?" lines and 45% one-or-two-word fragments. The words were mostly right; who-said-what was not.
It was already visible in the first minute, but nothing said so.

## Behavior
- Status bar warnings (yellow), each also written once to the message line, from the last 60 lines
  (after at least 20):
  - `N voices — how many people? press k` when 4+ voices and the number of people is not set;
  - `choppy (X% 1–2 word lines) — try ] bigger chunk` at 40%+;
  - `X% unknown speaker` at 12%+.
- `k` (or `rtt-ctl speakers N`, `--speakers N`, `RTT_SPEAKERS`) sets how many people are in the call.
  The voice tracker then never creates a new voice; an unmatched voice goes to the closest allowed
  one. If more voices already exist, only the N most-heard stay matchable. 0 = unknown again.
  Logged in the ledger; saved in session.json and restored on resume.
- Labels already split before `k` can be merged by renaming them to the same name.
- `rtt-ctl quality` returns the numbers.
- Esc closes the help screen.

## Interfaces
Key `k`; `rtt-ctl speakers <n>`, `rtt-ctl quality`; `--speakers N` / `RTT_SPEAKERS`.

## Implementation
`Session.quality`, `Session.set_expected_speakers`, `SpeakerTracker.set_max_speakers` /
`_candidates` / `allowed`, `SplitView._header`, `KeyActions` (`k`, Esc).

## Verification
tests/test_quality.py: Esc, cap + lock to most-heard, `k` prompt, clean call stays quiet, and a
replay of the real call's first 3.5 min (tests/fixtures/call-head.txt: labels, times and line
lengths only; words replaced) — first warning at 10:37:43, 27 s into the call.

The people count stays set across `N` (new session in the same call); set `k` again to change it.

## Limits
The thresholds come from one bad call and one synthetic clean one. The cap fixes who-said-what, not
fragmenting or noise. Recording (`--record`) is what allows a better second pass afterwards with `rtt-post`.
