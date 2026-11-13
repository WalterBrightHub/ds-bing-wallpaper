#!/bin/bash
# Build the launcher app and install it into /Applications (fallback ~/Applications).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/build-app.sh"

SOURCE="$ROOT/dist/DS 必应壁纸.app"
if [ -w "/Applications" ]; then
  DEST="/Applications/DS 必应壁纸.app"
else
  mkdir -p "$HOME/Applications"
  DEST="$HOME/Applications/DS 必应壁纸.app"
fi

echo "→ installing to $DEST"
rm -rf "$DEST"
ditto "$SOURCE" "$DEST"
xattr -dr com.apple.quarantine "$DEST" >/dev/null 2>&1 || true

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ]; then
  "$LSREGISTER" -f "$DEST" >/dev/null 2>&1 || true
fi

echo "√ installed: $DEST"
echo "  已加入启动台（Launchpad），双击即可打开壁纸面板。"

if [ "${1:-}" != "--no-open" ]; then
  open "$DEST"
fi
