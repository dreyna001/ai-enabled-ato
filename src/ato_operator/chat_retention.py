"""Bounded physical expiry of completed private chat turns."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ato_service.audit import append_audit_event
from ato_service.db.models import SspChatConversation, SspChatMessage, SspChatTurn
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)

CHAT_RETENTION_YEARS = 7
CHAT_PURGE_BATCH_SIZE = 100
_CHAT_PURGE_ACTOR_ID = "ato-operator"


class ChatRetentionConfigurationError(RuntimeConfigError):
    """Raised when the chat retention tables or policy are not available."""


@dataclass(frozen=True, slots=True)
class ChatRetentionReport:
    """Counts and timing metadata returned by one purge operation."""

    turns_purged: int
    messages_purged: int
    conversations_purged: int
    batches_processed: int
    batch_limit_reached: bool
    now: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe operator report without private chat data."""
        return {
            "turns_purged": self.turns_purged,
            "messages_purged": self.messages_purged,
            "conversations_purged": self.conversations_purged,
            "batches_processed": self.batches_processed,
            "batch_limit_reached": self.batch_limit_reached,
            "now": self.now,
        }


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChatRetentionConfigurationError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _add_calendar_years(value: datetime, years: int) -> datetime:
    """Add calendar years, mapping February 29 to February 28 if needed."""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _retention_cutoff(now: datetime) -> datetime:
    """Return the oldest timestamp still protected by seven-year retention."""
    return _add_calendar_years(now, -CHAT_RETENTION_YEARS)


def _resolve_supported_retention_years(config: RuntimeConfig) -> int:
    document = getattr(config, "document", {})
    configured = (
        document.get("RETENTION_YEARS", CHAT_RETENTION_YEARS)
        if isinstance(document, Mapping)
        else CHAT_RETENTION_YEARS
    )
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise ChatRetentionConfigurationError(
            "RETENTION_YEARS must be a positive integer"
        )
    if configured != CHAT_RETENTION_YEARS:
        raise ChatRetentionConfigurationError(
            "purge-chat supports only the seven-year default; customer retention "
            "overrides are not supported"
        )
    return configured


def _rowcount(result: Any) -> int:
    value = getattr(result, "rowcount", 0)
    return value if isinstance(value, int) and value >= 0 else 0


