#!/usr/bin/env bash
# Stop the bounded portal E2E stack started by e2e-stack-start.sh.
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/e2e-stack-common.sh
source "$SCRIPT_DIR/e2e-stack-common.sh"
e2e_export_paths

touch "$E2E_STOP_FILE"

for name in portal analyzer-worker intake-poller api; do
  if e2e_stop_pidfile "$name"; then
    e2e_info "Stopped ${name}"
  fi
done

e2e_clear_ready
echo "E2E stack stopped."
