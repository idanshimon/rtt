# Usage guide

Back to [README](../README.md). Every key, `rtt-ctl` command, flag and environment variable is
listed in [openspec/README.md](../openspec/README.md).

### Shell aliases

The examples use these aliases. Add the ones you want to `~/.zshrc` or `~/.bashrc`:

```bash
RTT=~/rtt                        # where you cloned the repo
alias rtt="$RTT/run-openai.sh"       # Azure OpenAI gpt-4o-transcribe-diarize
alias rttheb="$RTT/run-llmspeech.sh" # Azure LLM Speech, Hebrew + English
alias rttold="$RTT/run-azure.sh"     # classic Azure Speech SDK
alias rtthebold="$RTT/run-azure-heb.sh"
alias rttvibe="$RTT/run-vibe.sh"     # local VibeVoice server
alias rtt-cli="$RTT/rtt-cli.sh" rtt-ctl="$RTT/rtt-ctl.sh" rtt-align="$RTT/rtt-align.sh" rtt-post="$RTT/rtt-post.sh"
```

## Manual setup (if you skip `install.sh`)
1. **Audio routing** — install a virtual loopback device:
   - macOS: [BlackHole](https://existential.audio/blackhole/) + Multi-Output Device
   - Windows: nothing needed — use `--loopback`
   - Linux: PulseAudio/PipeWire monitor source (built in)
2. **Python 3.9+** with `venv`
3. (Optional) Azure Speech resource key + region for the cloud backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env   # then edit
```

## Usage

Every transcription run prints a resolved configuration summary before capture/authentication.
To inspect the same settings without recording, authenticating, or creating a transcript:

```bash
rtt --show-config
rtt --show-config --chunk-seconds 8 --language he
```

`rtt` is a shell alias for `run-openai.sh`, which selects `--backend openai`
and calls `run.sh`. That wrapper loads the repo-local `.env` (overriding matching
inherited environment values); explicit CLI flags override the supported environment
fallbacks, then built-in defaults apply. A plain `rtt` does not load a saved
`rtt-cli` profile. The summary shows the loaded `.env` path, backend/model,
language/locales, chunk/window settings, audio inputs/mic, auth mode/subscription,
endpoint and output path. API keys/tokens and URL credentials are never printed.
`--show-config` reports configured device selectors without opening audio devices;
the timestamped output path is prospective and no file is created.

List devices:
```bash
source .venv/bin/activate
python transcribe.py --list-devices
```

Quick start (recommended):
```bash
./run.sh --list-devices
```

Local transcription from the default device:
```bash
source .venv/bin/activate
python transcribe.py --backend local --model-size base
```

Specify a device (index or name substring) and save output:
```bash
source .venv/bin/activate
python transcribe.py --input-device "BlackHole" --output-file transcripts.txt
```
That command continuously appends to `transcripts.txt` as lines arrive (overriding the default timestamped path under `transcripts/`).

Hotkeys summary:
- `Ctrl+S` copies full transcript so far.
- `Ctrl+E` copies transcript since last `Ctrl+S` (repeatable).
- `Ctrl+P` pauses/resumes transcribing.

Preview only part of a long recording (skip the first 30 seconds and process 60 seconds total):
```bash
source .venv/bin/activate
python transcribe.py --input-file test.mp3 --skip-seconds 30 --max-seconds 60
```

Azure backend (requires env vars `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`):
```bash
source .venv/bin/activate
# Fill values in .env or export here
export AZURE_SPEECH_KEY="<your-key>"
export AZURE_SPEECH_REGION="<your-region>"
python transcribe.py --backend azure
```

### Azure AD authentication (no API key)

If you don't have (or prefer not to use) a subscription key, the tool falls back to Azure AD via `DefaultAzureCredential`. You must also set `AZURE_SPEECH_RESOURCE_ID` to the full ARM resource ID of your Speech resource:

```bash
# 1. Log in to Azure
az login

# 2. Find your Speech resource ID
az cognitiveservices account show --name <name> --resource-group <rg> --query id -o tsv

# 3. Set in .env (no AZURE_SPEECH_KEY)
AZURE_SPEECH_REGION=eastus
AZURE_SPEECH_RESOURCE_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>

# 4. Run
./run-azure.sh
```

Your Azure AD identity needs the **Cognitive Services Speech User** (or broader) role on the Speech resource.

If you keep your Azure vars in `.env`, you can start the common Azure mode with:
```bash
./run-azure.sh
```

Add Azure speaker labels (Conversation Transcriber) to prefix lines with `Speaker <id>`:
```bash
python transcribe.py --backend azure --azure-speaker-labels
```
Speaker diarization currently requires the Azure backend; local Whisper mode continues to emit unlabeled lines.

## Newer model backends (openai / llmspeech)

Beyond the classic Speech SDK backend (`--backend azure`), RTT can transcribe with Microsoft's newer speech models over their REST APIs. These are **chunked** (micro-batch): audio is buffered into `--chunk-seconds` windows (default 10s) and POSTed per chunk, trading true real-time latency for higher model quality + native diarization.

### `--backend openai` — Azure OpenAI `gpt-4o-transcribe-diarize`
Better word error rate, native speaker diarization, ~1/3 the per-hour cost of classic Speech. Speaker labels are letters (`Speaker A`, `Speaker B`).
```bash
# Needs RTT_OPENAI_ENDPOINT (Azure OpenAI resource endpoint) in .env
python transcribe.py --backend openai --chunk-seconds 10
# or just:
./run-openai.sh
```

### `--backend llmspeech` — Azure Speech "LLM Speech" (`enhancedMode`)
LLM-enhanced quality with multilingual auto-detect (handles Hebrew/English code-switching) and diarization. Speaker labels are numeric (`Speaker 1`, `Speaker 2`).
```bash
# Needs RTT_LLMSPEECH_ENDPOINT (Azure Speech resource endpoint) in .env
python transcribe.py --backend llmspeech --llmspeech-locales "en-US,he-IL"
# or just:
./run-llmspeech.sh
```

### `--backend mai` — Azure Speech **MAI-Transcribe-2** (batch, diarization)
Microsoft's MAI transcription model with LLM-enhanced quality **and speaker diarization**, reached on the SAME fast-transcription REST surface as `llmspeech`. Speaker labels are numeric (`Speaker 0`, `Speaker 1`). The request sends `enhancedMode{model:"MAI-Transcribe-2"}` + `diarization{enabled:true}` with **no** `maxSpeakers` (MAI rejects it with HTTP 400). Available for evaluation — not yet a default alias.
```bash
# Needs an Azure Speech endpoint: RTT_MAI_ENDPOINT, RTT_LLMSPEECH_ENDPOINT, or AZURE_SPEECH_ENDPOINT
python transcribe.py --backend mai --chunk-seconds 15
# or just:
./run-mai.sh
# pick a different MAI model:
python transcribe.py --backend mai --mai-model MAI-Transcribe-1.5
```

### Aliases
| Alias | Backend |
|---|---|
| `rtt` | openai (gpt-4o-transcribe-diarize) — default |
| `rttheb` | llmspeech (LLM Speech, en-US+he-IL) — default |
| `rttold` | classic Conversation Transcriber (fallback / A/B baseline) |
| `rtthebold` | classic continuous-LID Hebrew (true-realtime fallback) |
| `rttvibe` | Local VibeVoice-ASR 7B — Hebrew/English/mixed + speakers, no cloud. 30 s chunks (~30–45 s lag), ~22 GB memory. Needs a separate VibeVoice server (`vibe_server.py`, not part of this repo); point `RTT_VIBE_HOME` at its folder. Optional `RTT_VIBE_PROMPT="names, terms"`. |
| `run-mai.sh` | mai (MAI-Transcribe-2 batch diarization) — eval, no alias repoint yet |

### Session controls (all aliases)
Split view by default: transcript on the left, live summary on the right, status bar on top.
Keys: `space` pause/resume · `m` mark · `M` mark with note · `u` mute my mic · `c`/`e` copy transcript (full/new) ·
`s`/`a`/`d` copy summary/action items/decisions · `n` summarize now · `r` rename speaker · `q q` stop · `?` help.
From any terminal: `rtt-ctl status|pause|resume|mark "note"|mute|unmute|summarize|stop`.
Click a speaker line to rename it (also fixes the summary). `z` undoes; every action is logged in `sessions/<id>/actions.jsonl` (`rtt-ctl log`). Offline or from scripts: `rtt-align names|refine|tidy <transcript> <teams.txt|clip|->` (see meetalign/README.md). Copy the Teams/Zoom transcript and press `t` to put real names on the lines, or `T` to also take its wording and learn misheard names. Stopped by mistake? `rtt -c` picks the last session up again (`rtt --resume <id>` for a specific one; exit prints both). `o` = off the record (minutes, or click/paste a line's time to delete from that line on), `b` = switch backend live (arrows to choose, Enter to pick). Agents can drive all of this through the MCP server `rtt-mcp.sh` (openspec/features/mcp-server.md).
Meeting watcher (offers to start RTT when a Teams/Zoom call grabs the mic): `./install-meeting-watch.sh`.
`--record` saves audio (separate meeting/mic tracks) to `sessions/<id>/`; `rtt-post` re-transcribes it locally with VibeVoice.
Live summary uses GitHub Copilot `gpt-6.1-terra` (on by default for every backend; `--no-summary` / `RTT_SUMMARY=0` to disable, `--summary` / `RTT_SUMMARY=1` to re-enable). See `openspec/features/session-controls.md`.


**Auth:** Both reuse Azure AD via `DefaultAzureCredential` (fresh bearer token minted on demand, 30-min cache), or an API key if `AZURE_SPEECH_KEY` is set. The identity needs **Cognitive Services OpenAI User** (openai backend) or **Cognitive Services Speech User** (llmspeech backend) on the respective resource.

**Latency vs accuracy:** larger `--chunk-seconds` = more accurate but lands further behind the speaker; smaller = more live but cuts mid-sentence and resets diarization more often. Speaker numbers are not guaranteed stable across chunks.

## rtt-cli — interactive control (menu / wizard / chat)

`rtt-cli` is a zero-dependency front-end for configuring and launching RTT without
memorizing flags. It reads a single declarative schema (`config_schema.py`), so
every option — current and future — is available in all three modes automatically.

```bash
rtt-cli                       # main menu (pick a profile, tweak, launch)
rtt-cli --wizard              # walk through every applicable option, grouped
rtt-cli --chat                # natural-language config
rtt-cli --profile rttheb      # start from a profile, go to the action menu
rtt-cli --profile rtt --launch        # validate + launch, no menu
rtt-cli --print-argv --profile rtt    # non-interactive: print the transcribe.py args
```

**Profiles** mirror the shell aliases: `rtt` (openai diarize), `rttheb`
(LLM Speech Hebrew/English), `rttold` / `rtthebold` (classic Speech SDK),
`local` (offline Whisper).

**Chat mode** understands plain language, e.g.:
- `use hebrew` → llmspeech + `en-US,he-IL`
- `switch to local whisper` → local backend
- `chunk 8` / `faster` / `slower` → tune the chunk window
- `add my mic` → mix your microphone in
- `save to notes.txt` → set the output file
- `show` / `launch` / `wizard` / `help` / `quit`

**Extending it:** to add a new option, add one `Field(...)` entry to
`config_schema.py`. It then appears in the wizard, is understood by the chat
parser (via its `aliases`), is validated, and is emitted into the launch command —
no UI code changes. Named configs save to `~/.config/rtt-cli/`.

## Testing with an audio file
You can skip live capture and feed a WAV/FLAC/MP3 file using `--input-file path`. Audio is resampled to 16 kHz mono on the fly with PyAV, which keeps the pipeline identical to live capture. Combine `--skip-seconds` and `--max-seconds` to preview a slice of a long meeting recording. Use `ffmpeg` or `sox` to convert other formats if needed.

## Troubleshooting
- **401 Authentication error (Azure)**: Check the following:
  1. **Stale `AZURE_SPEECH_KEY` in your shell**: If the key is commented out in `.env` but was previously exported in your terminal, it will still be used and may be invalid. Run `unset AZURE_SPEECH_KEY` and retry.
  2. **Azure AD token format**: When using Azure AD auth (no API key), `AZURE_SPEECH_RESOURCE_ID` must be set. The Speech SDK requires the token in `aad#<resource-id>#<token>` format; the tool handles this automatically when the resource ID is configured.
  3. **Missing RBAC role**: Your Azure AD identity needs **Cognitive Services Speech User** (or broader) on the Speech resource.
  4. **`disableLocalAuth=true`**: If your resource disables key auth, use Azure AD auth instead, or add a `SecurityControl: Ignore` tag to bypass the policy.
- If you see `PortAudioError`, confirm macOS microphone permissions for the terminal and that the selected device supports the chosen sample rate (default 16 kHz).
- Azure mode requires the Speech SDK. If you only need local mode, you can omit the Azure package from `requirements.txt` and reinstall.
- Whisper models beyond `small` benefit from Apple Silicon acceleration; set `--compute cpu`, `--compute metal`, or `--compute auto` depending on your setup.
- **urllib3 OpenSSL warning**: This is a known compatibility issue with LibreSSL on macOS and doesn't affect functionality.