async def _run_purge(
    session: AsyncSession,
    *,
    now: datetime,
    hmac_key: bytes,
    batch_size: int = CHAT_PURGE_BATCH_SIZE,
) -> ChatRetentionReport:
    """Delete one bounded batch of expired completed chat rows and audit it."""
    effective_now = _require_aware_utc(now)
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size <= 0
    ):
        raise ChatRetentionConfigurationError("chat purge batch size must be positive")
    turns_purged = 0
    messages_purged = 0
    conversations_purged = 0
    batch_limit_reached = False
    retention_cutoff = _retention_cutoff(effective_now)

    try:
        expired_turn_ids_result = await session.execute(
            select(SspChatTurn.turn_id)
            .where(
                SspChatTurn.status == "completed",
                SspChatTurn.completed_at.is_not(None),
                SspChatTurn.expires_at.is_not(None),
                SspChatTurn.lease_expires_at.is_(None),
                SspChatTurn.expires_at <= effective_now,
                ~exists(
                    select(SspChatMessage.message_id).where(
                        SspChatMessage.turn_id == SspChatTurn.turn_id,
                        or_(
                            SspChatMessage.expires_at.is_(None),
                            SspChatMessage.expires_at > effective_now,
                        ),
                    )
                ),
            )
            .order_by(SspChatTurn.expires_at.asc(), SspChatTurn.turn_id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        turn_ids = tuple(row[0] for row in expired_turn_ids_result)
        batch_limit_reached = len(turn_ids) == batch_size

        if turn_ids:
            message_result = await session.execute(
                delete(SspChatMessage).where(
                    SspChatMessage.turn_id.in_(turn_ids),
                    SspChatMessage.expires_at <= effective_now,
                )
            )
            deleted_message_count = _rowcount(message_result)

            turn_result = await session.execute(
                delete(SspChatTurn).where(
                    SspChatTurn.turn_id.in_(turn_ids),
                    SspChatTurn.status == "completed",
                    SspChatTurn.completed_at.is_not(None),
                    SspChatTurn.expires_at.is_not(None),
                    SspChatTurn.lease_expires_at.is_(None),
                    SspChatTurn.expires_at <= effective_now,
                    ~exists(
                        select(SspChatMessage.message_id).where(
                            SspChatMessage.turn_id == SspChatTurn.turn_id,
                            or_(
                                SspChatMessage.expires_at.is_(None),
                                SspChatMessage.expires_at > effective_now,
                            ),
                        )
                    ),
                )
            )
            deleted_turn_count = _rowcount(turn_result)
            if deleted_turn_count != len(turn_ids):
                raise RuntimeError(
                    "chat purge deleted an incomplete expired-turn batch"
                )
            messages_purged = deleted_message_count
            turns_purged = deleted_turn_count

        orphan_conversation_ids_result = await session.execute(
            select(SspChatConversation.conversation_id)
            .where(
                ~exists(
                    select(SspChatTurn.turn_id).where(
                        SspChatTurn.conversation_id
                        == SspChatConversation.conversation_id
                    )
                ),
                SspChatConversation.updated_at <= retention_cutoff,
            )
            .order_by(
                SspChatConversation.updated_at.asc(),
                SspChatConversation.conversation_id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        orphan_conversation_ids = tuple(
            row[0] for row in orphan_conversation_ids_result
        )
        batch_limit_reached = batch_limit_reached or (
            len(orphan_conversation_ids) == batch_size
        )
        if orphan_conversation_ids:
            conversation_result = await session.execute(
                delete(SspChatConversation).where(
                    SspChatConversation.conversation_id.in_(orphan_conversation_ids),
                    SspChatConversation.updated_at <= retention_cutoff,
                    ~exists(
                        select(SspChatTurn.turn_id).where(
                            SspChatTurn.conversation_id
                            == SspChatConversation.conversation_id
                        )
                    ),
                )
            )
            conversations_purged = _rowcount(conversation_result)

        await append_audit_event(
            session,
            hmac_key=hmac_key,
            actor_type="service",
            actor_id=_CHAT_PURGE_ACTOR_ID,
            action="chat.retention_purged",
            object_type="chat_retention",
            object_id=effective_now.strftime("%Y%m%dT%H%M%SZ"),
            outcome="succeeded",
            reason_code=None,
            metadata={
                "turns_purged": turns_purged,
                "messages_purged": messages_purged,
                "conversations_purged": conversations_purged,
            },
            occurred_at=effective_now,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return ChatRetentionReport(
        turns_purged=turns_purged,
        messages_purged=messages_purged,
        conversations_purged=conversations_purged,
        batches_processed=1,
        batch_limit_reached=batch_limit_reached,
        now=effective_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def purge_expired_chat_turns_sync(
    config: RuntimeConfig,
    *,
    now: datetime | None = None,
) -> ChatRetentionReport:
    """Physically purge expired completed private chat turns from PostgreSQL."""
    _resolve_supported_retention_years(config)
    effective_now = _require_aware_utc(now or datetime.now(timezone.utc))
    dsn = resolve_runtime_database_dsn(config)
    hmac_key = resolve_runtime_audit_hmac_key(config)

    async def _execute() -> ChatRetentionReport:
        engine = create_async_engine(dsn)
        session_factory = sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        try:
            async with session_factory() as session:
                return await _run_purge(
                    session,
                    now=effective_now,
                    hmac_key=hmac_key,
                )
        finally:
            await engine.dispose()

    return asyncio.run(_execute())


__all__ = [
    "CHAT_PURGE_BATCH_SIZE",
    "CHAT_RETENTION_YEARS",
    "ChatRetentionConfigurationError",
    "ChatRetentionReport",
    "purge_expired_chat_turns_sync",
]
