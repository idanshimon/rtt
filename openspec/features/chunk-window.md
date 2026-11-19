# Chunk window

Status: shipped 2026-11-06.

## Why
Short chunks give fast text but split words and lose speaker context; long chunks are better but
slow. The right size depends on the meeting and the backend, so it must change live.

## Behavior
- `[` / `]`: step through 3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120 s. Bounds: vibe 10–120, others
  3–60; an explicit `--chunk-seconds` outside the bounds is kept. Applies from the next chunk.
- `\`: auto mode.
  - Cut at the quietest 0.3 s (sliding, 0.1 s hop) inside the last 40% of the window, so speech is
    not split mid-word.
  - After each chunk: processing time / chunk length ≥ 0.8, or more than two windows of audio
    waiting → grow 25% (up to the max). Below 0.35 with no backlog → shrink back toward the chosen
    size, never below it.
- Status bar `chunk 15s` / `chunk auto 18s`; messages when auto changes the size.
- Ledger entries with undo.
- Applies to chunked backends only (openai, llmspeech, mai, vibe); others get a message.

## Interfaces
`--chunk-seconds`, `--chunk-mode fixed|auto`, `RTT_CHUNK_MODE`, `rtt-ctl chunk <s|+|-|auto|fixed>`;
status → `chunk` {seconds, current, mode, last_rtf, quiet_cuts, hard_cuts}.

## Implementation
`chunk_policy.ChunkPolicy` (shared by session and backends, survives switches),
`quietest_cut`; `ChunkedHTTPBackend.attach_policy`, `_drain_ready_chunk`, `policy.report`.

## Verification
tests/test_chunk_policy.py (steps, bounds, adapt up/down, pause detection, live resize, keys,
rtt-ctl, undo). Real PTY run (openai, Hebrew at speaking speed): 10 → 20 → 15 → auto; 7 of 7 cuts at
real pauses; processing 0.39 of real time.
