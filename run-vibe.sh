#!/usr/bin/env bash
set -euo pipefail

# `rttvibe`: fully local transcription with VibeVoice-ASR 7B (Hebrew/English/mixed + speakers).
# Starts vibe_server.py from $RTT_VIBE_HOME (default ../speech-bench, not part of this repo) on 127.0.0.1:8765 if it isn't already running
# (first load ~10 s, ~22 GB unified memory), then runs RTT in 30 s chunks.
# Optional: RTT_VIBE_PROMPT="names, terms" to bias recognition.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="${RTT_VIBE_HOME:-$(dirname "$REPO_DIR")/speech-bench}"   # default: a sibling folder
URL="${RTT_VIBE_URL:-http://127.0.0.1:8765}"

if ! curl -fs "$URL/health" >/dev/null 2>&1; then
  echo "Starting VibeVoice server (log: $BENCH/vibe_server.log)..."
  (cd "$BENCH" && nohup .venv-vv/bin/python vibe_server.py >"$BENCH/vibe_server.log" 2>&1 &)
  for _ in $(seq 1 120); do
    curl -fs "$URL/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fs "$URL/health" >/dev/null 2>&1 || { echo "VibeVoice server failed to start; see $BENCH/vibe_server.log" >&2; exit 1; }
fi

exec "$REPO_DIR/run.sh" --backend vibe --chunk-seconds "${RTT_VIBE_CHUNK:-30}" "$@"
