#!/usr/bin/env bash
# verify_release.sh -- Offline verification for customer release archives.
set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly PYTHON_BIN="${PYTHON_BIN:-python3.12}"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

usage() {
    cat <<'EOF'
Usage: verify_release.sh [options] ARCHIVE.tar.gz

Verify allowlist membership, checksum manifest, SBOM presence, config schema
validity, migration head metadata, executable script modes, and absence of
secret-like paths. Does not extract the archive to disk.

Options:
  --signature PATH     Optional detached OpenPGP signature (.asc)
  --json               Emit machine-readable JSON report
  -h, --help           Show this help

Detached signature verification uses gpg when available. When gpg or a signature
file is unavailable, verification reports signature_status=unavailable and does
not claim the archive is signed.
EOF
}

SIGNATURE_PATH=""
EMIT_JSON=false
ARCHIVE_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --signature)
            [[ $# -ge 2 ]] || err "Missing value for --signature"
            SIGNATURE_PATH="$2"
            shift 2
            ;;
        --json)
            EMIT_JSON=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            err "Unknown argument: $1"
            ;;
        *)
            if [[ -z "$ARCHIVE_PATH" ]]; then
                ARCHIVE_PATH="$1"
                shift
            else
                err "Unexpected argument: $1"
            fi
            ;;
    esac
done

[[ -n "$ARCHIVE_PATH" ]] || err "ARCHIVE.tar.gz path is required (use --help)"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || err "Missing Python interpreter: $PYTHON_BIN"

args=(
    -m ato_operator.release_packaging_cli verify
    --project-root "$REPO_DIR"
    --archive "$ARCHIVE_PATH"
)
if [[ -n "$SIGNATURE_PATH" ]]; then
    args+=(--signature "$SIGNATURE_PATH")
fi
if [[ "$EMIT_JSON" == "true" ]]; then
    args+=(--json)
fi

info "Verifying release archive: $ARCHIVE_PATH"
"$PYTHON_BIN" "${args[@]}"
