#!/bin/bash
# Install (or refresh) the login agent that keeps the server + hourly job alive.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.deepseek.dsbingwallpaper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="$HOME/Library/Application Support/DSBingWallpaper"
PYTHON="/usr/bin/python3"

mkdir -p "$HOME/Library/LaunchAgents" "$DATA"

cat > "$PLIST" <<PLISTFILE
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PYTHON</string>
		<string>$ROOT/server/app.py</string>
		<string>--no-open</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$ROOT</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>StandardOutPath</key>
	<string>$DATA/agent.out.log</string>
	<key>StandardErrorPath</key>
	<string>$DATA/agent.err.log</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
</dict>
</plist>
PLISTFILE

# Reload cleanly: bootout is a no-op (and returns non-zero) when not loaded.
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo "√ login agent installed: $PLIST"
echo "  服务会在登录时自动启动，并保持运行（崩溃后自动重启）。"
