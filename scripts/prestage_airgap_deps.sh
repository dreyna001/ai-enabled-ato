#!/usr/bin/env bash
# prestage_airgap_deps.sh -- Download Python wheels and pin portal/npm evidence for airgap hosts.
# Retains JSON + credential-reference architecture; does not embed secret bytes.
set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly STAGE_ROOT="${STAGE_ROOT:-$REPO_DIR/dist/airgap}"
readonly PYTHON_BIN="${PYTHON_BIN:-python3.12}"
readonly WHEEL_DIR="$STAGE_ROOT/wheels"
readonly MANIFEST_PATH="$STAGE_ROOT/manifest.json"
readonly PORTAL_LOCK_PATH="$REPO_DIR/portal/package-lock.json"
readonly PORTAL_DIST_INDEX="$REPO_DIR/portal/dist/index.html"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

usage() {
    cat <<'EOF'
Usage: prestage_airgap_deps.sh [--verify-only]

Create or verify an offline dependency bundle under dist/airgap/:
  - wheels/        pip-downloaded Python dependencies for ato_service
  - manifest.json  pinned wheel, portal lock, and optional portal dist digests

Connected host (download):
  prestage_airgap_deps.sh

Airgap target (verify only, no network):
  prestage_airgap_deps.sh --verify-only

On the airgap host, copy the release archive or tree, then install wheels with:
  python3.12 -m venv /opt/ato-analyzer/venv
  /opt/ato-analyzer/venv/bin/pip install --no-index --find-links dist/airgap/wheels /opt/ato-analyzer

Provision runtime JSON and credential files separately; never embed secrets in the bundle.
EOF
}

sha256_file() {
    local path="$1"
    "$PYTHON_BIN" - <<'PY' "$path"
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
hasher = hashlib.sha256()
with path.open("rb") as handle:
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        hasher.update(chunk)
print(hasher.hexdigest())
PY
}

collect_portal_dist_digests() {
    local portal_dist_dir="$REPO_DIR/portal/dist"
    if [[ ! -d "$portal_dist_dir" ]]; then
        printf '[]'
        return 0
    fi
    "$PYTHON_BIN" - <<'PY' "$portal_dist_dir"
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
entries = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.is_symlink():
        continue
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    entries.append(
        {
            "relative_path": str(path.relative_to(root)).replace("\\", "/"),
            "sha256": hasher.hexdigest(),
            "size_bytes": path.stat().st_size,
        }
    )
print(json.dumps(entries, separators=(",", ":")))
PY
}

