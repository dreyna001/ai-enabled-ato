#!/usr/bin/env bash
# Start the local Vite portal and proxy API calls to the WSL API.
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PORTAL_DIR="$(cd "$SCRIPT_DIR/../portal" && pwd)"
readonly PORTAL_HOST="${PORTAL_HOST:-127.0.0.1}"
readonly PORTAL_PORT="${PORTAL_PORT:-5173}"
export VITE_DEV_API_TARGET="${VITE_DEV_API_TARGET:-http://127.0.0.1:8001}"

command -v npm >/dev/null 2>&1 || {
    echo "ERROR: npm is required to start the portal" >&2
    exit 1
}

cd "$PORTAL_DIR"
if [[ ! -d node_modules ]]; then
    npm install
fi

echo "Starting ATO portal at http://${PORTAL_HOST}:${PORTAL_PORT}"
echo "Proxying API requests to ${VITE_DEV_API_TARGET}"
exec npm run dev -- --host "$PORTAL_HOST" --port "$PORTAL_PORT" --strictPort
