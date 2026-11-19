#!/usr/bin/env bash
# Install / remove the RTT meeting watcher as a LaunchAgent (starts at login).
#   ./install-meeting-watch.sh          install + start
#   ./install-meeting-watch.sh remove   stop + uninstall
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.idanshimon.rtt.meeting-watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/.rtt/meeting-watch.log"

if [[ "${1:-}" == "remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

mkdir -p "$HOME/.rtt" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$REPO_DIR/.venv/bin/python</string><string>$REPO_DIR/meeting_watch.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL (log: $LOG). Remove with: $0 remove"
