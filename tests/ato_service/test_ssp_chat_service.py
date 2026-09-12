from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
from typing import Any
import uuid

import pytest

from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.ssp_workspace.chat import (
    CHAT_RESERVATION_LEASE_SECONDS,
    ChatDailyTokenLimitError,
    ChatRateLimitError,
    ChatSequenceConflictError,
    _ContextSnapshot,
    _release_reservation,
    _reserve_turn,
    load_chat_history,
    send_chat_message,
)
from ato_service.ssp_workspace.chat_contracts import ChatMessageRequest
from ato_service.ssp_workspace.model_policy import SspModelPolicyError
from ato_service.db.models import (
    SspProfileVersion,
    SspWorkspace,
    SspWorkspaceRevision,
    System,
)
from tests.integration_support.postgres import postgres_integration_harness


def _config(tmp_path: Path, *, approved: bool):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/storage",
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID": "dev-local-enterprise",
            "PROCESS_CAPABILITIES": {
                "api": True,
                "intake_worker": False,
                "analyzer_worker": False,
                "portal_static": False,
                "malware_scanning": False,
                "text_model_calls": True,
                "vision_model_calls": False,
                "oidc_authentication": False,
                "package_search": False,
                "package_chat": False,
            },
            "TEXT_MODEL_ENDPOINT_PROFILE": "mock",
            "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": approved,
        },
        base_dir=tmp_path,
    )


def test_injected_model_does_not_bypass_ssp_policy(tmp_path: Path) -> None:
    called = False

    def model(_prompt: Any) -> str:
        nonlocal called
        called = True
        return '{"answer":"unknown","source_ids":[]}'

    payload = ChatMessageRequest(
        message="What is documented?",
        expected_revision_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        expected_sequence=0,
    )

    async def exercise() -> None:
        with pytest.raises(SspModelPolicyError):
            await send_chat_message(
                object(),
                uuid.uuid4(),
                "actor@example.gov",
                payload,
                model,
                _config(tmp_path, approved=False),
                datetime.now(UTC),
            )

    asyncio.run(exercise())
    assert called is False


async def _seed_chat_workspaces(session: Any, now: datetime) -> tuple[Any, Any]:
    profile_id = uuid.uuid4()
    profile = SspProfileVersion(
        profile_version_id=profile_id,
        profile_key="chat-test-profile",
        version="1.0.0",
        status="active",
        bundle_sha256="a" * 64,
        bundle={},
        imported_by="test@example.gov",
        imported_at=now,
        activated_at=now,
    )
    session.add(profile)
    await session.flush()
    workspaces: list[SspWorkspace] = []
    revisions: list[SspWorkspaceRevision] = []
    for index in range(2):
        system = System(
            system_id=uuid.uuid4(),
            display_name=f"Chat Test System {index}",
            external_system_id=None,
            customer_enterprise_id="dev-local-enterprise",
            owner_group="owners",
            viewer_groups=[],
            created_at=now,
            archived_at=None,
        )
        workspace = SspWorkspace(
            workspace_id=uuid.uuid4(),
            system_id=system.system_id,
            profile_version_id=profile_id,
            current_revision_id=None,
            status="working",
            created_by="test@example.gov",
            created_at=now,
            archived_at=None,
        )
        revision = SspWorkspaceRevision(
            revision_id=uuid.uuid4(),
            workspace_id=workspace.workspace_id,
            parent_revision_id=None,
            version=1,
            status="working",
            content_sha256=(f"{index + 1}" * 64),
            content={},
            created_by="test@example.gov",
            created_at=now,
        )
        session.add(system)
        await session.flush()
        session.add(workspace)
        await session.flush()
        session.add(revision)
        await session.flush()
        workspaces.append(workspace)
        revisions.append(revision)
    for workspace, revision in zip(workspaces, revisions):
        workspace.current_revision_id = revision.revision_id
    await session.commit()
    return workspaces[0], workspaces[1]


def _chat_payload(revision_id: uuid.UUID, *, sequence: int = 0) -> ChatMessageRequest:
    return ChatMessageRequest(
        message="Which boundary is documented?",
        expected_revision_id=revision_id,
        request_id=uuid.uuid4(),
        expected_sequence=sequence,
    )


