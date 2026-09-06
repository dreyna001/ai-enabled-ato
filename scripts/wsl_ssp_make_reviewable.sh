#!/usr/bin/env bash
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DSN_FILE="/etc/ato-analyzer/credentials/database-dsn"
readonly PYTHON="${PYTHON:-/opt/ato-analyzer/venv/bin/python3}"

if [[ ! -r "$DSN_FILE" ]]; then
  echo "ERROR: run with sudo so ${DSN_FILE} can be read" >&2
  exit 1
fi

export ATO_DATABASE_DSN="$(tr -d '\n' <"$DSN_FILE")"
exec "$PYTHON" "$REPO_ROOT/scripts/wsl_ssp_make_reviewable.py" "$@"
