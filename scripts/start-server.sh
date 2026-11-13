#!/bin/bash
# Run the server in the foreground (development / debugging).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec /usr/bin/python3 "$ROOT/server/app.py" "$@"
