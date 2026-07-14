#!/usr/bin/env bash
# build_release.sh -- Deterministic customer release archive from explicit allowlist.
# No publication, upload, or signing side effects.
set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly PYTHON_BIN="${PYTHON_BIN:-python3.12}"
readonly OUTPUT_DIR="${OUTPUT_DIR:-$REPO_DIR/dist/releases}"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

usage() {
    cat <<'EOF'
Usage: build_release.sh [options]

Build a versioned, deterministic application archive from the repository
allowlist. Produces SHA-256 checksum manifest and SBOM evidence inside the
archive under release/.

Options:
  --output-dir PATH       Output directory (default: dist/releases)
  --skip-portal-dist        Do not require portal/dist (not for production)
  --require-airgap          Require dist/airgap prestaged wheels/manifest
  --source-date-epoch N     Deterministic archive timestamp (default: 1700000000)
  -h, --help                Show this help

Environment:
  RELEASE_GIT_REVISION      Optional revision string recorded in manifest/SBOM
  SOURCE_DATE_EPOCH         Overrides --source-date-epoch when set
  PYTHON_BIN                Python interpreter (default: python3.12)
EOF
}

REQUIRE_PORTAL_DIST=true
REQUIRE_AIRGAP=false
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1700000000}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            [[ $# -ge 2 ]] || err "Missing value for --output-dir"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --skip-portal-dist)
            REQUIRE_PORTAL_DIST=false
            shift
            ;;
        --require-airgap)
            REQUIRE_AIRGAP=true
            shift
            ;;
        --source-date-epoch)
            [[ $# -ge 2 ]] || err "Missing value for --source-date-epoch"
            SOURCE_DATE_EPOCH="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "Unknown argument: $1"
            ;;
    esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || err "Missing Python interpreter: $PYTHON_BIN"

args=(
    -m ato_operator.release_packaging_cli build
    --project-root "$REPO_DIR"
    --output-dir "$OUTPUT_DIR"
    --source-date-epoch "$SOURCE_DATE_EPOCH"
)
if [[ "$REQUIRE_PORTAL_DIST" == "true" ]]; then
    args+=(--require-portal-dist)
else
    args+=(--skip-portal-dist)
fi
if [[ "$REQUIRE_AIRGAP" == "true" ]]; then
    args+=(--require-airgap)
fi

info "Building release archive from $REPO_DIR"
"$PYTHON_BIN" "${args[@]}"
info "Release build complete"
