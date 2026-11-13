#!/bin/bash
# DS Bing Wallpaper launcher.
#
# Starts the local server if it is not already running, then opens the web UI.
# `--background` skips opening a browser (used by the login agent).
set -u

PROJECT="__PROJECT_DIR__"
DATA="$HOME/Library/Application Support/DSBingWallpaper"
LOG="$DATA/launcher.log"
URL_FILE="$DATA/server-url"
PORT="${DSBW_PORT:-8791}"
OPEN_UI=1
[ "${1:-}" = "--background" ] && OPEN_UI=0

mkdir -p "$DATA"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG" 2>&1
}

health() {
  # $1 = base url without trailing slash
  /usr/bin/curl -fsS --max-time 1 "$1/api/health" >/dev/null 2>&1
}

resolve_url() {
  if [ -f "$URL_FILE" ]; then
    local candidate
    candidate="$(tr -d '[:space:]' <"$URL_FILE")"
    candidate="${candidate%/}"
    if [ -n "$candidate" ] && health "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  fi
  if health "http://127.0.0.1:$PORT"; then
    printf 'http://127.0.0.1:%s' "$PORT"
    return 0
  fi
  return 1
}

URL="$(resolve_url || true)"
if [ -n "${URL}" ]; then
  log "already running at $URL"
  [ "$OPEN_UI" = "1" ] && open "$URL"
  exit 0
fi

PYTHON="/usr/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
  osascript -e 'display alert "DS 必应壁纸" message "找不到 python3，请先安装 Xcode Command Line Tools。"' >/dev/null 2>&1
  log "python3 not found"
  exit 1
fi

# Finder-launched apps do not inherit the shell PATH.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

log "starting server (project=$PROJECT port=$PORT)"
nohup "$PYTHON" "$PROJECT/server/app.py" --port "$PORT" --no-open >>"$LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  sleep 0.25
  URL="$(resolve_url || true)"
  [ -n "${URL}" ] && break
done

if [ -z "${URL}" ]; then
  log "server failed to start (pid=$SERVER_PID); see $LOG"
  osascript -e "display alert \"DS 必应壁纸启动失败\" message \"请查看日志：$LOG\"" >/dev/null 2>&1
  exit 1
fi

log "server ready at $URL"
if [ "$OPEN_UI" = "1" ]; then
  open "$URL"
fi
exit 0
