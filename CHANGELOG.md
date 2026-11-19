# Changelog

## Unreleased
- Try local Whisper with an included 20-second synthetic recording: `.venv/bin/python demo/run.py`.
- Startup and `--show-config` show separate audio and live-summary destinations. Summaries remain on by default, including for Whisper; `--no-summary` disables and `--summary` re-enables them.

## 2026-11-18 (ready for public release)
- Packaging: MIT `LICENSE`, `NOTICE.md` (third-party licenses), `SECURITY.md` (what leaves your machine, recording consent), `CONTRIBUTING.md`, `requirements-dev.txt`, GitHub Actions tests on Python 3.11 and 3.12 (3.9 is tested locally).
- New README with a rendered screenshot of the split view (`scripts/render_readme_screenshot.py`, invented meeting); the long usage text moved to `docs/USAGE.md`; new `docs/ARCHITECTURE.md`.
- Fix: on Linux, `ps` cut its output to 80 columns, so a running RTT session could look stopped and be picked by `--resume`; now `ps -ww`.
- Fix: fresh installs pulled click 8.2+, which breaks typer 0.12 at startup; `click<8.2` is pinned.
- `RTT_COPILOT_GH_USER` now defaults to the active `gh` account instead of a fixed user.
- Removed personal and environment-specific details (resource names, subscription and tenant ids, local paths, a customer name in tests and docs). `.env.example` lists the newer backend variables.

## 2026-11-16 (voice-training mic track)
- `--record` with mic mixing also writes `mic_hq.flac`: your mic at 48 kHz for training a voice model. The mic opens once at 48 kHz and is filtered down to 16 kHz for transcription (no second stream; a second stream on the same device starved capture). Verified on the real devices: 40/40 blocks to the transcriber, speech intact on both tracks.

## 2026-11-13 (MCP server)
- Fix: `rtt_start` hung forever when macOS held the AppleScript Automation prompt; it now opens a `.command` file with `open -a` (Terminal, iTerm or Warp), no permission needed, 15 s limit.
- Review fixes: stray prints can no longer corrupt the protocol stream; any tool error is returned instead of crashing; transcript read streams the file, `since` accepts HH:MM and keeps untimed continuation lines; output capped at 40k chars; shorter socket timeout (15 s, 180 s for name matching); tool safety hints; pick-list arrows also work without the split view.
- `rtt-mcp.sh`: MCP server so an agent can start RTT, start new sessions, run every action and read the transcript/summary. Same control socket and ledger as the keys; stdlib only. Spec: openspec/features/mcp-server.md.

## 2026-11-13 (pick lists)
- `b` (switch backend) now opens a pick list: ←/→ or ↑/↓ move the highlight (starts on the current backend), Enter picks it, Esc cancels. Typing still works; a typed prefix moves the highlight. Other prompts keep ↑/↓ for scrolling.

## 2026-11-10 (domain vocabulary)
- One vocabulary of right spellings (shared `vocabulary.json` + private `private/vocabulary.json` for customer names): fixes casing on every backend (SSPS → SSPs, nwnd → NWND; ordinary words like "it" untouched) and is sent as a phrase list to `rttheb`, `rttold` and `rttvibe` (`RTT_PHRASE_LIST=0` to turn off).
- `rtt-align vocab list|add|remove|mine` — `mine` finds candidate terms and their spellings in past transcripts.

## 2026-11-10 (meetalign library + rtt-align CLI)
- Alignment logic (names, wording, learnable words, AI step, bursts) moved into `meetalign/`, a stdlib-only library with registries for paste formats, transcript layouts and AI resolvers. RTT uses it; `names_match.py` stays as a compatibility layer.
- `rtt-align` CLI works on any transcript file, a saved session, or the live RTT: `parse`, `names`, `refine [--learn]`, `tidy`, `learned`; diff by default, `--write` keeps a `.bak`, `--json`.

## 2026-11-10 (paste safety)
- Fix: pasting text into RTT ran every letter as a key (pauses, bookmarks, mute, copies, wrong name matches). Pastes are now recognized (bracketed paste + burst detection) and never run as keys; pasting a Teams/Zoom transcript offers names / text + names directly.
- `rtt-ctl tidy [--file F]` removes status-line bursts (undoable).

