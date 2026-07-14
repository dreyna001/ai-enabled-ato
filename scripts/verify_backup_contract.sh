#!/usr/bin/env bash
# verify_backup_contract.sh -- Validate customer-selected backup declarations fail safely.
# Does not invoke a backup vendor or perform backup I/O.
set -euo pipefail
IFS=$'\n\t'

readonly CONFIG_DIR="/etc/ato-analyzer"
readonly RUNTIME_CONFIG_PATH="$CONFIG_DIR/runtime-config.json"
readonly CREDENTIALS_DIR="$CONFIG_DIR/credentials"
readonly INSTALL_DIR="/opt/ato-analyzer"

PRE_UPGRADE=false

usage() {
    cat <<'EOF'
Usage: verify_backup_contract.sh [options]

Read BACKUP_* declarations from runtime JSON and verify that customer-owned
backup prerequisites are present. Exits non-zero when backup is enabled in JSON
but required customer inputs are missing (HS-008). Does not run backup jobs.

Options:
  --pre-upgrade          Informational mode used by upgrade.sh
  -h, --help             Show this help
EOF
}

err() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }
info() { echo "  $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pre-upgrade) PRE_UPGRADE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown argument: $1" ;;
    esac
done

resolve_python() {
    if [[ -x "$INSTALL_DIR/venv/bin/python" ]]; then
        echo "$INSTALL_DIR/venv/bin/python"
        return 0
    fi
    command -v python3.12 >/dev/null 2>&1 && { command -v python3.12; return 0; }
    command -v python3 >/dev/null 2>&1 && { command -v python3; return 0; }
    err "Python interpreter not found for backup contract validation"
}

[[ -f "$RUNTIME_CONFIG_PATH" ]] || {
    if [[ "$PRE_UPGRADE" == "true" ]]; then
        warn "Runtime config missing; backup contract not verified (HS-008 remains open)"
        exit 0
    fi
    err "Missing runtime config: $RUNTIME_CONFIG_PATH"
}

PYBIN="$(resolve_python)"

result="$("$PYBIN" - "$RUNTIME_CONFIG_PATH" "$CREDENTIALS_DIR" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
credentials_dir = Path(sys.argv[2])
config = json.loads(config_path.read_text(encoding="utf-8"))

enabled = bool(config.get("BACKUP_OFF_HOST_ENABLED"))
encrypted = bool(config.get("BACKUP_ENCRYPTION_ENABLED"))
ownership = str(config.get("BACKUP_KEY_OWNERSHIP", "")).strip()
key_ref = config.get("BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE") or {}
identifier = str(key_ref.get("identifier", "")).strip()

issues = []
if enabled:
    if ownership != "customer":
        issues.append("BACKUP_KEY_OWNERSHIP must be 'customer' when BACKUP_OFF_HOST_ENABLED=true")
    if encrypted:
        if not identifier:
            issues.append("BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE.identifier is required")
        else:
            key_path = credentials_dir / identifier
            if not key_path.is_file() or key_path.stat().st_size < 32:
                issues.append(f"backup encryption key credential missing or too short: {key_path}")
    issues.append(
        "customer backup target selection and verified restore drill remain operator-owned (HS-008)"
    )

print(json.dumps({"enabled": enabled, "issues": issues}))
PY
)"

enabled="$(printf '%s' "$result" | "$PYBIN" -c 'import json,sys; print(json.load(sys.stdin)["enabled"])')"
issues_json="$(printf '%s' "$result" | "$PYBIN" -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["issues"]))')"

if [[ "$enabled" == "true" ]]; then
    info "Backup declarations enabled in runtime JSON"
    "$PYBIN" -c '
import json, sys
for issue in json.loads(sys.argv[1]):
    print(f"  CHECK: {issue}")
' "$issues_json"
    if [[ "$PRE_UPGRADE" == "true" ]]; then
        warn "Backup contract incomplete for production claims; upgrade may continue for non-production hosts"
        exit 0
    fi
    err "Backup contract verification failed (HS-008). Supply customer backup target and key ownership before production readiness."
fi

info "Backup disabled in runtime JSON or contract satisfied for scaffold verification"
exit 0
