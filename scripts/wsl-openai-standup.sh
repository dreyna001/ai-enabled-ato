#!/usr/bin/env bash
# wsl-openai-standup.sh -- Enable OpenAI text model + portal on an existing WSL install.
if grep -q $'\r' "$0" 2>/dev/null; then
  exec /usr/bin/env bash <(sed 's/\r$//' "$0") "$@"
fi
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly LOCAL_ENV="${ATO_LOCAL_ENV_FILE:-$REPO_DIR/config.local.env}"
readonly API_URL="${API_LOOPBACK_URL:-http://127.0.0.1:8001}"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

[[ "$(id -u)" -eq 0 ]] || err "Run as root: sudo bash scripts/wsl-openai-standup.sh"
grep -Eiq 'microsoft|wsl' /proc/version 2>/dev/null || err "Run inside WSL"
[[ -f "$LOCAL_ENV" ]] || err "Missing $LOCAL_ENV (copy config.local.env.example and set ATO_TEXT_MODEL_API_KEY)"
grep -Eq '^[[:space:]]*ATO_TEXT_MODEL_API_KEY=.+$' "$LOCAL_ENV" || err "$LOCAL_ENV must set a non-empty ATO_TEXT_MODEL_API_KEY line"

echo "=== ATO WSL OpenAI prod-like standup ==="
echo ""

info "Installing OpenAI portal runtime, credentials, and refreshed package bytes"
bash "$REPO_DIR/scripts/wsl-portal-enable.sh" --openai

info "Verifying OpenAI credential file"
[[ -f /etc/ato-analyzer/credentials/ato-local.env ]] || err "ato-local.env was not installed; check wsl-portal-enable output"

info "Verifying bundled analysis profiles"
for profile in fedramp-rev5-transition-moderate.json fisma-agency-security-moderate-draft.json; do
  [[ -f "/opt/ato-analyzer/reference/profiles/$profile" ]] || err "Missing /opt/ato-analyzer/reference/profiles/$profile (rerun install from repo root)"
done

info "Checking API liveness"
curl -fsS --max-time 5 "${API_URL%/}/health/live" >/dev/null

info "Checking runtime config uses OpenAI"
python3 - <<'PY'
import json
from pathlib import Path

config = json.loads(Path("/opt/ato-analyzer/runtime-config.json").read_text(encoding="utf-8"))
assert config.get("TEXT_MODEL_PROVIDER") == "openai_compatible", config.get("TEXT_MODEL_PROVIDER")
assert config.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is True
assert config.get("TEXT_MODEL_ENDPOINT_URL", "").startswith("https://")
print("  TEXT_MODEL_PROVIDER=openai_compatible")
print("  TEXT_MODEL_NAME=", config.get("TEXT_MODEL_NAME"))
fisma = config.get("FISMA_ANALYSIS_PROFILE_FILE_REFERENCE")
if isinstance(fisma, dict):
    print("  FISMA_ANALYSIS_PROFILE_FILE_REFERENCE.path=", fisma.get("path"))
PY

echo ""
echo "OpenAI standup complete."
echo "  API:    ${API_URL}/health/live"
echo "  Portal: bash scripts/start-portal.sh  (then open http://localhost:5173)"
echo ""
echo "Near-prod test path (FedRAMP Rev5, fewest hard-stop warnings):"
echo "  1. New revision -> FedRAMP Rev. 5 transition, Impact moderate"
echo "  2. Data origin: Synthetic, Sensitivity: Internal unclassified"
echo "  3. Upload data/synthetic-packages/fedramp-rev5-demo-portal/demo-package.json"
echo "  4. Finalize upload, wait for intake MAP (check MAP status in Intake readiness)"
echo "  5. Confirm package -> Preflight -> Start Targeted Run for LLM sufficiency"
echo ""
echo "Still expected in WSL (not full onprem_production):"
echo "  - /health/ready may return 503 (HS-001 draft authority manifest)"
echo "  - HS-002 FISMA template-pack warning if using Agency FISMA profile"
echo "  - dev_local runtime profile and dev OIDC (not customer IdP)"
