# Names from the meeting app's transcript

Status: shipped 2026-11-09.

## Why
Teams and Zoom know who is speaking; RTT only hears voices. In a real 8-person call (2026-11-09)
RTT had 15 labels for ~8 people, and some labels mixed two people. Copying a few minutes of the
Teams transcript is enough to put real names on RTT's lines.

## Behavior
- Copy the Teams/Zoom transcript (any part, by mouse), press `t` in RTT. RTT reads the clipboard,
  matches, shows e.g. `147/155 lines named (56 by words, 77 by label, 14 by AI) · Ajay 48, …`,
  and asks `Apply these names? Enter = yes, Esc = no`.
- Per line, not per label: each RTT line in the pasted time range gets the name of the meeting-app
  turn at the same time (minute ±75 s) with the most shared words and word pairs. A label that
  splits two people is therefore fixed line by line.
- A label whose confident lines point to one name (2+ lines, 75%+) is renamed too, so its later
  lines carry the name without another paste, and its undecided lines in the range get it.
- Lines still undecided (short "Yeah.", "part") go to Copilot (same auth/model as the live summary)
  with the pasted turns; only answers that are one of the pasted names are used; the rest stay as is.
- One undoable ledger entry (`meeting_names`); `z` restores the lines that still hold the applied
  text, removes only the label names it set (later renames stay), and rebuilds the summary.
- If the transcript changed so that no previewed line still matches (e.g. off the record), nothing
  is applied: "press t again".
- In Teams-copy text, a line like `Action Item: …` inside a turn stays text, not a new speaker.
- `rtt-ctl names [file|-] [--no-ai] [--dry-run]` (clipboard when no file).

## Formats
Teams copy (`Name` / `11:39 AM` / text), Teams .vtt (`<v Name>`), `[11:39:02] Name: text`,
`Name: text`, `[Name] 11:39:02` + text. AM/PM and midnight (11:59 PM → 12:00 AM) handled.

## Implementation
`names_match.py` (parse, `match_lines`, `ai_resolve`, `copilot_call`), `Session.match_meeting_names`
/ `apply_meeting_names`, `TranscriptBuffer.raw_lines/replace_lines`, `KeyActions._names_from_clipboard`.
Renaming a name that was written into lines (e.g. "Dana Levi" → "Dana") also renames those lines.

## Verification
tests/test_names_match.py (formats, mixed label fixed per line, AI answers limited to pasted names,
`t` → Enter → `z`). Real 2026-11-09 call (private/names-match/, not committed): 155 RTT lines in
range, 133 named by words/label, +14 by Copilot (147), 8 left unnamed; spot-checked, including
label 13 split between Nicholas and Nathan.

The `t` worker thread hands the confirm prompt to the key thread (opened in `tick()`), so prompt
state is only touched there.

## Limits
Two identical lines with the same time are not told apart by the stale check.
Only the pasted time range is matched by words; outside it only label renames apply. RTT and the
meeting app clocks must agree to about a minute. Teams' own speaker attribution is trusted.
