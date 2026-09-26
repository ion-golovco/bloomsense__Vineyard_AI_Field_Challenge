#!/usr/bin/env bash
# The pitch demo in one command: the web app on http://127.0.0.1:${PORT} and the Telegram bot, both on the current
# data/generated files (see docs/DEMO.md for the click path). Ctrl+C stops both.
#
#   scripts/demo.sh
#
# Env: PORT (default 8000), HOST (default 127.0.0.1; 0.0.0.0 lets a phone on the same Wi-Fi open the app),
#      BUILD=1 rebuilds web/dist first, TELEGRAM_BOT_TOKEN (read from .env when unset; no token = no bot).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
LOGS="$ROOT/data/generated/work/demo"
mkdir -p "$LOGS"

if [ "${BUILD:-0}" = 1 ] || [ ! -f "$ROOT/web/dist/index.html" ]; then
  (cd "$ROOT/web" && npm run build)
fi

# only the token line, so nothing else in .env leaks into this shell
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && [ -f "$ROOT/.env" ]; then
  TELEGRAM_BOT_TOKEN="$(sed -n 's/^TELEGRAM_BOT_TOKEN=["'\'']\{0,1\}\([^"'\'']*\)["'\'']\{0,1\}[[:space:]]*$/\1/p' "$ROOT/.env" | head -n 1)"
fi

pids=()
trap 'kill ${pids[@]+"${pids[@]}"} 2>/dev/null || true' EXIT INT TERM
cd "$ROOT/backend"

if curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/scene"; then
  echo "app: already running on port $PORT, reusing it"
else
  uv run --frozen uvicorn marcaj.api:app --host "$HOST" --port "$PORT" > "$LOGS/app.log" 2>&1 &
  pids+=($!)
  echo "app: starting on http://$HOST:$PORT (log $LOGS/app.log)"
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" uv run --frozen python -m marcaj.telegram_bot > "$LOGS/bot.log" 2>&1 &
  pids+=($!)
  echo "bot: starting (log $LOGS/bot.log); only one bot process may run per token"
else
  echo "bot: skipped, no TELEGRAM_BOT_TOKEN in the environment or .env"
fi

for _ in $(seq 60); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/scene" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/scene" && echo "app: ready at http://127.0.0.1:$PORT" || echo "app: not answering yet, see $LOGS/app.log"
if [ "${#pids[@]}" -gt 0 ]; then wait; fi
