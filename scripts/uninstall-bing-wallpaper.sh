#!/bin/bash
# Remove the official Microsoft Bing Wallpaper app so the two do not fight
# over the desktop picture. Nothing here touches DS Bing Wallpaper.
set -uo pipefail

APPS=(
  "/Applications/Microsoft Bing Wallpaper.app"
  "$HOME/Applications/Microsoft Bing Wallpaper.app"
)
AGENT_PLIST="$HOME/Library/LaunchAgents/Microsoft Bing Wallpaper.plist"
SUPPORT="$HOME/Library/Application Support/com.microsoft.bing.wallpaper"
LABEL="Microsoft Bing Wallpaper"
ASSUME_YES=0
KEEP_DATA=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    --keep-data) KEEP_DATA=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$ASSUME_YES" -ne 1 ]; then
  echo "即将卸载官方「Microsoft Bing Wallpaper」："
  printf '  - %s\n' "${APPS[@]}"
  echo "  - $AGENT_PLIST"
  [ "$KEEP_DATA" -eq 1 ] || echo "  - $SUPPORT （已下载的官方壁纸缓存）"
  read -r -p "确认继续？[y/N] " reply
  case "$reply" in [yY]*) ;; *) echo "已取消"; exit 1 ;; esac
fi

# 1. stop it
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
pkill -x "Microsoft Bing Wallpaper" >/dev/null 2>&1 || true
osascript -e 'tell application "Microsoft Bing Wallpaper" to quit' >/dev/null 2>&1 || true
sleep 1

# 2. drop the login item / launch agent
if [ -f "$AGENT_PLIST" ]; then
  rm -f "$AGENT_PLIST" && echo "√ removed $AGENT_PLIST"
fi
defaults delete com.microsoft.bing.wallpaper >/dev/null 2>&1 || true

# 3. remove the bundle (guard against odd paths before deleting)
for app in "${APPS[@]}"; do
  if [ -d "$app" ]; then
    case "$app" in
      */"Microsoft Bing Wallpaper.app") rm -rf "$app" && echo "√ removed $app" ;;
      *) echo "! refusing to remove unexpected path: $app" >&2 ;;
    esac
  fi
done

# 4. optional cache
if [ "$KEEP_DATA" -ne 1 ] && [ -d "$SUPPORT" ]; then
  case "$SUPPORT" in
    */com.microsoft.bing.wallpaper) rm -rf "$SUPPORT" && echo "√ removed $SUPPORT" ;;
    *) echo "! refusing to remove unexpected path: $SUPPORT" >&2 ;;
  esac
fi

echo "√ 官方 Bing 壁纸已卸载。"
echo "  提示：如果「系统设置 → 通用 → 登录项」里还留有它，请在那里再移除一次。"
