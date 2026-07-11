"""Tests for fact proposal list and review routes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Awaitable, TypeVar
from unittest.mock import MagicMock

import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.concurrency import IfMatchRequiredError
from ato_service.fact_proposals import (
    FactProposalNotFoundError,
    FactProposalReviewConflictError,
    accept_fact_proposal,
    list_fact_proposals,
    reject_fact_proposal,
)
from ato_service.package_revisions import PackageRevisionNotFoundError

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)

PACKAGE_REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PROPOSAL_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="actor-1",
        groups=("owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


def _revision(*, status: str = "awaiting_confirmation", revision_version: int = 3) -> MagicMock:
    revision = MagicMock()
    revision.package_revision_id = PACKAGE_REVISION_ID
    revision.system_id = SYSTEM_ID
    revision.status = status
    revision.revision_version = revision_version
    return revision


def _system() -> MagicMock:
    system = MagicMock()
    system.owner_group = "owners"
    system.viewer_groups = ["viewers"]
    return system


def _proposal(*, review_status: str = "pending") -> MagicMock:
    proposal = MagicMock()
    proposal.fact_proposal_id = PROPOSAL_ID
    proposal.package_revision_id = PACKAGE_REVISION_ID
    proposal.json_pointer = "/system_name"
    proposal.proposed_value = "Example System"
    proposal.source_artifact_id = uuid.uuid4()
    proposal.source_sha256 = "a" * 64
    proposal.source_locator = {"kind": "json_pointer", "json_pointer": "/system_name"}
    proposal.extraction_method = "deterministic"
    proposal.model_step_id = None
    proposal.review_status = review_status
    proposal.reviewed_by = None
    proposal.reviewed_at = None
    return proposal


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value

    def scalar_one_or_none(self) -> object | None:
        return self._value

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[object]:
        if isinstance(self._value, list):
            return self._value
        if self._value is None:
            return []
        return [self._value]


class _RecordingSession:
    def __init__(self, execute_results: list[object]) -> None:
        self.execute_results = list(execute_results)
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> _ScalarResult:
        self.execute_calls.append(statement)
        if not self.execute_results:
            raise AssertionError("unexpected execute()")
        value = self.execute_results.pop(0)
        return _ScalarResult(value)


def test_list_fact_proposals_returns_domain_json() -> None:
    session = _RecordingSession(
        [_revision(), _system(), [_proposal()]],
    )
    page = _run(
        list_fact_proposals(
        session,  # type: ignore[arg-type]
        principal=_principal(),
        package_revision_id=PACKAGE_REVISION_ID,
        cursor=None,
        limit=None,
        )
    )
    assert page.items[0]["object_type"] == "fact_proposal"
    assert page.items[0]["json_pointer"] == "/system_name"


def test_accept_fact_proposal_requires_if_match() -> None:
    session = _RecordingSession([])
    with pytest.raises(IfMatchRequiredError):
        _run(
            accept_fact_proposal(
            session,  # type: ignore[arg-type]
            principal=_principal(),
            fact_proposal_id=PROPOSAL_ID,
            if_match=None,
            edited_value=None,
            hmac_key=b"audit-key",
            now=NOW,
            )
        )


def test_accept_fact_proposal_rejects_non_awaiting_parent() -> None:
    session = _RecordingSession(
        [_proposal(), _revision(status="ready"), _system()],
    )
    with pytest.raises(FactProposalReviewConflictError):
        _run(
            accept_fact_proposal(
            session,  # type: ignore[arg-type]
            principal=_principal(),
            fact_proposal_id=PROPOSAL_ID,
            if_match='"v3"',
            edited_value=None,
            hmac_key=b"audit-key",
            now=NOW,
            )
        )


def test_list_fact_proposals_missing_revision_raises_not_found() -> None:
    session = _RecordingSession([None])
    with pytest.raises(PackageRevisionNotFoundError):
        _run(
            list_fact_proposals(
            session,  # type: ignore[arg-type]
            principal=_principal(),
            package_revision_id=PACKAGE_REVISION_ID,
            cursor=None,
            limit=None,
            )
        )


def test_reject_fact_proposal_missing_row_raises_not_found() -> None:
    session = _RecordingSession([None])
    with pytest.raises(FactProposalNotFoundError):
        _run(
            reject_fact_proposal(
            session,  # type: ignore[arg-type]
            principal=_principal(),
            fact_proposal_id=PROPOSAL_ID,
            if_match='"v3"',
            reason="not applicable",
            hmac_key=b"audit-key",
            now=NOW,
            )
        )
