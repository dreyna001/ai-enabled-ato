"""Tests for the bounded operator chat-retention command."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from ato_operator.chat_retention import (
    CHAT_PURGE_BATCH_SIZE,
    ChatRetentionConfigurationError,
    ChatRetentionReport,
    _resolve_supported_retention_years,
    _retention_cutoff,
    _run_purge,
)
from ato_operator.cli import main
from ato_service.runtime_config import RuntimeConfig


NOW = datetime(2033, 1, 1, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows: tuple[tuple[object, ...], ...] = (), *, rowcount: int = 0):
        self.rows = rows
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self.rows)


def _session(*results: _Result) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=list(results))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _compile(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_purge_processes_one_bounded_batch_and_removes_idle_conversation() -> None:
    turn_id = "11111111-1111-4111-8111-111111111111"
    conversation_id = "22222222-2222-4222-8222-222222222222"
    session = _session(
        _Result(((turn_id,),)),
        _Result(rowcount=2),
        _Result(rowcount=1),
        _Result(((conversation_id,),)),
        _Result(rowcount=1),
    )

    with patch(
        "ato_operator.chat_retention.append_audit_event",
        new=AsyncMock(),
    ) as audit:
        report = asyncio.run(
            _run_purge(session, now=NOW, hmac_key=b"k" * 32, batch_size=1)
        )

    assert report == ChatRetentionReport(
        turns_purged=1,
        messages_purged=2,
        conversations_purged=1,
        batches_processed=1,
        batch_limit_reached=True,
        now="2033-01-01T12:00:00Z",
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["metadata"] == {
        "turns_purged": 1,
        "messages_purged": 2,
        "conversations_purged": 1,
    }

    statements = [call.args[0] for call in session.execute.await_args_list]
    select_sql = _compile(statements[0])
    assert "ssp_chat_turns.status =" in select_sql
    assert "ssp_chat_turns.expires_at <=" in select_sql
    assert "ssp_chat_turns.lease_expires_at IS NULL" in select_sql
    assert "ssp_chat_messages.expires_at >" in select_sql
    assert "ssp_chat_conversations.updated_at <=" in _compile(statements[3])
    assert "LIMIT " in select_sql
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert "actor_id" not in "\n".join(_compile(statement) for statement in statements)
    assert _compile(statements[1]).startswith("DELETE FROM ssp_chat_messages")
    turn_delete_sql = _compile(statements[2])
    assert turn_delete_sql.startswith("DELETE FROM ssp_chat_turns")
    assert "ssp_chat_turns.lease_expires_at IS NULL" in turn_delete_sql
    conversation_delete_sql = _compile(statements[4])
    assert conversation_delete_sql.startswith("DELETE FROM ssp_chat_conversations")
    assert "ssp_chat_conversations.updated_at <=" in conversation_delete_sql


def test_purge_query_excludes_turns_with_future_message_expiry() -> None:
    session = _session(_Result(), _Result())

    with patch(
        "ato_operator.chat_retention.append_audit_event",
        new=AsyncMock(),
    ):
        report = asyncio.run(_run_purge(session, now=NOW, hmac_key=b"k" * 32))

    assert report.turns_purged == 0
    assert report.messages_purged == 0
    assert report.conversations_purged == 0
    assert session.commit.await_count == 1
    select_sql = _compile(session.execute.await_args_list[0].args[0])
    assert "ssp_chat_messages.expires_at >" in select_sql
    assert "ssp_chat_messages.expires_at IS NULL" in select_sql
    assert "ssp_chat_conversations.updated_at <=" in _compile(
        session.execute.await_args_list[1].args[0]
    )
    assert all(
        not _compile(call.args[0]).startswith("DELETE FROM")
        for call in session.execute.await_args_list
    )


def test_pending_turns_preserve_conversation_and_idempotency_state() -> None:
    session = _session(_Result(), _Result())

    with patch(
        "ato_operator.chat_retention.append_audit_event",
        new=AsyncMock(),
    ):
        asyncio.run(_run_purge(session, now=NOW, hmac_key=b"k" * 32))

    conversation_select_sql = _compile(session.execute.await_args_list[1].args[0])
    assert "NOT (EXISTS" in conversation_select_sql
    assert "ssp_chat_turns.conversation_id" in conversation_select_sql
    assert all(
        not _compile(call.args[0]).startswith("DELETE FROM")
        for call in session.execute.await_args_list
    )


def test_audit_failure_rolls_back_physical_deletes() -> None:
    session = _session(
        _Result((("11111111-1111-4111-8111-111111111111",),)),
        _Result(rowcount=2),
        _Result(rowcount=1),
        _Result(),
    )

    with patch(
        "ato_operator.chat_retention.append_audit_event",
        new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
    ):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            asyncio.run(_run_purge(session, now=NOW, hmac_key=b"k" * 32))

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_non_default_customer_retention_override_fails_closed(tmp_path: Path) -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={"RETENTION_YEARS": 6},
    )

    with pytest.raises(
        ChatRetentionConfigurationError,
        match="customer retention overrides are not supported",
    ):
        _resolve_supported_retention_years(config)


def test_retention_cutoff_uses_calendar_years_and_maps_february_29() -> None:
    leap_day = datetime(2032, 2, 29, 12, 0, tzinfo=UTC)

    assert _retention_cutoff(leap_day) == datetime(2025, 2, 28, 12, 0, tzinfo=UTC)


def test_purge_chat_cli_uses_config_and_reports_one_manual_batch() -> None:
    report = ChatRetentionReport(
        turns_purged=1,
        messages_purged=2,
        conversations_purged=1,
        batches_processed=1,
        batch_limit_reached=False,
        now="2033-01-01T12:00:00Z",
    )
    config = SimpleNamespace()
    output = io.StringIO()
    with (
        patch("ato_operator.cli._load_config", return_value=config) as load_config,
        patch(
            "ato_operator.cli.purge_expired_chat_turns_sync", return_value=report
        ) as purge,
        redirect_stdout(output),
    ):
        exit_code = main(
            ["purge-chat", "--config", "/protected/runtime.json", "--json"]
        )

    assert exit_code == 0
    load_config.assert_called_once()
    purge.assert_called_once_with(config)
    assert json.loads(output.getvalue()) == report.to_dict()


def test_chat_purge_batch_size_is_bounded() -> None:
    assert CHAT_PURGE_BATCH_SIZE == 100