## 2026-11-09 (refine text from Teams/Zoom)
- `T`: same paste as `t`, but also replaces RTT's wording with Teams/Zoom's for lines both heard, and offers misheard names to learn (`L` + Enter). Learned words are private (`private/corrections.learned.json`) and fix new lines live. Undo with `z`. `rtt-ctl refine`.

## 2026-11-09 (speaker colors)
- Each speaker's name has its own color; wrapped lines break between words and are indented with a bar in the speaker's color; unknown "?" is gray italic.

## 2026-11-09 (undo button, full action log)
- Clickable `[ ↶ Undo: … ]` button at the bottom shows what undo will do; click = `z`.
- The action log now also records help, panel focus, leaving/returning to live view, cancelled prompts (with what was typed), invalid input, each `t` preview, picking a line time, and the first `q`.

## 2026-11-09 (code review fixes)
Independent review of the day's 4 features; all 16 findings fixed with tests:
- Audio: a crash's leftover WAV is no longer overwritten on resume; off the record cuts audio by capture time (correct across pauses and into audio from before a resume); cutting everything leaves no broken file.
- Resume: sessions closed with `N` are resumable; "latest" means most recently active; speaker labels come back exactly (`lines.json`) even after merging two labels into one name; `z` can't undo actions from before the resume; duration excludes the stopped gap; a new meeting title replaces the old one in the folder name.
- Names (`t`): undo keeps later renames and rebuilds the summary; nothing is applied if the transcript changed since the preview; midnight-crossing meetings match; "Action Item: …" text isn't taken as a speaker; the confirm prompt opens on the key thread.
- `rtt-ctl offrecord 2min` works; the quality check reads only the last 60 lines.

## 2026-11-09 (names from Teams/Zoom)
- `t`: copy the Teams/Zoom transcript, press `t` — every RTT line in that time range gets the real speaker's name (word match per line, Copilot for short lines), labels that clearly belong to one person are renamed for the rest of the call. Undo with `z`. `rtt-ctl names`.

## 2026-11-09 (quality warnings, people count, Esc closes help)
- Status bar warns live when one call grows too many voices, lines get choppy, or many lines have an unknown speaker.
- `k` / `--speakers N` / `rtt-ctl speakers N`: how many people are in the call; stops one noisy voice becoming Speaker 3, 4, 5….
- Esc closes the help screen.

## 2026-11-09 (resume a stopped session)
- `rtt --resume <id>`, `rtt -c` (latest), `rtt -c "title"`: continue a stopped session in the same folder and transcript, with speaker names, voices, marks, ledger, summary and audio carried over. Exit prints how to resume. Sessions still running in another RTT are never picked.

## 2026-11-09 (off the record from a line)
- `o` now also takes a line's time (`09:15:09`, `[09:15:09]`, or a pasted line): that line and everything after it are deleted. Minutes accept `2m` / `90s`.
- While the `o` prompt is open, scroll up and click a line to pick its time; the message line shows how many lines will go. Scrolling now works while any prompt is open.
- `rtt-ctl offrecord 09:15:09`.

## 2026-11-06 (scrolling)
- Mouse wheel scrolls the transcript and summary panels; Tab switches the active panel; PgUp/PgDn, ↑/↓, Home/End scroll it; End returns the transcript to live.

## 2026-11-06 (new session in-app, languages in status bar)
- `N` / `rtt-ctl new [name]`: close the session and start a new one without restarting RTT; the old one is finished properly (audio, summary, ended_at).
- Status bar shows which languages the current backend + settings handle (`he+en`, `en only`, `(he ?)`).

## 2026-11-06 (stable speakers, spoken timestamps)
- Speaker numbers stay the same person for the whole session: chunk-local labels are matched by voice (TitaNet-small via sherpa-onnx). Renames are now correct for every line. `scripts/get-speaker-model.sh`; `RTT_SPEAKER_TRACKING=0` to disable.
- Each line is stamped with when it was said (segment start), not when its chunk returned.
- Calendar title is used only while a Teams/Zoom/Webex call is using the mic.

