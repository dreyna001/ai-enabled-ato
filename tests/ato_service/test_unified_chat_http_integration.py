"""Real PostgreSQL chat HTTP persistence and authorization regression."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
import httpx
import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.db.session import create_session_factory
from ato_service.db.models import SspWorkspaceRevision
from ato_service.problems import register_problem_handlers
from ato_service.ssp_workspace import api
from ato_service.ssp_workspace.contracts import RevisionContent
from ato_service.ssp_workspace.persistence import save_revision
from tests.ato_service.test_ssp_workspace_boundaries import _seed_workspace
from tests.integration_support.postgres import postgres_integration_harness


@pytest.mark.integration
def test_http_chat_persists_replays_privately_and_remains_readable_when_disabled(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path, ordinary_session=True) as h:
            workspace, _ = await _seed_workspace(
                create_session_factory(h.engine), h.isolated_schema,
                actor_id="first", hmac_key=h.hmac_key, now=h.now,
            )
            workspace_id, revision_id = workspace.workspace_id, workspace.current_revision_id
            calls = 0

            async def model(prompt):
                nonlocal calls
                assert not h.session.in_transaction(), "model must not hold database transaction"
                assert prompt.output_schema["additionalProperties"] is False
                calls += 1
                source_id = re.search(r'"source_id":"([^"]+)"', prompt.user).group(1)
                return json.dumps({
                    "answer": "Pinned profile metadata is available; the boundary remains unknown.",
                    "source_ids": [source_id],
                })

            identity = {"actor": "first", "groups": ("system-owners",)}
            runtime = SimpleNamespace(config=h.config, ssp_model_adapter=model)
            app = FastAPI()
            register_problem_handlers(app)

            @app.middleware("http")
            async def authenticate(request, call_next):
                request.state.authenticated_principal = AuthenticatedPrincipal(
                    actor_id=identity["actor"], groups=identity["groups"],
                    csrf_token="c" * 32, allowed_origins=("https://portal.example",),
                )
                return await call_next(request)

            app.dependency_overrides[api.get_db_session] = lambda: h.session
            app.dependency_overrides[api.get_runtime_state] = lambda: runtime
            app.include_router(api.build_ssp_workspace_router(), prefix="/api/v1")
            path = f"/api/v1/ssp-workspaces/{workspace_id}/chat"
            headers = {"Origin": "https://portal.example", "X-CSRF-Token": "c" * 32}
            body = {
                "message": "What is the boundary?", "expected_revision_id": str(revision_id),
                "request_id": str(uuid.uuid4()), "expected_sequence": 0,
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://portal.example") as client:
                created = await client.post(path, json=body, headers=headers)
                assert created.status_code == 200, created.text
                saved = created.json()
                assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
                assert saved["sequence"] == 1
                assert len(saved["messages"][1]["sources"]) == 1
                assert len(saved["messages"][1]["sources"][0]["sha256"]) == 64
                replay = await client.post(path, json=body, headers=headers)
                assert replay.status_code == 200, replay.text
                assert replay.json() == saved
                assert calls == 1
                previous_revision = await h.session.get(SspWorkspaceRevision, revision_id)
                await save_revision(
                    h.session, workspace_id=workspace_id,
                    content=RevisionContent.model_validate(previous_revision.content),
                    created_by="first", now=datetime.now(UTC), expected_revision_id=revision_id,
                )
                await h.session.commit()
                refreshed = await client.get(path)
                assert all(message["stale"] for message in refreshed.json()["messages"])
                stale_replay = await client.post(path, json=body, headers=headers)
                assert stale_replay.status_code == 200, stale_replay.text
                assert calls == 1
                saved = refreshed.json()
                identity["actor"] = "second"
                separate = await client.get(path)
                assert separate.status_code == 200
                assert separate.json()["messages"] == []
                identity["actor"] = "first"
                runtime.config = replace(h.config, document={
                    **h.config.document, "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": False,
                })
                assert (await client.get(path)).json() == saved
                denied = await client.post(path, json={**body, "request_id": str(uuid.uuid4()), "expected_sequence": 1}, headers=headers)
                assert denied.status_code == 403
                assert calls == 1
                identity["groups"] = ("unrelated-system",)
                assert (await client.get(path)).status_code == 403

    asyncio.run(exercise())
