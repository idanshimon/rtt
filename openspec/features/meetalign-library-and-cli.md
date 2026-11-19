# meetalign library and rtt-align CLI

Status: shipped 2026-11-10.

## Why
Names (`t`), wording (`T`), paste handling and tidy were built inside RTT's session/TUI code and
assumed RTT's line layout, one paste format family and Copilot. To use them on any transcript
file, from scripts, with other recorders/backends or other AI providers, the logic needed to be
a library with clear seams and a CLI that does not need a running RTT.

## Behavior
- `meetalign/` (stdlib only, no RTT imports — enforced by a test): `align(raw_lines, meeting, *,
  line_format, source, names, refine, resolver, window_s) -> AlignResult`. Pure: input lines are not
  changed; callers apply `changes`. Stages and registries as in meetalign/README.md.
- RTT uses it: `Session.match_meeting_names` = `align(...)`; burst detection = `meetalign.find_bursts`;
  paste detection = `meetalign.parse_meeting`. `names_match.py` remains as a thin compatibility layer.
- `rtt-align` CLI (alias in ~/.zshrc): `parse | names | refine [--learn] | tidy | learned`.
  TRANSCRIPT = file, session id/name (same lookup as `rtt -c`), or `live` (sent to the running RTT,
  where it is undoable). MEETING = file, `-`, `clip`. Default is a diff; `--write` keeps a `.bak`;
  `-o FILE`; `--json`; `--ai copilot|none|<registered>`; `--format`; `--source`; `--window`.
  `--learn` (with `--write`) adds words to the private dictionary (corrections.learned.json).
  Exit codes 0 changed / 1 nothing to do / 2 usage or input error.
- Tests never make real Copilot calls or touch the private dictionary (tests/conftest.py).

## Interfaces
Python: `meetalign.align`, `parse_meeting`, `register_source`, `RegexLineFormat`,
`register_line_format`, `register_resolver`, `find_bursts`, `learnable`, `match_names`, `refine_lines`.
CLI: `rtt-align`.

## Files
`meetalign/{__init__,model,clock,text,sources,lines,names,refine,ai,bursts,api}.py`,
`meetalign/README.md`, `rtt_align.py`, `rtt-align.sh`, `names_match.py` (compat).

## Verification
tests/test_meetalign.py: no RTT imports; align is pure; another recorder's layout via
RegexLineFormat; custom source; custom resolver incl. failure fallback; generic bursts; CLI names/
refine `--write` (+.bak) / `--json` / parse / tidy / error exit. All earlier tests (names, refine,
paste, tidy, session) pass unchanged on the library. Real 2026-11-09 data through the library and
CLI: 133/155 named by words, 36 reworded, Urban→Irvin offered — identical to before the split.
Real 2026-11-10 transcript: `rtt-align tidy` finds the same 186 burst lines.

## Limits
`summarizer.py` keeps its own Copilot client (same endpoint); `live` needs an RTT that has the
`refine`/`names`/`tidy` commands (restart RTT after 2026-11-10).
