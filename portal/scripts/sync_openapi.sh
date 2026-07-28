#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORTAL_OPENAPI_DIR="$ROOT/portal/openapi"
mkdir -p "$PORTAL_OPENAPI_DIR"
PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" \
  "$ROOT/scripts/export_ssp_openapi.py" \
  "$ROOT/docs/contracts/ssp-openapi.json" \
  "$PORTAL_OPENAPI_DIR/portal.openapi.json"
cp "$ROOT/docs/contracts/domain.schema.json" "$PORTAL_OPENAPI_DIR/domain.schema.json"
echo "Generated SSP OpenAPI and synced portal contract assets"