write_manifest() {
    local verify_only="$1"
    local portal_built=false
    local portal_lock_sha=""
    local wheels_json="[]"
    local portal_dist_json="[]"

    [[ -f "$PORTAL_LOCK_PATH" ]] || err "Missing portal lockfile: $PORTAL_LOCK_PATH"
    portal_lock_sha="$(sha256_file "$PORTAL_LOCK_PATH")"

    if [[ -f "$PORTAL_DIST_INDEX" ]]; then
        portal_built=true
        portal_dist_json="$(collect_portal_dist_digests)"
    fi

    if [[ -d "$WHEEL_DIR" ]]; then
        wheels_json="$(
            "$PYTHON_BIN" - <<'PY' "$WHEEL_DIR"
import hashlib
import json
import sys
from pathlib import Path

wheel_dir = Path(sys.argv[1]).resolve()
entries = []
for path in sorted(wheel_dir.glob("*.whl")):
    if not path.is_file() or path.is_symlink():
        continue
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    entries.append(
        {
            "filename": path.name,
            "sha256": hasher.hexdigest(),
            "size_bytes": path.stat().st_size,
        }
    )
print(json.dumps(entries, separators=(",", ":")))
PY
        )"
    fi

    if [[ "$verify_only" == "true" ]]; then
        [[ -f "$MANIFEST_PATH" ]] || err "Missing airgap manifest for verify-only mode: $MANIFEST_PATH"
        [[ -d "$WHEEL_DIR" ]] || err "Missing wheel directory for verify-only mode: $WHEEL_DIR"
        [[ "$wheels_json" != "[]" ]] || err "No pinned wheels found under $WHEEL_DIR"
    fi

    if [[ "$verify_only" != "true" ]]; then
        created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        cat > "$MANIFEST_PATH" <<EOF
{
  "schema_version": "1.1.0",
  "created_at": "$created_at",
  "python": "$("$PYTHON_BIN" --version 2>&1 | awk '{print $2}')",
  "wheel_dir": "wheels",
  "portal_bundle_built": $portal_built,
  "portal_package_lock_sha256": "$portal_lock_sha",
  "portal_dist_files": $portal_dist_json,
  "wheels": $wheels_json,
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
        return 0
    fi

    "$PYTHON_BIN" - <<'PY' "$MANIFEST_PATH" "$WHEEL_DIR" "$PORTAL_LOCK_PATH" "$portal_lock_sha" "$portal_built" "$portal_dist_json"
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
wheel_dir = Path(sys.argv[2])
portal_lock_path = Path(sys.argv[3])
expected_lock_sha = sys.argv[4]
portal_built = sys.argv[5] == "true"
portal_dist_json = sys.argv[6]

errors = []
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schema_version") != "1.1.0":
    errors.append("unsupported airgap manifest schema_version")

actual_lock_sha = hashlib.sha256(portal_lock_path.read_bytes()).hexdigest()
if manifest.get("portal_package_lock_sha256") != actual_lock_sha:
    errors.append("portal package-lock digest mismatch")
if expected_lock_sha != actual_lock_sha:
    errors.append("portal package-lock digest mismatch against workspace")

wheels = manifest.get("wheels")
if not isinstance(wheels, list) or not wheels:
    errors.append("manifest wheels list is missing or empty")

for entry in wheels or []:
    filename = entry.get("filename")
    expected = entry.get("sha256")
    size_bytes = entry.get("size_bytes")
    if not filename or not expected or size_bytes is None:
        errors.append("wheel entry is missing filename, sha256, or size_bytes")
        continue
    wheel_path = wheel_dir / filename
    if not wheel_path.is_file():
        errors.append(f"missing pinned wheel: {filename}")
        continue
    if wheel_path.stat().st_size != size_bytes:
        errors.append(f"wheel size mismatch for {filename}")
    actual = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    if actual != expected:
        errors.append(f"wheel digest mismatch for {filename}")

if manifest.get("portal_bundle_built") and portal_built:
    expected_files = manifest.get("portal_dist_files") or []
    dist_root = portal_lock_path.parent / "dist"
    for entry in expected_files:
        relative = entry.get("relative_path")
        expected = entry.get("sha256")
        size_bytes = entry.get("size_bytes")
        if not relative or not expected or size_bytes is None:
            errors.append("portal dist entry is missing relative_path, sha256, or size_bytes")
            continue
        target = dist_root / relative
        if not target.is_file():
            errors.append(f"missing portal dist file: {relative}")
            continue
        if target.stat().st_size != size_bytes:
            errors.append(f"portal dist size mismatch for {relative}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"portal dist digest mismatch for {relative}")

if errors:
    for message in errors:
        print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)
print("airgap prestage verification passed")
PY
}

VERIFY_ONLY=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verify-only)
            VERIFY_ONLY=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "Unknown argument: $1 (use --help)"
            ;;
    esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || err "Missing Python interpreter: $PYTHON_BIN"

if [[ "$VERIFY_ONLY" == "true" ]]; then
    write_manifest true
    exit 0
fi

command -v pip >/dev/null 2>&1 || err "Missing pip"
mkdir -p "$WHEEL_DIR" || err "Failed to create wheel directory"

info "Downloading Python wheels to $WHEEL_DIR"
"$PYTHON_BIN" -m pip download -d "$WHEEL_DIR" "$REPO_DIR" \
    || err "pip download failed"

write_manifest false

if [[ ! -f "$PORTAL_DIST_INDEX" ]]; then
    info "Portal bundle not built; run 'cd portal && npm ci && npm run build' before packaging"
fi
info "Airgap prestage complete"
