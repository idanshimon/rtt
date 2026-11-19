# VibeVoice local backend (`--backend vibe`, `rttvibe`)

Status: shipped 2026-11-05.

## Why
A local option with strong diarization and Hebrew/English support, no cloud, no per-hour cost.

## Behavior
- `rttvibe` → run-vibe.sh → `--backend vibe --chunk-seconds ${RTT_VIBE_CHUNK:-30}`.
- RTT starts the server if it is not answering (`ensure_vibe_server`), and stops it at exit when
  the session used vibe (see stopping-and-shutdown).
- Optional `RTT_VIBE_PROMPT` ("names, terms") biases recognition (sent as `X-Prompt`).
- Non-speech tags such as `[Music]` are dropped.
- Chunk bounds 10–120 s (the model needs context for speakers; each call has fixed overhead).

## Interfaces
Server `speech-bench/vibe_server.py` on `RTT_VIBE_URL` (127.0.0.1:8765): `POST /transcribe`
(WAV body, optional `X-Prompt`) → `{segments: [{Start, End, Speaker, Content}]}`, `GET /health`.
`RTT_VIBE_HOME` (default: `speech-bench` next to the repo folder) holds the venv (`.venv-vv`, transformers, torch MPS)
and the model (`models/VibeVoice-ASR-HF`, 16 GB). `max_new_tokens = max(4000, seconds × 12)`.

## Implementation
`VibeVoiceLocalBackend` in chunked_backends.py (same base class as openai/llmspeech, so chunk
window, speaker tracking and spoken time apply); `ensure_vibe_server`, `stop_vibe_server` in
transcribe.py.

## Verification
speech-bench/RESULTS.md. English 10-minute meeting: ~210 s per 10 min, best text and speakers of
the tested local options, but hallucinated German and [Music] on a screen-demo stretch. Hebrew
4-speaker podcast 10 min: best result (292 s, 52 segments); ivrit-ai + Nemotron diarization second;
Nemotron ASR unusable for Hebrew. Resources: 2 min audio in 49.7 s, peak memory ~21.8 GB, low CPU,
no swap. tests/test_vibe_backend.py.

## Limits
~22 GB memory while the server runs. Slower than real time at short chunks; use ≥30 s.
