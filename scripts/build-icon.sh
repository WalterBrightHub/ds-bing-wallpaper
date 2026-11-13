#!/bin/bash
# Render the app icon (AppKit) into an .iconset and pack it with iconutil.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.runtime/modulecache"
WORK="$ROOT/.runtime/icon"
mkdir -p "$CACHE" "$WORK"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "! swiftc not found — building without a custom icon" >&2
  exit 0
fi

swiftc -O -module-cache-path "$CACHE" -o "$WORK/make-icon" "$ROOT/scripts/make-icon.swift"
rm -rf "$ROOT/AppIcon.iconset"
"$WORK/make-icon" "$ROOT/AppIcon.iconset"
iconutil -c icns "$ROOT/AppIcon.iconset" -o "$ROOT/AppIcon.icns"
echo "√ built AppIcon.icns"
