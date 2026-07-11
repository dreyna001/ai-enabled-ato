#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORTAL_OPENAPI_DIR="$ROOT/portal/openapi"
mkdir -p "$PORTAL_OPENAPI_DIR"
cp "$ROOT/docs/contracts/openapi.json" "$PORTAL_OPENAPI_DIR/portal.openapi.json"
cp "$ROOT/docs/contracts/domain.schema.json" "$PORTAL_OPENAPI_DIR/domain.schema.json"
echo "Synced portal OpenAPI assets from docs/contracts/"
