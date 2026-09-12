"""PostgreSQL integration tests for the bounded chat-retention purge."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy import select

from ato_operator.chat_retention import ChatRetentionReport, _run_purge
from ato_service.db.models import (
    AuditEvent,
    SspChatConversation,
    SspChatMessage,
    SspChatTurn,
    SspWorkspace,
    SspWorkspaceRevision,
)
from ato_service.db.session import create_session_factory
from tests.ato_service.test_ssp_workspace_boundaries import (
    _seed_workspace,
    _set_search_path,
)
from tests.integration_support.postgres import (
    postgres_integration_harness,
    run_async,
)


NOW = datetime(2033, 1, 1, 12, 0, tzinfo=UTC)
OLD_CREATED_AT = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
EXPIRED_AT = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
OLD_CONVERSATION_UPDATED_AT = datetime(2025, 12, 31, 12, 0, tzinfo=UTC)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _conversation(
    *,
    workspace_id: uuid.UUID,
    actor_id: str,
    updated_at: datetime,
    last_sequence: int = 0,
) -> SspChatConversation:
    return SspChatConversation(
        conversation_id=uuid.uuid4(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        last_sequence=last_sequence,
        rate_window_started_at=updated_at,
        rate_window_count=0,
        daily_token_count=0,
        usage_date=updated_at.date(),
        created_at=updated_at,
        updated_at=updated_at,
    )


def _turn(
    *,
    conversation_id: uuid.UUID,
    revision_id: uuid.UUID,
    sequence: int,
    status: str,
    created_at: datetime,
    completed_at: datetime | None,
    expires_at: datetime | None,
    lease_expires_at: datetime | None,
) -> SspChatTurn:
    turn_id = uuid.uuid4()
    return SspChatTurn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        sequence=sequence,
        request_id=uuid.uuid4(),
        request_fingerprint=_fingerprint(f"request:{turn_id}"),
        expected_revision_id=revision_id,
        context_fingerprint=_fingerprint(f"context:{turn_id}"),
        lease_token=uuid.uuid4(),
        lease_expires_at=lease_expires_at,
        status=status,
        created_at=created_at,
        completed_at=completed_at,
        expires_at=expires_at,
    )


def _messages(
    *,
    turn: SspChatTurn,
    created_at: datetime,
    expires_at: datetime,
) -> list[SspChatMessage]:
    return [
        SspChatMessage(
            message_id=uuid.uuid4(),
            turn_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            sequence=turn.sequence,
            role=role,
            content=f"integration {role} message",
            created_at=created_at,
            expires_at=expires_at,
            sources=[],
            context_fingerprint=turn.context_fingerprint,
        )
        for role in ("user", "assistant")
    ]


@pytest.mark.integration
def test_purge_chat_postgres_removes_expired_rows_only_and_preserves_active_state(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            assert harness.isolated_schema is not None
            factory = create_session_factory(harness.engine)
            workspace, _ = await _seed_workspace(
                factory,
                harness.isolated_schema,
                actor_id="retention-owner@example.gov",
                hmac_key=harness.hmac_key,
                now=NOW,
            )
            revision_id = workspace.current_revision_id
            assert revision_id is not None

            expired_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="expired@example.gov",
                updated_at=NOW,
                last_sequence=1,
            )
            expired_turn = _turn(
                conversation_id=expired_conversation.conversation_id,
                revision_id=revision_id,
                sequence=1,
                status="completed",
                created_at=OLD_CREATED_AT,
                completed_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
                lease_expires_at=None,
            )

            future_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="future@example.gov",
                updated_at=NOW,
                last_sequence=1,
            )
            future_turn = _turn(
                conversation_id=future_conversation.conversation_id,
                revision_id=revision_id,
                sequence=1,
                status="completed",
                created_at=NOW - timedelta(days=1),
                completed_at=NOW - timedelta(days=1),
                expires_at=NOW + timedelta(days=1),
                lease_expires_at=None,
            )

            mismatch_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="mismatch@example.gov",
                updated_at=NOW,
                last_sequence=1,
            )
            mismatch_turn = _turn(
                conversation_id=mismatch_conversation.conversation_id,
                revision_id=revision_id,
                sequence=1,
                status="completed",
                created_at=OLD_CREATED_AT,
                completed_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
                lease_expires_at=None,
            )

            pending_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="pending@example.gov",
                updated_at=NOW,
                last_sequence=1,
            )
            pending_turn = _turn(
                conversation_id=pending_conversation.conversation_id,
                revision_id=revision_id,
                sequence=1,
                status="pending",
                created_at=NOW,
                completed_at=None,
                expires_at=None,
                lease_expires_at=NOW + timedelta(hours=1),
            )

            aged_empty_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="aged-empty@example.gov",
                updated_at=OLD_CONVERSATION_UPDATED_AT,
            )
            fresh_empty_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="fresh-empty@example.gov",
                updated_at=NOW,
            )

            expired_messages = _messages(
                turn=expired_turn,
                created_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
            )
            future_messages = _messages(
                turn=future_turn,
                created_at=NOW - timedelta(days=1),
                expires_at=NOW + timedelta(days=1),
            )
            mismatch_messages = _messages(
                turn=mismatch_turn,
                created_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
            )
            mismatch_messages[1].created_at = NOW - timedelta(hours=1)
            mismatch_messages[1].expires_at = NOW + timedelta(days=1)

            harness.session.add_all(
                [
                    expired_conversation,
                    future_conversation,
                    mismatch_conversation,
                    pending_conversation,
                    aged_empty_conversation,
                    fresh_empty_conversation,
                    expired_turn,
                    future_turn,
                    mismatch_turn,
                    pending_turn,
                ]
            )
            await harness.session.flush()
            harness.session.add_all(
                [*expired_messages, *future_messages, *mismatch_messages]
            )
            await harness.session.commit()

            report = await _run_purge(
                harness.session,
                now=NOW,
                hmac_key=harness.hmac_key,
            )
            assert report == ChatRetentionReport(
                turns_purged=1,
                messages_purged=2,
                conversations_purged=1,
                batches_processed=1,
                batch_limit_reached=False,
                now="2033-01-01T12:00:00Z",
            )

            async with factory() as check:
                await _set_search_path(check, harness.isolated_schema)
                remaining_turns = {
                    row.turn_id: row
                    for row in (await check.execute(select(SspChatTurn))).scalars()
                }
                remaining_messages = list(
                    (await check.execute(select(SspChatMessage))).scalars()
                )
                remaining_conversation_ids = set(
                    (
                        await check.execute(select(SspChatConversation.conversation_id))
                    ).scalars()
                )
                workspace_row = (
                    await check.execute(
                        select(SspWorkspace).where(
                            SspWorkspace.workspace_id == workspace.workspace_id
                        )
                    )
                ).scalar_one()
                revision_row = (
                    await check.execute(
                        select(SspWorkspaceRevision).where(
                            SspWorkspaceRevision.revision_id == revision_id
                        )
                    )
                ).scalar_one()
                audit_events = list(
                    (
                        await check.execute(
                            select(AuditEvent).where(
                                AuditEvent.action == "chat.retention_purged"
                            )
                        )
                    ).scalars()
                )

            assert set(remaining_turns) == {
                future_turn.turn_id,
                mismatch_turn.turn_id,
                pending_turn.turn_id,
            }
            assert {message.turn_id for message in remaining_messages} == {
                future_turn.turn_id,
                mismatch_turn.turn_id,
            }
            assert len(remaining_messages) == 4
            assert (
                aged_empty_conversation.conversation_id
                not in remaining_conversation_ids
            )
            assert (
                fresh_empty_conversation.conversation_id in remaining_conversation_ids
            )
            assert expired_conversation.conversation_id in remaining_conversation_ids
            assert future_conversation.conversation_id in remaining_conversation_ids
            assert mismatch_conversation.conversation_id in remaining_conversation_ids
            assert pending_conversation.conversation_id in remaining_conversation_ids
            assert remaining_turns[pending_turn.turn_id].status == "pending"
            assert remaining_turns[pending_turn.turn_id].expires_at is None
            assert remaining_turns[
                pending_turn.turn_id
            ].lease_expires_at == NOW + timedelta(hours=1)
            assert workspace_row.workspace_id == workspace.workspace_id
            assert revision_row.revision_id == revision_id
            assert len(audit_events) == 1
            assert audit_events[0].metadata_ == {
                "turns_purged": 1,
                "messages_purged": 2,
                "conversations_purged": 1,
            }

    run_async(exercise())


@pytest.mark.integration
def test_purge_chat_postgres_rolls_back_rows_when_audit_append_fails(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(
            tmp_path,
            now=NOW,
            ordinary_session=True,
        ) as harness:
            assert harness.isolated_schema is not None
            factory = create_session_factory(harness.engine)
            workspace, _ = await _seed_workspace(
                factory,
                harness.isolated_schema,
                actor_id="rollback-owner@example.gov",
                hmac_key=harness.hmac_key,
                now=NOW,
            )
            revision_id = workspace.current_revision_id
            assert revision_id is not None
            rollback_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="rollback@example.gov",
                updated_at=NOW,
                last_sequence=1,
            )
            rollback_turn = _turn(
                conversation_id=rollback_conversation.conversation_id,
                revision_id=revision_id,
                sequence=1,
                status="completed",
                created_at=OLD_CREATED_AT,
                completed_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
                lease_expires_at=None,
            )
            aged_empty_conversation = _conversation(
                workspace_id=workspace.workspace_id,
                actor_id="rollback-aged-empty@example.gov",
                updated_at=OLD_CONVERSATION_UPDATED_AT,
            )
            rollback_turn_id = rollback_turn.turn_id
            aged_empty_conversation_id = aged_empty_conversation.conversation_id
            rollback_messages = _messages(
                turn=rollback_turn,
                created_at=OLD_CREATED_AT,
                expires_at=EXPIRED_AT,
            )
            harness.session.add_all(
                [
                    rollback_conversation,
                    rollback_turn,
                    aged_empty_conversation,
                ]
            )
            await harness.session.flush()
            harness.session.add_all(rollback_messages)
            await harness.session.commit()

            with patch(
                "ato_operator.chat_retention.append_audit_event",
                new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
            ) as audit:
                with pytest.raises(RuntimeError, match="audit unavailable"):
                    await _run_purge(
                        harness.session,
                        now=NOW,
                        hmac_key=harness.hmac_key,
                    )
            audit.assert_awaited_once()

            async with factory() as check:
                await _set_search_path(check, harness.isolated_schema)
                turn_count = (
                    await check.execute(
                        select(SspChatTurn).where(
                            SspChatTurn.turn_id == rollback_turn_id
                        )
                    )
                ).scalar_one_or_none()
                message_count = len(
                    (
                        await check.execute(
                            select(SspChatMessage).where(
                                SspChatMessage.turn_id == rollback_turn_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                conversation_exists = (
                    await check.execute(
                        select(SspChatConversation).where(
                            SspChatConversation.conversation_id
                            == aged_empty_conversation_id
                        )
                    )
                ).scalar_one_or_none()

            assert turn_count is not None
            assert message_count == 2
            assert conversation_exists is not None

    run_async(exercise())
