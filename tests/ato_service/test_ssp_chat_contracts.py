from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest
from pydantic import ValidationError

from ato_service.ssp_workspace.chat import (
    ChatModelOutputError,
    ChatRetentionConfigurationError,
    ChatSequenceConflictError,
    _parse_model_output,
    chat_retention_expiry,
)
from ato_service.ssp_workspace.chat_contracts import ChatMessageRequest
from ato_service.ssp_workspace.persistence import WorkspacePersistenceError


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "message": "What is the authorization boundary?",
        "expected_revision_id": uuid.uuid4(),
        "request_id": uuid.uuid4(),
        "expected_sequence": 0,
    }
    value.update(overrides)
    return value


def test_chat_message_request_is_closed_and_rejects_blank_message() -> None:
    request = ChatMessageRequest.model_validate(
        _request(focus="  boundary  ")
    )

    assert request.focus == "boundary"
    with pytest.raises(ValidationError):
        ChatMessageRequest.model_validate(_request(message=" \t\n"))
    with pytest.raises(ValidationError):
        ChatMessageRequest.model_validate(_request(unexpected="rejected"))
    with pytest.raises(ValidationError):
        ChatMessageRequest.model_validate(_request(expected_sequence=-1))


def test_model_output_rejects_unknown_or_uncited_factual_answer() -> None:
    source_id = "control:approved"
    accepted = _parse_model_output(
        '{"answer":"The boundary is documented.","source_ids":["control:approved"]}',
        allowed_source_ids=frozenset({source_id}),
    )
    assert accepted.source_ids == [source_id]

    with pytest.raises(ChatModelOutputError):
        _parse_model_output(
            '{"answer":"The boundary is documented.","source_ids":["control:other"]}',
            allowed_source_ids=frozenset({source_id}),
        )
    with pytest.raises(ChatModelOutputError):
        _parse_model_output(
            '{"answer":"The boundary is documented.","source_ids":[]}',
            allowed_source_ids=frozenset({source_id}),
        )
    uncertain = _parse_model_output(
        '{"answer":"The boundary is unknown from the available records.","source_ids":[]}',
        allowed_source_ids=frozenset({source_id}),
    )
    assert uncertain.source_ids == []


def test_chat_retention_is_calendar_year_and_fails_closed() -> None:
    completed_at = datetime(2024, 2, 29, 12, 0, tzinfo=UTC)

    assert chat_retention_expiry(completed_at) == datetime(
        2031, 2, 28, 12, 0, tzinfo=UTC
    )
    with pytest.raises(ChatRetentionConfigurationError):
        chat_retention_expiry(completed_at, retention_years=10)


def test_chat_conflicts_are_workspace_persistence_errors() -> None:
    assert issubclass(ChatSequenceConflictError, WorkspacePersistenceError)
