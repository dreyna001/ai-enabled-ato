#!/usr/bin/env bash
# Reproduce SSP agent patch generation (same path as POST .../agent/patches).
set -euo pipefail
WORKSPACE_ID="${1:-65782bc0-403e-4d3f-88c4-4f1953a42ac1}"
INSTRUCTION="${2:-Add one sentence to the system description section using only existing evidence.}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/wsl-agent-patch-debug.sh" >&2
  exit 1
fi

DEBUG_DSN="$(tr -d '\n' </etc/ato-analyzer/credentials/database-dsn)"
DEBUG_HMAC_HEX="$(xxd -p /etc/ato-analyzer/credentials/audit-hmac-key | tr -d '\n')"

sudo -u ato env \
  AWS_PROFILE="${AWS_PROFILE:-DevDataScience-180555414983}" \
  HOME=/var/lib/ato \
  ATO_RUNTIME_CONFIG_PATH=/opt/ato-analyzer/runtime-config.json \
  ATO_DEBUG_DATABASE_DSN="$DEBUG_DSN" \
  ATO_DEBUG_HMAC_HEX="$DEBUG_HMAC_HEX" \
  /opt/ato-analyzer/venv/bin/python - "$WORKSPACE_ID" "$INSTRUCTION" <<'PY'
import asyncio
import binascii
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ato_service.db.models import SspWorkspaceRevision
from ato_service.runtime_config import load_runtime_config
from ato_service.ssp_workspace.generation import ModelPrompt, SspGenerationError
from ato_service.ssp_workspace.service import propose_agent_patch
from ato_service.text_llm import ChatMessage, TextModelCallError, build_text_model_client

workspace_id = uuid.UUID(sys.argv[1])
instruction = sys.argv[2]
install_dir = Path("/opt/ato-analyzer")
config = load_runtime_config(install_dir / "runtime-config.json", base_dir=install_dir)
dsn = os.environ["ATO_DEBUG_DATABASE_DSN"].strip()
if dsn.startswith("postgresql://"):
    dsn = "postgresql+asyncpg://" + dsn[len("postgresql://") :]
engine = create_async_engine(dsn)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
client = build_text_model_client(config)


def model(prompt: ModelPrompt) -> str:
    return client.complete(
        [ChatMessage(role="user", content=prompt.user)],
        system=prompt.system,
    )


async def main() -> None:
    async with session_factory() as session:
        rev = (
            await session.execute(
                select(SspWorkspaceRevision)
                .where(SspWorkspaceRevision.workspace_id == workspace_id)
                .order_by(SspWorkspaceRevision.version.desc())
                .limit(1)
            )
        ).scalar_one()
        hmac = binascii.unhexlify(os.environ["ATO_DEBUG_HMAC_HEX"])
        try:
            row = await propose_agent_patch(
                session,
                workspace_id=workspace_id,
                expected_revision_id=rev.revision_id,
                instruction=instruction,
                model=model,
                actor_id="debug-user",
                now=datetime.now(timezone.utc),
                audit_hmac_key=hmac,
            )
            print("OK patch_id=", row.patch_id)
            print("summary=", row.summary[:200])
        except SspGenerationError as exc:
            print("SspGenerationError:", exc.failure_kind, exc.detail)
            print("attempts=", exc.attempts, "repair=", exc.repair_attempted)
            if exc.last_raw_response:
                print("--- raw (first 3000 chars) ---")
                print(exc.last_raw_response[:3000])
            raise SystemExit(1)
        except TextModelCallError as exc:
            print("TextModelCallError:", exc)
            raise SystemExit(1)
        except Exception as exc:
            print(type(exc).__name__, exc)
            raise SystemExit(1)


asyncio.run(main())
PY
