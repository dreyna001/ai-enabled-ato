#!/usr/bin/env bash
# Quick Bedrock / LLM health check for WSL systemd stack (API runs as user ato).
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/wsl-llm-health.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="/opt/ato-analyzer/runtime-config.json"
fail=0

info() { echo "  $*"; }
ok() { echo "[OK] $*"; }
bad() { echo "[FAIL] $*"; fail=1; }

echo "=== ATO WSL LLM health (Bedrock) ==="
echo ""

if [[ ! -f "$CONFIG" ]]; then
  bad "Missing $CONFIG — run scripts/wsl-local-deploy.sh first"
  exit 1
fi

provider="$(python3 -c "import json; print(json.load(open('$CONFIG'))['TEXT_MODEL_PROVIDER'])")"
model="$(python3 -c "import json; print(json.load(open('$CONFIG'))['TEXT_MODEL_NAME'])")"
caps="$(python3 -c "import json; d=json.load(open('$CONFIG')).get('PROCESS_CAPABILITIES',{}); print('text_model_calls=', d.get('text_model_calls'), 'package_chat=', d.get('package_chat'))")"

info "TEXT_MODEL_PROVIDER=$provider"
info "TEXT_MODEL_NAME=$model"
info "$caps"

if [[ "$provider" != "aws_bedrock" ]]; then
  bad "Runtime is not Bedrock — run: sudo bash scripts/wsl-portal-enable.sh --bedrock"
fi

if systemctl is-active --quiet ato-api.service; then
  ok "ato-api.service active"
else
  bad "ato-api.service not active"
fi

if curl -fsS --max-time 3 http://127.0.0.1:8001/health/live >/dev/null; then
  ok "API liveness http://127.0.0.1:8001/health/live"
else
  bad "API not reachable on 8001"
fi

ready_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8001/health/ready || echo 000)"
if [[ "$ready_code" == "503" ]]; then
  ok "/health/ready returns 503 (expected locally while HS-001 draft manifest is open — not an LLM failure)"
elif [[ "$ready_code" == "200" ]]; then
  ok "/health/ready returns 200"
else
  bad "/health/ready returned HTTP $ready_code"
fi

if grep -Eq '^AWS_PROFILE=|^AWS_ACCESS_KEY_ID=' /etc/ato-analyzer/credentials/ato-local.env 2>/dev/null; then
  ok "ato-local.env has AWS credentials reference"
else
  bad "No AWS_PROFILE/AWS_ACCESS_KEY_ID in /etc/ato-analyzer/credentials/ato-local.env — copy config.local.env.bedrock.example"
fi

echo ""
echo "--- Bedrock call (same path as API service user) ---"
if bash "$REPO_DIR/scripts/wsl-bedrock-smoke.sh"; then
  ok "Bedrock converse smoke"
else
  bad "Bedrock smoke failed — run: aws sso login && sudo bash scripts/wsl-sync-aws-for-ato.sh"
fi

WORKSPACE_ID="${ATO_LLM_HEALTH_WORKSPACE_ID:-5101997a-b743-4059-8553-218761178bf8}"
if [[ -f "$REPO_DIR/scripts/wsl-agent-patch-debug.sh" ]]; then
  echo ""
  echo "--- SSP agent patch (workspace $WORKSPACE_ID) ---"
  if bash "$REPO_DIR/scripts/wsl-agent-patch-debug.sh" "$WORKSPACE_ID" \
    "Reply OK if you can read this instruction." 2>&1 | tee /tmp/wsl-llm-agent.log | tail -3; then
    if grep -q '^OK patch_id=' /tmp/wsl-llm-agent.log; then
      ok "SSP agent + Bedrock end-to-end"
    else
      bad "Agent debug script did not return OK"
    fi
  else
    bad "SSP agent path failed — see output above"
  fi
fi

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "All LLM checks passed. Portal must use http://localhost:5174 (proxies to API :8001)."
  echo "After aws sso login: sudo bash scripts/wsl-sync-aws-for-ato.sh"
  exit 0
fi

echo "One or more checks failed. Fix Bedrock enable + SSO sync, then rerun this script."
exit 1
