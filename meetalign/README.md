# meetalign

Line up an automatic transcript (RTT or any other recorder) with a meeting app's transcript
(Teams, Zoom, .vtt, …): real names per line, the app's wording where both heard the same speech,
misheard names worth learning, and accidental status-line bursts. Stdlib only; no RTT imports.

```python
from meetalign import align

result = align(my_lines, teams_text, refine=True, resolver="copilot")   # or None, or any callable
print(result.summary())               # "36 lines reworded · 147/155 lines named (…)"
new_lines = [result.changes.get(i, ln) for i, ln in enumerate(my_lines)]
result.learn                           # [("Urban", "Irvin")]
```

`align()` applies nothing; it returns an `AlignResult` (`changes`, `lines_before`, `label_names`,
`matches`, `fixes`, `learn`, `turns`, `ai_filled`, `ai_error`, `summary()`, `to_dict()`).

## Pipeline and extension points

| Stage | Module | Built in | Extend with |
|---|---|---|---|
| meeting text → `[Turn]` | `sources` | `auto` (Teams copy, .vtt, `[time] Name: text`, `Name: text`, `[Name] time`) | `register_source(name, fn(text) -> [Turn])` |
| your lines ↔ `Line` | `lines` | `rtt` (`[HH:MM:SS] Speaker X: text`) | `register_line_format(name, RegexLineFormat(pattern, template))` or any object with `parse(index, raw)` / `render(line, label=, text=)` |
| who said it | `names` | time ±75 s + words + word pairs; label votes (2+ lines, 75%+) | `window_s` |
| better words | `refine` | shared-region replacement, edge names, redaction guard; `learnable()` | `min_ratio`, `window_s` |
| undecided lines | `ai` | `copilot` (gh token), `none` | `register_resolver(name, fn(prompt) -> text)` or pass a callable |
| domain terms | `vocab` | `Vocabulary(terms, common).normalize()` / `.phrases()`, `mine(lines)` | your own term lists |
| status bursts | `bursts` | RTT marker lines, 4+ within 3 s | `find_bursts(lines, marker_re, gap_s, min_run)` |

Times are clock seconds since midnight and compared midnight-safe (`clock.near`).

## CLI

`rtt-align` (repo root `rtt_align.py`, wrapper `rtt-align.sh`) — see `rtt-align --help`:
`parse`, `names`, `refine [--learn]`, `tidy`, `learned`; TRANSCRIPT = file, session id/name, or
`live`; MEETING = file, `-`, or `clip`; shows a diff unless `--write` (keeps `.bak`) or `-o FILE`;
`--json`, `--ai`, `--format`, `--source`, `--window`. Exit 0 = changed, 1 = nothing to do, 2 = error.
