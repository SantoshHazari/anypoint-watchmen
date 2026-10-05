#!/bin/bash
# Start Watchmen on macOS (localhost only). Usage: bash run_mac.sh [port]
cd "$(dirname "$0")"
PORT="${1:-5050}"
echo "Watchmen: open http://localhost:$PORT in Safari. Press Control+C to stop."
(sleep 3 && open "http://localhost:$PORT") &
WATCHMEN_HOST=127.0.0.1 WATCHMEN_PORT="$PORT" .venv/bin/python web/app.py
