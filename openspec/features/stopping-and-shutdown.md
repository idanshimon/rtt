# Stopping and shutdown

Status: shipped 2026-11-06.

## Why
RTT could not be closed reliably: shutdown waited up to 3 minutes for the last chunk plus 90 s for
the summary, a second Ctrl+C did nothing, audio kept being captured, and the VibeVoice server
(22 GB) stayed up.

## Behavior
- Ctrl+C once: "Press Ctrl+C again within 3 s to stop"; nothing stops.
- Ctrl+C twice within 3 s, `q q`, or `rtt-ctl stop`: clean stop, logged in the ledger with its source.
- Ctrl+C during shutdown: force quit — terminal restored, socket and audio files closed, VibeVoice
  server killed, exit code 130.
- Clean stop order: stop capture → leave the full screen and restore the terminal → last chunk
  (≤ `RTT_STOP_FLUSH_TIMEOUT`, 15 s) → final summary (≤ `RTT_FINAL_SUMMARY_TIMEOUT`, 15 s; on timeout
  the last good summary stays) → close session → stop VibeVoice if this session used `vibe`
  (`RTT_VIBE_KEEP=1` keeps it) → "Session saved: …". SIGTERM stops cleanly.

## Implementation
transcribe.py `handle_interrupt`, `force_quit`, `stop_vibe_server` (kills only a process running
vibe_server.py on the port: SIGTERM, then SIGKILL), `ClipboardHotkeyListener.restore_terminal`,
`Session.close_fast`.

## Verification
Real PTY runs: openai clean exit 7.6 s; vibe 25 s (last 30 s chunk + summary) and no server left;
force quit 3.1 s; a single Ctrl+C keeps running.

## Limits
vibe clean exit is dominated by transcribing the last chunk; Ctrl+C again skips it.