@pytest.mark.integration
def test_chat_budget_is_actor_scoped_across_workspaces(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path, ordinary_session=True
        ) as harness:
            workspace_one, workspace_two = await _seed_chat_workspaces(
                harness.session, harness.now
            )
            workspace_one_id = workspace_one.workspace_id
            workspace_two_id = workspace_two.workspace_id
            context_one = _ContextSnapshot(
                workspace_id=workspace_one_id,
                revision_id=uuid.uuid4(),
                fingerprint="b" * 64,
                sources=(),
            )
            context_two = _ContextSnapshot(
                workspace_id=workspace_two_id,
                revision_id=uuid.uuid4(),
                fingerprint="c" * 64,
                sources=(),
            )
            # Turn reservations only exercise the durable usage boundary; the
            # revision IDs must satisfy the FK but no model work is performed.
            context_one = replace(
                context_one,
                revision_id=(
                    await harness.session.get(
                        SspWorkspaceRevision, workspace_one.current_revision_id
                    )
                ).revision_id,
            )
            context_two = replace(
                context_two,
                revision_id=(
                    await harness.session.get(
                        SspWorkspaceRevision, workspace_two.current_revision_id
                    )
                ).revision_id,
            )
            actor_id = "same-actor@example.gov"
            limits = replace(
                harness.config.chat_limits,
                rate_limit_max_requests=1,
            )
            first = await _reserve_turn(
                harness.session,
                    workspace_id=workspace_one_id,
                actor_id=actor_id,
                payload=_chat_payload(context_one.revision_id),
                context=context_one,
                now=harness.now,
                limits=limits,
                estimated_tokens=100,
            )
            with pytest.raises(ChatRateLimitError):
                await _reserve_turn(
                    harness.session,
                        workspace_id=workspace_two_id,
                    actor_id=actor_id,
                    payload=_chat_payload(context_two.revision_id),
                    context=context_two,
                    now=harness.now,
                    limits=limits,
                    estimated_tokens=100,
                )
            await _release_reservation(harness.session, reservation=first)

            daily_limits = replace(
                harness.config.chat_limits,
                daily_token_limit_per_user=100,
            )
            first_daily = await _reserve_turn(
                harness.session,
                    workspace_id=workspace_one_id,
                actor_id="daily-actor@example.gov",
                payload=_chat_payload(context_one.revision_id),
                context=context_one,
                now=harness.now,
                limits=daily_limits,
                estimated_tokens=100,
            )
            with pytest.raises(ChatDailyTokenLimitError):
                await _reserve_turn(
                    harness.session,
                        workspace_id=workspace_two_id,
                    actor_id="daily-actor@example.gov",
                    payload=_chat_payload(context_two.revision_id),
                    context=context_two,
                    now=harness.now,
                    limits=daily_limits,
                    estimated_tokens=100,
                )
            await _release_reservation(harness.session, reservation=first_daily)

    asyncio.run(exercise())


@pytest.mark.integration
def test_expired_pending_turn_is_hidden_and_new_request_can_retry(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path, ordinary_session=True
        ) as harness:
            workspace, _ = await _seed_chat_workspaces(harness.session, harness.now)
            workspace_id = workspace.workspace_id
            revision_id = workspace.current_revision_id
            revision = await harness.session.get(
                SspWorkspaceRevision, revision_id
            )
            context = _ContextSnapshot(
                workspace_id=workspace_id,
                revision_id=revision.revision_id,
                fingerprint="d" * 64,
                sources=(),
            )
            actor_id = "reload-actor@example.gov"
            pending = await _reserve_turn(
                harness.session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                payload=_chat_payload(revision_id),
                context=context,
                now=harness.now,
                limits=harness.config.chat_limits,
                estimated_tokens=100,
            )
            with pytest.raises(ChatSequenceConflictError):
                await _reserve_turn(
                    harness.session,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    payload=_chat_payload(revision_id, sequence=1),
                    context=context,
                    now=harness.now,
                    limits=harness.config.chat_limits,
                    estimated_tokens=100,
                )
            await harness.session.rollback()
            expired_at = harness.now + timedelta(
                seconds=CHAT_RESERVATION_LEASE_SECONDS + 1
            )
            # A history reload reports the last committed sequence, not the
            # abandoned reservation sequence.
            history = await load_chat_history(
                harness.session,
                workspace_id,
                actor_id,
                expired_at,
            )
            assert history.sequence == 0
            replacement = await _reserve_turn(
                harness.session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                    payload=_chat_payload(revision_id),
                context=context,
                now=expired_at,
                limits=harness.config.chat_limits,
                estimated_tokens=100,
            )
            assert replacement.sequence == 1
            await _release_reservation(
                harness.session, reservation=replacement
            )
            # The old reservation was reclaimed before the replacement was
            # inserted, so its lease token cannot release the replacement.
            await _release_reservation(harness.session, reservation=pending)

    asyncio.run(exercise())
