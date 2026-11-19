# RTT specs

One file per feature under `features/`. Each spec has the same sections: Status · Why ·
Behavior · Interfaces · Files and data · Implementation · Verification · Limits.
Code is the source of truth; when a spec and the code disagree, fix the spec in the same commit.

## Features

| Spec | What it covers | Status |
|---|---|---|
| [chunked-transcription-backends](features/chunked-transcription-backends.md) | openai / llmspeech chunked HTTP backends, shared base class | shipped 2026-07 |
| [mai-transcribe-backend](features/mai-transcribe-backend.md) | MAI-Transcribe-2 backend | shipped 2026-10 |
| [vibevoice-local-backend](features/vibevoice-local-backend.md) | local VibeVoice-ASR backend (`rttvibe`), server lifecycle | shipped 2026-11-05 |
| [session-controls](features/session-controls.md) | Session object, pause/resume, mute, bookmarks, new session in-app, control socket, markers, session folder | shipped 2026-11-05 |
| [split-view-tui](features/split-view-tui.md) | full-screen view, status bar (incl. languages), keys, mouse, prompts | shipped 2026-11-05/06 |
| [mcp-server](features/mcp-server.md) | MCP server: agents start RTT, run every action, read transcript/summary | shipped 2026-11-13 |
| [speaker-rename-hotkey](features/speaker-rename-hotkey.md) | original rename (Ctrl+R / `r`), speaker map | shipped 2026-05; extended |
| [speaker-tracking](features/speaker-tracking.md) | stable speaker ids across chunks, click-to-rename, renames in summary, spoken time per line | shipped 2026-11-06 |
| [live-summary](features/live-summary.md) | Copilot gpt-6.1-terra incremental summary, copy keys | shipped 2026-11-05 |
| [audio-recording-and-second-pass](features/audio-recording-and-second-pass.md) | `--record`, tracks, `rtt-post`, "me" attribution | shipped 2026-11-05/06 |
| [off-the-record](features/off-the-record.md) | delete the last N minutes everywhere | shipped 2026-11-06 |
| [live-backend-switch](features/live-backend-switch.md) | change backend mid-session | shipped 2026-11-06 |
| [chunk-window](features/chunk-window.md) | chunk size live (`[` `]`), auto mode | shipped 2026-11-06 |
| [action-ledger](features/action-ledger.md) | actions.jsonl, undo | shipped 2026-11-06 |
| [meeting-awareness](features/meeting-awareness.md) | mic-in-use detection, calendar title, meeting watcher | shipped 2026-11-06 |
| [session-resume](features/session-resume.md) | `--resume` / `-c`: pick a stopped session up again; exit hint | shipped 2026-11-09 |
| [transcription-quality](features/transcription-quality.md) | live quality warnings, `k` / `--speakers` people count | shipped 2026-11-09 |
| [meeting-app-names](features/meeting-app-names.md) | `t`: real names from a copied Teams/Zoom transcript (+ Copilot) | shipped 2026-11-09 |
| [meeting-app-refine](features/meeting-app-refine.md) | `T`: the app's wording for matched lines + learn misheard names | shipped 2026-11-09 |
| [paste-safety](features/paste-safety.md) | pasted text never runs as keys; paste a Teams transcript into RTT; `tidy` | shipped 2026-11-10 |
| [meetalign-library-and-cli](features/meetalign-library-and-cli.md) | reusable alignment library + `rtt-align` CLI | shipped 2026-11-10 |
| [domain-vocabulary](features/domain-vocabulary.md) | right spelling of domain terms (SSPs, NWND…) on every backend; phrase lists; `rtt-align vocab` | shipped 2026-11-10 |
| [stopping-and-shutdown](features/stopping-and-shutdown.md) | Ctrl+C confirm, bounded shutdown, force quit | shipped 2026-11-06 |

## Quick reference

