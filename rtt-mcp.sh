#!/usr/bin/env bash
# rtt-mcp: MCP server (stdio) to control RTT from an agent. See rtt_mcp.py.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec env -u PYTHONPATH -u PYTHONHOME "$REPO_DIR/.venv/bin/python" "$REPO_DIR/rtt_mcp.py" "$@"
