#!/usr/bin/env bash
# rtt-ctl: control the running RTT session (pause/resume/mark/mute/status/stop).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$REPO_DIR/.venv/bin/python" "$REPO_DIR/rtt_ctl.py" "$@"
