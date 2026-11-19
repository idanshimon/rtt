# Refine the transcript from the meeting app's transcript (`T`)

Status: shipped 2026-11-09. Builds on meeting-app-names (`t`).

## Why
Teams/Zoom hear each person from their own microphone, so their words are usually better than
RTT's mixed audio: in a real call RTT wrote "Urban" for Irvin, "Damien" for Adamya, "Zena pathways"
for Zenith pathways, "cognizant style" for "cognizant cow". The paste used for names can fix the
words too, and teach RTT the names it keeps mishearing for the rest of the call.

## Behavior
- Copy the Teams/Zoom transcript, press `T` (`t` = names only). One preview:
  `36 lines reworded · 147/155 lines named (…) · new words: Urban→Irvin`.
- Text: each RTT line (3+ words) is aligned word by word to the meeting-app turn at the same time
  (±75 s, midnight-safe). Only the part both transcripts share is replaced with the app's wording
  (60%+ of the line must match; the taken span must be 60%+ real matches and 0.6–1.5× as long).
  RTT's own words before/after that part stay, except a single edge word that sounds like the app's
  word (misheard names). A line's time and speaker label never change from text refinement.
- Where the app hid words (`**** *****`), RTT's words are kept.
- Names are matched after rewording (better words match better), same as `t`.
- New words: misheard names/terms the app spelled differently (the app's side has a capital or digit;
  not just common words; not numbers or word endings), offered only if corrected 2+ times or the
  word is part of a participant's name. `L` + Enter applies and learns them; Enter applies without
  learning; Esc cancels.
- Learned words go to `private/corrections.learned.json` (ignored by git: real people's names) and
  are loaded with corrections.json, so the running backend corrects new lines at once and later
  sessions too. A word learned with a capital ("Urban") only matches with that capital, so
  "urban planning" is left alone.
- One undoable ledger entry `meeting_refine` (params: summary, labels, learned words). `z` / Undo
  button restores text and names; learned words stay (edit or delete the learned file to remove).
- `rtt-ctl refine [file|-] [--learn] [--no-ai] [--dry-run]`.

## Interfaces
Key `T`; `rtt-ctl refine`; env `RTT_LEARNED_CORRECTIONS` (path, or `none` to disable).

## Implementation
`names_match.refine_text` (difflib alignment, shared-region replacement, edge words, redaction guard),
`names_match.learnable`, `corrections.learn` / `learned_path` / case-sensitive literal rules,
`Session.match_meeting_names(refine=True)`, `Session.apply_meeting_names(learn=)` (reloads
`backend._corrections`), `KeyActions._names_from_clipboard(refine=True)`.

## Verification
tests/test_refine.py: app wording taken, edge names fixed, redacted name kept, short lines left,
time/label kept, learnable words; learned file private, no duplicates, case-safe, applied by a fresh
Corrections; `T` → `L` → text + names applied, backend corrects new lines live, ledger lists learned
words, undo restores exactly. tests/conftest.py keeps tests away from the real learned file.
Real 2026-11-09 call (private/names-match/, not committed): 36 lines reworded, 147/155 named,
1 word offered (Urban→Irvin; Zena→Zenith held back because Teams also wrote "Xena"), undo exact.

## Limits
The app's transcript can be wrong too (it is trusted where both agree on most of the line). Only
the pasted time range is reworded. Learned words are global for this machine; remove bad ones
from the learned file.
