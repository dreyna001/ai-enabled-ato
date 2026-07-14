#!/usr/bin/env bash
# prestage_airgap_deps.sh -- Download Python wheels and document portal prebuild for airgap hosts.
# Retains JSON + credential-reference architecture; does not embed secret bytes.
set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly STAGE_ROOT="${STAGE_ROOT:-$REPO_DIR/dist/airgap}"
readonly PYTHON_BIN="${PYTHON_BIN:-python3.12}"
readonly WHEEL_DIR="$STAGE_ROOT/wheels"
readonly MANIFEST_PATH="$STAGE_ROOT/manifest.json"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

usage() {
    cat <<'EOF'
Usage: prestage_airgap_deps.sh

Create an offline dependency bundle under dist/airgap/:
  - wheels/     pip-downloaded Python dependencies for ato_service
  - manifest.json  non-secret staging metadata and portal prebuild instructions

On the airgap host, copy the repository (or release tarball), install wheels with:
  python3.12 -m venv /opt/ato-analyzer/venv
  /opt/ato-analyzer/venv/bin/pip install --no-index --find-links dist/airgap/wheels /opt/ato-analyzer

Provision runtime JSON and credential files separately; never embed secrets in the bundle.
EOF
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }
[[ $# -eq 0 ]] || err "prestage_airgap_deps.sh accepts no arguments (use --help)"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || err "Missing Python interpreter: $PYTHON_BIN"
command -v pip >/dev/null 2>&1 || err "Missing pip"

mkdir -p "$WHEEL_DIR" || err "Failed to create wheel directory"

info "Downloading Python wheels to $WHEEL_DIR"
"$PYTHON_BIN" -m pip download -d "$WHEEL_DIR" "$REPO_DIR" \
    || err "pip download failed"

portal_index="$REPO_DIR/portal/dist/index.html"
portal_built=false
if [[ -f "$portal_index" ]]; then
    portal_built=true
fi

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$MANIFEST_PATH" <<EOF
{
  "schema_version": "1.0.0",
  "created_at": "$created_at",
  "python": "$("$PYTHON_BIN" --version 2>&1 | awk '{print $2}')",
  "wheel_dir": "wheels",
  "portal_bundle_built": $portal_built,
  "runtime_config_contract": "JSON non-secret settings with credential references only",
  "credential_layout": [
    "/etc/ato-analyzer/credentials/database-dsn",
    "/etc/ato-analyzer/credentials/audit-hmac-key"
  ],
  "portal_prebuild_command": "cd portal && npm ci && npm run build",
  "install_command": "sudo bash scripts/install.sh",
  "notes": "Customer backup target, IdP values, TLS material, and additional credential files remain out-of-band."
}
EOF

info "Wrote airgap manifest: $MANIFEST_PATH"
if [[ "$portal_built" != "true" ]]; then
    info "Portal bundle not built; run 'npm run build' in portal/ on a connected staging host before packaging"
fi
info "Airgap prestage complete"
