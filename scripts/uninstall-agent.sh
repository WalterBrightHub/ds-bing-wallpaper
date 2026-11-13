#!/bin/bash
# Stop and remove the login agent (the server keeps running until you quit it).
set -uo pipefail
LABEL="com.deepseek.dsbingwallpaper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
if [ -f "$PLIST" ]; then
  rm -f "$PLIST" && echo "√ removed $PLIST"
else
  echo "· no agent installed"
fi

pkill -f "server/app.py" >/dev/null 2>&1 && echo "√ stopped running server" || true
echo "√ done"
