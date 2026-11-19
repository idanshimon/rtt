#!/usr/bin/env bash
# rtt-post: re-transcribe a recorded session with local VibeVoice (consistent speakers).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/.venv/bin/python" "$REPO_DIR/rtt_post.py" "$@"
