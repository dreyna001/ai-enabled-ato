#!/usr/bin/env bash
# Diagnose OpenAI-compatible text model wiring for WSL (no secret output).
set -euo pipefail

INSTALL_DIR="/opt/ato-analyzer"
ENV_FILE="/etc/ato-analyzer/credentials/ato-local.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with: sudo bash scripts/_diagnose_text_model.sh"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FAIL: missing $ENV_FILE"
  exit 1
fi

if ! grep -q '^ATO_TEXT_MODEL_API_KEY=.' "$ENV_FILE"; then
  echo "FAIL: $ENV_FILE has no ATO_TEXT_MODEL_API_KEY line"
  exit 1
fi

echo "OK: ato-local.env present with key line"
ls -l "$ENV_FILE" | awk '{print "    perms:", $1, "owner:", $3, $4}'
ls -ld "$(dirname "$ENV_FILE")" | awk '{print "    dir:", $1, "owner:", $3, $4}'

sudo -u ato -- bash -lc "
set -a
source '$ENV_FILE'
set +a
export ATO_RUNTIME_CONFIG_PATH='$INSTALL_DIR/runtime-config.json'
export ATO_LOCAL_ENV_FILE='$ENV_FILE'
'$INSTALL_DIR/venv/bin/python' - <<'PY'
import json
import os
from pathlib import Path

from ato_service.runtime_config import load_runtime_config
from ato_service.text_llm import (
    ChatMessage,
    TextModelCallError,
    TextModelConfigurationError,
    build_text_model_client,
)

config = load_runtime_config(Path(os.environ['ATO_RUNTIME_CONFIG_PATH']))
doc = config.document
print('model', doc.get('TEXT_MODEL_NAME'))
print('endpoint', doc.get('TEXT_MODEL_ENDPOINT_URL'))
print('profile_id', doc.get('TEXT_MODEL_PROFILE_ID'))
print('context_tokens', doc.get('TEXT_MODEL_CONTEXT_TOKENS'))
print('max_output_tokens', doc.get('TEXT_MODEL_MAX_OUTPUT_TOKENS'))
print('timeout_seconds', doc.get('TEXT_MODEL_TIMEOUT_SECONDS'))
print('key_in_env', bool(os.environ.get('ATO_TEXT_MODEL_API_KEY', '').strip()))

try:
    client = build_text_model_client(config)
    text = client.complete([ChatMessage(role='user', content='Reply with exactly: ok')], system='You are a test.')
    print('smoke_call', repr(text[:80]))
except TextModelConfigurationError as exc:
    print('FAIL configuration:', exc)
except TextModelCallError as exc:
    print('FAIL call:', type(exc).__name__, str(exc)[:500])
    cause = exc.__cause__
    if cause is not None:
        print('cause', type(cause).__name__, str(cause)[:800])
PY
"
