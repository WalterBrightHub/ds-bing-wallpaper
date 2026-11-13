#!/bin/bash
# Assemble dist/DS 必应壁纸.app from the launcher script + icon.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="DS 必应壁纸"
APP="$ROOT/dist/$APP_NAME.app"
VERSION="$(/usr/bin/python3 -c 'import re,sys;print(re.search(r"VERSION = \"([^\"]+)\"", open(sys.argv[1]).read()).group(1))' "$ROOT/server/config.py")"

cd "$ROOT"
"$ROOT/scripts/build-setwall.sh"
"$ROOT/scripts/build-icon.sh"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Launcher script with the project path baked in.
sed "s|__PROJECT_DIR__|$ROOT|g" "$ROOT/launcher/launcher.sh" > "$APP/Contents/MacOS/DSBingWallpaper"
chmod 755 "$APP/Contents/MacOS/DSBingWallpaper"

if [ -f "$ROOT/AppIcon.icns" ]; then
  cp "$ROOT/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>zh_CN</string>
	<key>CFBundleDisplayName</key>
	<string>$APP_NAME</string>
	<key>CFBundleName</key>
	<string>DS Bing Wallpaper</string>
	<key>CFBundleExecutable</key>
	<string>DSBingWallpaper</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundleIdentifier</key>
	<string>com.deepseek.dsbingwallpaper</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>$VERSION</string>
	<key>CFBundleVersion</key>
	<string>$VERSION</string>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSHumanReadableCopyright</key>
	<string>DS Bing Wallpaper · 本地壁纸管理，数据不出本机</string>
</dict>
</plist>
PLIST

printf 'APPL????' > "$APP/Contents/PkgInfo"

# Unsigned bundles edit-then-run poorly on recent macOS; ad-hoc sign it.
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
fi

echo "√ built $APP"
