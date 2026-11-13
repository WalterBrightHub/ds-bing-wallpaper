#!/bin/bash
# Compile the native wallpaper helper into bin/wallpaperctl.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.runtime/modulecache"
mkdir -p "$CACHE" "$ROOT/bin"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "! swiftc not found — the app will fall back to AppleScript for setting wallpaper" >&2
  echo "  install Xcode Command Line Tools: xcode-select --install" >&2
  exit 0
fi

# The compiler writes module caches outside the project by default; keep them local.
swiftc -O -module-cache-path "$CACHE" -o "$ROOT/bin/wallpaperctl" "$ROOT/src/wallpaperctl.swift"
chmod +x "$ROOT/bin/wallpaperctl"
echo "√ built bin/wallpaperctl"
