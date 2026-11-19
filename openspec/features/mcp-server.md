# MCP server (agent control)

## Status
Shipped 2026-11-13.

## Why
Let an agent (Hermes, Copilot, Claude) run RTT while you work with it: start, new session, rename, mark, off the record, switch backend, read the transcript and summary.

## Behavior
`rtt-mcp.sh` runs `rtt_mcp.py`, an MCP server over stdio. Every action is sent to the same control socket as the keys and `rtt-ctl`, so it lands in the action ledger (source `ctl`) and `rtt_undo` covers it. No confirm step: actions are logged and undoable, except off the record, which deletes on purpose.

Tools: rtt_status, rtt_start, rtt_stop, rtt_new_session, rtt_pause, rtt_resume, rtt_mute, rtt_mark, rtt_rename_speaker, rtt_expected_speakers, rtt_names_from_teams, rtt_off_record, rtt_undo, rtt_log, rtt_switch_backend, rtt_chunk, rtt_summarize_now, rtt_quality, rtt_tidy, rtt_transcript, rtt_summary, rtt_sessions, rtt_command (raw socket command).

`rtt_start` launches a wrapper (openai, llmspeech, mai, vibe, azure, azure-heb) in a new Terminal window (default), iTerm or Warp (via a `.command` file and `open -a`, so no AppleScript permission is needed), or headless (`--no-tui`, log in $TMPDIR/rtt-mcp-headless.log), then waits up to 30 s for the socket. It refuses when RTT is already running.

## Interfaces
Hermes `config.yaml`: `mcp_servers: rtt: command: <repo>/rtt-mcp.sh`. Env: `RTT_RUN_DIR` (socket dir), `RTT_SESSIONS_DIR` (default `<repo>/sessions`), `RTT_MCP_TIMEOUT` (socket timeout, default 60 s).

## Implementation
Stdlib only: the repo venv is Python 3.9 and the `mcp` package needs 3.10+. JSON-RPC handlers: initialize (protocol negotiation + instructions), ping, tools/list (with read-only / destructive hints), tools/call. stdout is reserved for protocol messages; imported modules' prints go to stderr. Socket timeout 15 s, 180 s for names/refine (`RTT_MCP_TIMEOUT` overrides). Any tool exception returns an error result instead of killing the server.

## Verification
`tests/test_mcp.py` (handshake, actions through a test socket, ledger source, not-running error). Read-only check against a live session with status + sessions.

## Limits
One running RTT at a time (one socket). Transcript and summary text is capped at 40,000 characters (newest kept, `truncated: true`). `since` (HH:MM or HH:MM:SS) compares line times as text, so a meeting that crosses midnight is not handled.