## 2026-11-06 (live chunk window; bookmark marker)
- `[` / `]` change the chunk window live; `\` auto mode cuts at pauses and grows when the backend falls behind. `rtt-ctl chunk`, `--chunk-mode`. Ledger + undo.
- Marks are now written `[★ RTT bookmark: note]` (was `★ MARK: note`) and the summary prompt says bracketed RTT lines are not speech, so a person named Mark is never confused with a bookmark. Verified with a real summary call.

## 2026-11-06 (reliable stop)
- Ctrl+C asks for confirmation (press again within 3 s); a further Ctrl+C during shutdown force-quits. Shutdown stops capture first, restores the terminal, bounds the last-chunk flush and final summary (15 s each), and stops the local VibeVoice server when the session used it.
- Verified in a PTY: openai exit 7.6 s, vibe 25 s with the server stopped, force quit 3.1 s, single Ctrl+C keeps running.

## 2026-11-06 (click-to-rename, action ledger, off-the-record, live backend switch, calendar, watcher)
- Click a speaker line in the split view to rename it; renames also rewrite the live summary (raw labels like "A" never touch words like "Series A").
- Action ledger `sessions/<id>/actions.jsonl` (every user action with source key/mouse/ctl); `z` / `rtt-ctl undo [id]` / `rtt-ctl log` / `rtt-ctl rename`. Undo skips actions whose effect is already gone.
- Off the record (`o`, `rtt-ctl offrecord`), live backend switch (`b`, `rtt-ctl switch`), meeting title from the calendar via workiq, `rtt-post` labels your mic as `RTT_ME_NAME`, meeting watcher LaunchAgent (`install-meeting-watch.sh`), `RTT_FILE_REALTIME=1` to play files at speaking speed.
- Verification: 129 tests; real PTY runs (pyte) covering mouse rename, summary rename, backend switch openai→llmspeech, off-record, ledger and undo; real rtt-post with a mic track; real workiq calendar lookup. Watcher verified by unit tests and mic detection only (no live call yet).

## 2026-11-05 (session controls, recording, live summary, split view)
- New `session.py` (state, markers, session folder, FLAC recorder, control socket), `tui.py` (split view + single-key controls), `summarizer.py` (Copilot gpt-6.1-terra live summary), `rtt-ctl`, `rtt-post`.
- Flags: `--record/--no-record` (off), `--summary/--no-summary` (on), `--tui/--no-tui` (on), `--session-name`. Transcript path unchanged (livertt-compatible).
- Verification: 115 tests; real PTY run with recording, keys, rtt-ctl and a real Copilot summary. Details: openspec/features/session-controls.md.

## 2026-11-05 (local VibeVoice backend — `rttvibe`)
- New `vibe` backend + `run-vibe.sh`: posts 30 s WAV chunks to a loopback VibeVoice-ASR 7B server (speech-bench/vibe_server.py, Apple MPS); no Azure auth, nothing leaves the Mac.
- Measured: 95 s Hebrew podcast through the real CLI in 45 s; 2 min in ~50 s; peak memory ~22 GB. Speaker ids are chunk-local (not stable across chunks).
- Verification: 108 tests passed (2 new). Not yet tested on a live meeting capture.

## 2026-10-15 (runtime configuration visibility)
- Print a backend-aware resolved settings summary before capture/authentication on every run.
- Add `--show-config` (also registered in the configuration UI schema) to inspect settings without audio, authentication, hotkeys or transcript writes.
- `run.sh` reports the exact `.env` it loaded; summaries show audio/mic, model/deployment, language, chunk/window, auth/subscription, endpoint and output. Credentials are allowlisted out; URL userinfo/query/fragment are hidden.
- Verification: 106 tests passed, including seven new config-display tests covering every backend, CLI precedence, secret hiding, startup visibility and side-effect-free inspection. Real `run-openai.sh --show-config` exercised the live `.env` path. Existing urllib3/LibreSSL warning remains unchanged.

## 2026-10-14 (MAI-Transcribe-2 batch backend — `--backend mai`)
- **New `mai` backend: Azure Speech MAI-Transcribe-2 (chunked batch) with speaker diarization.** MAI-Transcribe-2 is Microsoft's LLM-enhanced transcription model that supports diarization, reachable on the SAME fast-transcription REST surface as `llmspeech` (`/speechtotext/transcriptions:transcribe`, same Speech resource). `MAITranscribeBackend` extends `LLMSpeechBackend`, inheriting the chunk framing, bare-bearer/key auth, the 401 self-heal, transient-retry + loud escalation, and the `phrases[].speaker → "Speaker N: text"` formatter unchanged.
  - **The only differences, both verified empirically (HTTP 200 on real audio):** `enhancedMode` selects the model `{"enabled":true,"task":"transcribe","model":"MAI-Transcribe-2"}`, and diarization is `{"enabled":true}` with **NO `maxSpeakers`** — MAI rejects `maxSpeakers` with `HTTP 400 "maxSpeakers is not supported for MAI transcription yet."` (the stock LLM Speech path always sends `maxSpeakers`, so it was NOT already MAI-ready). Refactored the definition into `LLMSpeechBackend._build_definition()` so the subclass overrides only that.
  - **CLI:** `--backend mai` + `--mai-endpoint` / `--mai-model` (default `MAI-Transcribe-2`) / `--mai-locales`. Endpoint resolves from `--mai-endpoint` → `RTT_MAI_ENDPOINT` → `RTT_LLMSPEECH_ENDPOINT` → `AZURE_SPEECH_ENDPOINT`. Schema registers the `mai` backend + a `mai` profile; `run-mai.sh` launcher added.
  - **No default/alias changes.** `rtt` (openai), `rttheb` (llmspeech), `rttold`/`rtthebold` (azure) are untouched — `mai` is available for evaluation; the alias repoint is a separate, deliberate decision.
  - **Real CLI smoke (run-filetest.sh, test.mp3, subscription pinned):** short segments returned numeric speaker labels; this did NOT establish correct attribution or complete input coverage. A subsequent 3-minute benchmark exposed an existing chunked-worker EOF bug: only two buffered chunks drained. Fixed by draining until empty; regression failed at 2/19 requests before the fix, and full suite passes 99 tests afterward. Full 28-minute MAI request returned HTTP 200 in 20.636s, 92 phrases and eight distinct speaker IDs (not a verified participant count). In the completed 3-minute 10s-chunk run, speaker IDs reset across requests and merge different people under Speaker 0. Do not claim session-wide diarization. Classic `azure`/`rttold` independently emitted `Guest-N` after SDK 1.52 upgrade.
  - Tests: `tests/test_mai_backend.py` (+10), `tests/test_mai_cli.py` (+2), `tests/test_mai_schema.py` (+6), incl. a guard that `LLMSpeechBackend` still sends `maxSpeakers`/no-model and that `rtt`/`rttheb`/`rttold` routing is unchanged. Full suite **98/98**.

## 2026-07-06 (tenant-drift pin + file-test wrapper)
- **Fix: RTT no longer breaks when the `az` CLI active context drifts to another tenant.** When the openai/llmspeech resources live in one tenant and `az` is switched to a subscription in another tenant, an unpinned `DefaultAzureCredential` mints a wrong-tenant token and the resource rejects it with `HTTP 500 "Unable to get resource information"` — a cross-tenant rejection that masquerades as a server error, **not** a 401. (This was the actual cause of the "500 outage" first mislabeled as transient — the resource was healthy; only the token's tenant was wrong.) The resource has `disableLocalAuth=true`, so API keys are not an option.
  - **`RTT_AAD_SUBSCRIPTION`** (new `.env` var): when set, `_build_aad_credential()` pins minting via `AzureCliCredential(subscription=...)`. Pinning the SUBSCRIPTION is required — `tenant_id=` alone still follows the active account and fails. Makes RTT immune to `az` drift. Unset → falls back to `DefaultAzureCredential` (prior behavior).
  - Proven end-to-end: with `az` active on a CORP sub, the real CLI printed `Pinning AAD token to subscription …` and transcribed cleanly; unpinned mint 500s under the same condition.
  - **`run-filetest.sh`** (new): file-mode test wrapper that sources `.env` for creds/endpoints but UNSETS capture vars (RTT_INPUT_DEVICE, RTT_INCLUDE_MIC, RTT_MIX_MIC, RTT_MIC_DEVICE, RTT_USE_LOOPBACK) for the invocation only — so file tests never collide with the live-capture "use either --input-file or --input-device" guard, and `.env` is never hand-edited/restored (which previously left it half-masked and broke live capture).
  - Tests: `tests/test_aad_credential.py` +5 (pin-when-set, fallback-when-unset, blank-ignored, cli-unavailable-fallback, no-credential-raises). Full suite 68/68.

## 2026-07-06 (transient-outage resilience)
- **Fix: chunked backends survive a transient failure instead of silently dropping audio.** During a stretch of `HTTP 500` responses the old per-chunk log-and-skip dropped ~140 chunks (~23 min) with no visible signal, discovered only by watching stderr. (NOTE: the specific 500s that day were later root-caused to tenant drift, not a server outage — see the tenant-drift pin entry above. This resilience layer is still correct and valuable for GENUINE transient blips/throttling, and its escalation is what surfaces a persistent failure like drift loudly instead of silently.)
  - **Bounded transient retry:** `_post()` now retries 429/500/502/503/504 and network-level `RequestException`s up to 2× with linear backoff. A brief blip recovers instead of dropping that chunk. The 401 token-refresh is separate and does NOT consume the transient budget.
  - **Loud health escalation:** after 3 consecutive failed chunks, ONE `⚠️ RTT BACKEND DOWN …` line is written into the transcript (not just stderr) telling you audio is dropping and to switch backend (rttheb/rttold/local). On recovery, a `⚠️ RTT RECOVERED` line + normal transcription resumes. A long outage is now visible at ~30s, not 23 min later.
  - Escalates exactly once per outage; resets on recovery so future outages re-escalate.
  - Tests: `tests/test_chunked_backends.py` +8 (transient retry/exhaust, 429, network-exc, 401-doesn't-eat-budget, escalate-once, recovery-announced, single-failure-no-spam). Full suite 63/63.
  - NOT done (deliberate): automatic backend failover — has label/language/cost tradeoffs, left as a user decision; the escalation line prompts the switch.

## 2026-07-06
- **Fix: chunked backends (`openai`/`llmspeech`) no longer die mid-meeting on AAD token expiry.** A live call was dropping ~24 min in (not 60) with a permanent `chunk failed: HTTP 401` storm that never recovered — forcing a restart that reset diarization/speaker labels. Three compounding causes: (1) `DefaultAzureCredential` falls through to the shared `az` CLI token, often handed over already-aged (<30 min life); (2) the token cache used a blind 30-min wall-clock TTL that ignored the token's real `exp`; (3) a 401 never busted the cache, so the dead token was re-sent forever.
  - **Real-expiry cache:** the AAD token provider now returns `(token, expires_on)`; the cache refreshes 5 min before actual expiry instead of a fixed TTL. Bare-string providers still supported.
  - **One-shot 401 self-heal:** new `ChunkedHTTPBackend._post()` catches a 401/403, force-mints a fresh token, and retries the same chunk ONCE. A transient token death now self-heals in ~one chunk (~10s) in the **same process / same session** — no restart, so speaker labels are preserved. Persistent auth failure surfaces cleanly (no infinite loop); key-auth skips the path entirely.
  - Tests: `tests/test_chunked_backends.py` +5 (real-expiry refresh, bare-string TTL fallback, 401-retry-with-fresh-token, no-infinite-retry, key-auth no-op). Full suite 55/55.

## 2026-07-04 (rtt-cli)
- **`rtt-cli`** — interactive menu / wizard / chat front-end for configuring and launching RTT. Zero new dependencies (pure stdlib).
  - **Schema-driven** (`config_schema.py`): one declarative `Field` registry is the single source of truth. Wizard, chat parser, validation, and the launch-command builder all read from it — adding a new option = one Field entry, no UI changes.
  - Three modes: main menu (profiles + edit + save/load), wizard (walks every applicable option, backend-aware), chat (natural language — "use hebrew", "chunk 8", "faster", "add my mic", "switch to local").
  - 5 profiles (`rtt`/`rttheb`/`rttold`/`rtthebold`/`local`) resolved from the schema, mirroring the shell aliases. Named configs save to `~/.config/rtt-cli/`.
  - `rtt-cli.sh` launcher + `rtt-cli` alias. Non-interactive `--print-argv` / `--launch` / `--profile` / `--load` entry points.
  - Tests: `tests/test_rtt_cli.py` (23 tests incl. a same-backend alias-collision guard). Full suite 50/50.

## 2026-07-04
- **Two new transcription backends** for the newer Azure speech models (chunked HTTP, `chunked_backends.py`):
  - **`--backend openai`** → Azure OpenAI `gpt-4o-transcribe-diarize`. Better WER, native diarization, ~1/3 the per-hour cost of classic Speech. New default for the `rtt` alias. Runs on a dedicated `rtt-transcribe-*` resource (eastus2).
  - **`--backend llmspeech`** → Azure Speech "LLM Speech" `enhancedMode`. LLM-enhanced quality, multilingual auto-detect (Hebrew/English code-switching), diarization. New default for the `rttheb` alias. Runs on the existing Speech resource.
- Both use a **micro-batch** design: audio is buffered into `--chunk-seconds` windows (default 10s) and POSTed per chunk. Trades true real-time latency for model quality. Includes a silence gate (skips quiet chunks) and per-chunk failure isolation (a dropped chunk never kills the run).
- Auth reuses the AAD `token_provider` discipline (fresh bearer token on demand, 30-min cache); API-key auth also supported.
- **Alias changes:** `rtt`→`run-openai.sh`, `rttheb`→`run-llmspeech.sh`. Classic Speech SDK backends preserved as **`rttold`** (`run-azure.sh`) and **`rtthebold`** (`run-azure-heb.sh`) for fallback + A/B baseline.
- New CLI options: `--chunk-seconds`, `--openai-endpoint`, `--openai-deployment`, `--llmspeech-endpoint`, `--llmspeech-locales`. New env vars: `RTT_OPENAI_ENDPOINT`, `RTT_LLMSPEECH_ENDPOINT`.
- Unit tests: `tests/test_chunked_backends.py` (20 tests, no network).
- API contract note: the OpenAI audio endpoint needs stable data-plane api-version `2024-10-21` (Speech-style `2025-10-15` 404s). Diarize returns letter labels (`Speaker A`), LLM Speech returns numeric (`Speaker 1`).

## 2026-05-06
- **Cross-platform installer** (`install.sh` / `install\windows.ps1`) — zero-friction onboarding:
  - macOS: auto-installs BlackHole via Homebrew, programmatically creates a `RTT Multi-Output` aggregate device (Swift + CoreAudio), sets it as default output, runs a tone-based audio smoke test.
  - Linux: detects PulseAudio/PipeWire monitor source.
  - Windows: configures WASAPI loopback (built-in, no drivers). *Beta — not yet validated on a live Windows machine.*
- **`--include-mic`** flag and `RTT_INCLUDE_MIC=1` env var — mixes the system microphone into the captured stream so the user's own voice appears in the transcript. Companion flags `--mic-device` and `--mic-gain`.
- **`--loopback`** flag and `RTT_USE_LOOPBACK=1` — enables WASAPI loopback capture on Windows.
- Added `MixedDeviceCapture` class: dual-stream capture (system + mic) with bounded ring buffers and real-time mixing.
- Sanitized public-repo references; added `.stubs/`, `.vfsmeta/`, `devices.txt`, `AGENTS.md` to `.gitignore`.

## 2026-03-12
- Fixed Azure AD token authentication: tokens are now formatted as `aad#<resource-id>#<token>` as required by the Speech SDK.
- Added `AZURE_SPEECH_RESOURCE_ID` env var and `--azure-resource-id` CLI flag (required for Azure AD auth).
- Updated `.env.example` with Azure AD auth instructions.
- Expanded Troubleshooting section with common 401 causes (stale keys, missing resource ID, RBAC roles).

## 2026-01-18
- Updated terminal hotkeys for clipboard workflow:
  - `Ctrl+S` copies full transcript so far and sets the delta baseline.
  - `Ctrl+E` copies transcript delta since the last `Ctrl+S`.
- Added `Ctrl+P` to pause/resume transcribing.
- Removed the mistaken VS Code-extension approach; hotkeys are handled in-terminal.
- Added `run.sh` and `run-azure.sh` helpers to simplify startup and auto-load `.env`.
