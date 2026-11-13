#!/bin/bash
# Render the app icon into AppIcon.iconset and pack it with iconutil.
#
# If assets/app-icon-source.png exists (the user-supplied artwork, kept out of
# git) it is used as the icon. Otherwise a programmatic fallback icon is drawn,
# so a fresh clone can still build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.runtime/modulecache"
WORK="$ROOT/.runtime/icon"
SOURCE="$ROOT/assets/app-icon-source.png"
mkdir -p "$CACHE" "$WORK"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "! swiftc not found — building without a custom icon" >&2
  exit 0
fi

rm -rf "$ROOT/AppIcon.iconset"

if [ -f "$SOURCE" ]; then
  echo "→ icon source: assets/app-icon-source.png"
  swiftc -O -module-cache-path "$CACHE" -o "$WORK/make-icon-photo" "$ROOT/scripts/make-icon-photo.swift"
  "$WORK/make-icon-photo" "$SOURCE" "$ROOT/AppIcon.iconset"
else
  echo "→ icon source: built-in fallback artwork（未找到 assets/app-icon-source.png）"
  swiftc -O -module-cache-path "$CACHE" -o "$WORK/make-icon" "$ROOT/scripts/make-icon.swift"
  "$WORK/make-icon" "$ROOT/AppIcon.iconset"
fi

iconutil -c icns "$ROOT/AppIcon.iconset" -o "$ROOT/AppIcon.icns"
echo "√ built AppIcon.icns"
