#!/usr/bin/env bash
set -euo pipefail
sudo -u ato env AWS_PROFILE="${AWS_PROFILE:-DevDataScience-180555414983}" HOME=/var/lib/ato \
  /opt/ato-analyzer/venv/bin/python - <<'PY'
from pathlib import Path

from ato_service.runtime_config import load_runtime_config
from ato_service.text_llm import ChatMessage, build_text_model_client

cfg = load_runtime_config(
    Path("/opt/ato-analyzer/runtime-config.json"),
    base_dir=Path("/opt/ato-analyzer"),
)
client = build_text_model_client(cfg)
text = client.complete([ChatMessage(role="user", content="Reply with exactly: OK")])
print(text.strip()[:200])
PY