### Keys (split view and plain mode)
| Key | Action | Spec |
|---|---|---|
| space | pause / resume | session-controls |
| m / M | bookmark / bookmark with note | session-controls |
| u | mute / unmute your mic | session-controls |
| c / e | copy full transcript / copy new lines since last c | split-view-tui |
| s / a / d | copy summary / action items / decisions | live-summary |
| n | update summary now | live-summary |
| r, or click a line | rename speaker | speaker-tracking |
| [ / ] | smaller / bigger chunk window | chunk-window |
| \ | chunk window auto / fixed | chunk-window |
| b | switch backend | live-backend-switch |
| o | off the record | off-the-record |
| k | how many people in the call | transcription-quality |
| t | names from a copied Teams/Zoom transcript | meeting-app-names |
| T | text + names from the same paste (L = also learn words) | meeting-app-refine |
| z, or click `[ ↶ Undo ]` | undo last action (button shows what) | action-ledger |
| q q, or Ctrl+C twice | stop (Ctrl+C during shutdown = force quit) | stopping-and-shutdown |
| Tab | switch active panel (transcript / summary) | split-view-tui |
| PgUp / PgDn, ↑ / ↓, Home / End | scroll the active panel (End = back to live) | split-view-tui |
| mouse wheel | scroll the panel under the pointer | split-view-tui |
| N | new session (name prompt; capture keeps running) | session-controls |
| ? (Esc closes) | help | split-view-tui |

Old Ctrl+P (pause), Ctrl+S/E (copy), Ctrl+R (rename) still work.

### rtt-align (offline / scripts)
`parse MEETING · names T MEETING · refine T MEETING [--learn] · tidy T · learned · vocab list|add|remove|mine` — T = file, session
id/name or `live`; MEETING = file, `-` or `clip`; diff by default, `--write` (keeps .bak), `-o`, `--json`,
`--ai`, `--format`, `--source`, `--window`. Library: meetalign/README.md.

### rtt-ctl
`status · quality · names [file] · refine [file] [--learn] · tidy [--file F] · speakers <n> · pause · resume · toggle · mute · unmute · mark [note] · summarize · offrecord [min|HH:MM:SS] ·
rename <from> <to> · log · undo [id] · chunk <s|+|-|auto|fixed> · switch <backend> · new [name] · stop`
Socket `$RTT_RUN_DIR/rtt.sock` (default `~/.rtt/run`), mode 0600.

### Flags (new since 2026-11)
`--record/--no-record` (off) · `--summary/--no-summary` (on) · `--tui/--no-tui` (on) ·
`--session-name` · `--chunk-mode fixed|auto` · `--backend vibe` · `--speakers N` · `--resume [id]` · `-c/--continue [text]`.

### Environment
| Variable | Default | Spec |
|---|---|---|
| RTT_RECORD | 0 | audio-recording-and-second-pass |
| RTT_SUMMARY / RTT_SUMMARY_MODEL / RTT_SUMMARY_INTERVAL | 1 / gpt-6.1-terra / 60 | live-summary |
| RTT_COPILOT_GH_USER / RTT_COPILOT_URL | active `gh` account / …/responses | live-summary |
| RTT_FINAL_SUMMARY_TIMEOUT / RTT_STOP_FLUSH_TIMEOUT | 15 / 15 | stopping-and-shutdown |
| RTT_TUI | 1 | split-view-tui |
| RTT_RUN_DIR | ~/.rtt/run | session-controls |
| RTT_CHUNK_MODE | fixed | chunk-window |
| RTT_SPEAKER_TRACKING / RTT_SPEAKER_MODEL / RTT_SPEAKER_THRESHOLD | 1 / models/nemo_en_titanet_small.onnx / 0.45 | speaker-tracking |
| RTT_VIBE_URL / RTT_VIBE_HOME / RTT_VIBE_PROMPT / RTT_VIBE_CHUNK / RTT_VIBE_KEEP | 127.0.0.1:8765 / ../speech-bench (sibling of the repo) / – / 30 / 0 | vibevoice-local-backend |
| RTT_ME_NAME / RTT_POST_PIECE_SECONDS | Me / 3000 | audio-recording-and-second-pass |
| RTT_CALENDAR | workiq | meeting-awareness |
| RTT_WATCH_CMD / RTT_WATCH_APPS / RTT_WATCH_START_AFTER / RTT_WATCH_STOP_AFTER | run-openai.sh / Teams,Zoom,Webex / 10 / 30 | meeting-awareness |
| RTT_FILE_REALTIME | 0 | session-controls (testing) |

### Files
```
transcripts/transcript-*.txt        live transcript (livertt reads this; path unchanged)
sessions/<YYYYMMDD-HHMMSS>[-name]/
  session.json      state: pauses, marks, backends, redactions, chunk, speakers, audio, ended_at
  actions.jsonl     action ledger (append-only)
  summary.md        live summary
  transcript.txt    link to the live transcript
  others.flac / mic.flac / audio.flac   with --record (WAV while running)
  transcript.final.txt                  after rtt-post
models/nemo_en_titanet_small.onnx      speaker model (scripts/get-speaker-model.sh, gitignored)
```
