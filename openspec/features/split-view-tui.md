# Split view (TUI)

Status: shipped 2026-11-05; mouse 2026-11-06.

## Why
The scrolling output showed no state and no summary; there was nowhere to see what RTT was doing.

## Behavior
- Full screen (rich): status bar · transcript tail (left) · live summary (right) · footer with key
  hints and the latest message (prompts appear here too).
- Status bar: `● LIVE` / `⏸ PAUSED`, elapsed, backend, languages (green `he+en` when both work,
  mixed included; yellow `en only` or `(he ?)` when Hebrew is off or not measured — details in
  `rtt-ctl status` → `lang`), chunk (`chunk 15s` / `chunk auto 18s`),
  meeting title, `● REC`, mic state, `BACKEND DOWN`, last-text age (red after 120 s while live),
  bookmarks count, summary state.
- Bookmarks are bold yellow; system markers dim.
- Speakers (2026-11-09): each person's name is bold in their own color (12-color palette, assigned
  in order of first appearance and stable for the session; neighbours alternate hue), the word
  "Speaker" is not shown, timestamps are dim, and an unknown "?" is gray italic. Long lines wrap at
  word boundaries; wrapped rows are indented under the speech with a thin bar in the speaker's
  color. Clicking any row of a line still renames that speaker. (`SPEAKER_COLORS`, `CONT_INDENT`,
  `SplitView.speaker_color`.)
- Header warnings (2026-11-09, transcription-quality): yellow `⚠ …` items for too many voices,
  choppy lines or many unknown speakers; `N people` once `k` is set.
- Keys: see openspec/README.md. `?` shows the full list; `?` or Esc closes it. Prompts (rename, note, backend, off the
  record minutes) accept UTF-8 (Hebrew); Enter confirms, Esc cancels.
- Scrolling (2026-11-06): Tab switches the active panel (▶ in its title, brighter border).
  PgUp/PgDn scroll it a page, ↑/↓ one row, Home/End to top/bottom. The mouse wheel scrolls the panel
  under the pointer (3 rows) without changing focus. A scrolled transcript stays put while new
  lines arrive; its title shows "scrolled · N rows below · End = live", and End or scrolling back to
  the bottom returns to following live. The summary title shows ↑/↓ hidden rows when it overflows.
  Clicking a name works on the scrolled view. While a prompt is open, typed keys go to the prompt,
  but scrolling (wheel, PgUp/PgDn, arrows, Home/End) still works (2026-11-09). With the
  off-the-record prompt open, a click on any line puts its time into the prompt instead of renaming.
- Mouse: SGR mouse reporting is on. Left click on a transcript line with a speaker opens
  "Rename speaker “X” to:". Clicks in the summary column and button releases are ignored. Native
  text selection then needs Option (iTerm2/Terminal) or Shift (most others).
- `--no-tui` / `RTT_TUI=0` keeps the plain scrolling output with the same keys.

## Languages shown (languages.py `lang_profile`)
| Backend | Setting | Shown |
|---|---|---|
| vibe | – | he+en |
| openai | no --language / --language en | he+en / en only |
| llmspeech | rttheb (en-US,he-IL) / en-US only / none (auto) | he+en / en only / he+en |
| azure | rtthebold (en-US,he-IL, per-segment language ID) / one locale | he+en / en only |
| mai | --mai-locales with he / none | he+en / en (he ?) |
| local | --language / none | that language / en (he ?) |
Based on what RTT configures and what was measured in speech-bench; "?" = not measured.
Updates on a live backend switch.

## Implementation
`tui.SplitView` renders and records `click_map` [(first_row, last_row, label)] and the transcript
column width each frame (content starts at row 3, column 3). Both panels are rendered as explicit rows (lines wrapped to the panel width, cropped, not
re-wrapped by rich) so scroll offsets and click rows are exact: `tx_top` (None = live),
`sum_top`, page size = visible rows; `scroll`, `scroll_home`, `scroll_end`, `toggle_focus`.
`tui.KeyActions` maps keys to
Session methods, parses escape sequences (SGR mouse `ESC[<b;x;yM`, arrows ignored; a lone Esc
resolves after 150 ms in `tick()`), and runs prompts. `speaker_of(line)` extracts the label.
Ctrl+C is a signal, not a key (see stopping-and-shutdown).

## Verification
Scrolling: unit test (PgUp/End/wheel on transcript incl. click map on scrolled rows, Tab, PgDn/Home/
wheel/arrow on summary, prompt captures keys). Real PTY run (openai, Hebrew, 14-row terminal):
PgUp → "scrolled · 9 rows below", wheel down → 6 below, End → live; Tab → summary, PgDn ↑3↓0,
wheel up ↑0↓3, Home.
tests/test_session_more.py: click → rename for the clicked line, clicks in the summary column and
releases ignored, escape handling. Real PTY run: click on row → prompt → Hebrew name applied.

## Limits
Very long lines wrap; click map covers every wrapped row of a line.
