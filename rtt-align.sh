#!/usr/bin/env bash
# rtt-align: line up an RTT transcript with a Teams/Zoom transcript (names, wording, tidy).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/.venv/bin/python" "$REPO_DIR/rtt_align.py" "$@"
