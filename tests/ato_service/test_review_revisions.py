"""Focused tests for review revision mutation audit wiring."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES, append_audit_event
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.poam_routing import PoamRoutingResult
from ato_service.review_revisions import (
    create_review_comment,
    create_review_revision,
    submit_review_revision,
    update_disposition,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
IDEM_KEY = "idem-key-0123456789"

RUN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REVIEW_REVISION_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
MATRIX_ROW_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
DISPOSITION_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

_APPEND_AUDIT_SIGNATURE = inspect.signature(append_audit_event)

OWNER_PRINCIPAL = AuthenticatedPrincipal(
    actor_id="owner@example.test",
    groups=("owners",),
    csrf_token="c" * 32,
    allowed_origins=("https://portal.example.test",),
)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


class _RecordingSession:
    def __init__(self, execute_results: list[Any]) -> None:
        self._execute_results = list(execute_results)
        self.added: list[object] = []

    async def execute(self, statement: object) -> Any:
        if not self._execute_results:
            raise AssertionError("unexpected execute call")
        return self._execute_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


class StrictAppendAuditEventSpy:
    """Reject kwargs that do not match append_audit_event's real signature."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session: object, **kwargs: Any) -> MagicMock:
        bound = _APPEND_AUDIT_SIGNATURE.bind(session, **kwargs)
        bound.apply_defaults()
        self.calls.append(dict(bound.arguments))
        return MagicMock()


def _system() -> MagicMock:
    system = MagicMock()
    system.owner_group = "owners"
    system.viewer_groups = ["owners"]
    return system


def _revision() -> MagicMock:
    revision = MagicMock()
    revision.package_revision_id = REVISION_ID
    revision.system_id = SYSTEM_ID
    revision.created_by = "owner@example.test"
    revision.profile_id = "fisma_agency_security"
    return revision


def _run_row() -> MagicMock:
    run = MagicMock()
    run.run_id = RUN_ID
    run.package_revision_id = REVISION_ID
    return run


def _review_revision(*, status: str = "draft", version: int = 1) -> MagicMock:
    review_revision = MagicMock()
    review_revision.review_revision_id = REVIEW_REVISION_ID
    review_revision.run_id = RUN_ID
    review_revision.version = version
    review_revision.status = status
    review_revision.created_by = "owner@example.test"
    review_revision.created_at = NOW
    return review_revision


def _matrix_row() -> MagicMock:
    matrix_row = MagicMock()
    matrix_row.matrix_row_id = MATRIX_ROW_ID
    matrix_row.system_status = "implemented"
    matrix_row.assessment_item_id = "AC-2"
    matrix_row.assessment_item_type = "control"
    matrix_row.finding_summary = "summary"
    return matrix_row


def _disposition(*, decision: str = "pending") -> MagicMock:
    disposition = MagicMock()
    disposition.disposition_id = DISPOSITION_ID
    disposition.review_revision_id = REVIEW_REVISION_ID
    disposition.matrix_row_id = MATRIX_ROW_ID
    disposition.decision = decision
    disposition.edited_summary = None
    disposition.notes = None
    disposition.version = 1
    disposition.decided_by = "owner@example.test"
    disposition.decided_at = NOW
    return disposition


def test_strict_append_audit_event_spy_rejects_invalid_now_kwarg() -> None:
    spy = StrictAppendAuditEventSpy()
    with pytest.raises(TypeError):
        _run(
            spy(
                MagicMock(),
                hmac_key=HMAC_KEY,
                actor_type="user",
                actor_id="owner@example.test",
                action="review_revision.create",
                object_type="review_revision",
                object_id=str(REVIEW_REVISION_ID).lower(),
                outcome="succeeded",
                reason_code=None,
                metadata={},
                now=NOW,
            )
        )


@pytest.mark.parametrize(
    ("mutation", "expected_action"),
    [
        ("create", "review_revision.create"),
        ("submit", "review_revision.submit"),
        ("disposition", "review_revision.disposition"),
        ("comment", "review_revision.comment"),
    ],
)
def test_review_revision_mutations_pass_occurred_at_to_append_audit_event(
    mutation: str,
    expected_action: str,
) -> None:
    audit_spy = StrictAppendAuditEventSpy()
    run = _run_row()
    revision = _revision()
    system = _system()
    review_revision = _review_revision()
    matrix_row = _matrix_row()

    if mutation == "create":
        session = _RecordingSession(
            [
                _scalar_result(run),
                _scalar_result(revision),
                _scalar_result(system),
                _scalars_result([matrix_row]),
                _scalar_result(None),
                _scalar_result(None),
            ]
        )
        with (
            patch(
                "ato_service.review_revisions.append_audit_event",
                audit_spy,
            ),
            patch(
                "ato_service.review_revisions.load_idempotency_replay",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "ato_service.review_revisions.record_idempotency_outcome",
                new=AsyncMock(),
            ),
        ):
            result = _run(
                create_review_revision(
                    session,
                    principal=OWNER_PRINCIPAL,
                    run_id=RUN_ID,
                    idempotency_key=IDEM_KEY,
                    hmac_key=HMAC_KEY,
                    now=NOW,
                )
            )
        assert result.status == 201
    elif mutation == "submit":
        resolved = _disposition(decision="accepted")
        session = _RecordingSession(
            [
                _scalar_result(review_revision),
                _scalar_result(run),
                _scalar_result(revision),
                _scalar_result(system),
                _scalars_result([]),
                _scalars_result([resolved]),
            ]
        )
        with patch("ato_service.review_revisions.append_audit_event", audit_spy):
            result = _run(
                submit_review_revision(
                    session,
                    principal=OWNER_PRINCIPAL,
                    review_revision_id=REVIEW_REVISION_ID,
                    if_match='"v1"',
                    idempotency_key=IDEM_KEY,
                    hmac_key=HMAC_KEY,
                    now=NOW,
                )
            )
        assert result.status == 200
        assert result.payload["status"] == "submitted"
        assert result.payload["version"] == 1
    elif mutation == "disposition":
        disposition = _disposition()
        session = _RecordingSession(
            [
                _scalar_result(review_revision),
                _scalar_result(run),
                _scalar_result(revision),
                _scalar_result(system),
                _scalars_result([]),
                _scalar_result(disposition),
                _scalar_result(matrix_row),
            ]
        )
        with (
            patch("ato_service.review_revisions.append_audit_event", audit_spy),
            patch(
                "ato_service.poam_routing.route_disposition_side_effects",
                new=AsyncMock(
                    return_value=PoamRoutingResult(
                        evidence_request_id=None,
                        poam_candidate_id=None,
                        created=False,
                    )
                ),
            ),
        ):
            _run(
                update_disposition(
                    session,
                    principal=OWNER_PRINCIPAL,
                    review_revision_id=REVIEW_REVISION_ID,
                    matrix_row_id=MATRIX_ROW_ID,
                    decision="accepted",
                    edited_summary=None,
                    notes=None,
                    if_match='"v1"',
                    hmac_key=HMAC_KEY,
                    now=NOW,
                )
            )
    else:
        session = _RecordingSession(
            [
                _scalar_result(review_revision),
                _scalar_result(run),
                _scalar_result(revision),
                _scalar_result(system),
                _scalars_result([]),
            ]
        )
        with (
            patch("ato_service.review_revisions.append_audit_event", audit_spy),
            patch(
                "ato_service.review_revisions.load_idempotency_replay",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "ato_service.review_revisions.record_idempotency_outcome",
                new=AsyncMock(),
            ),
        ):
            status, replayed = _run(
                create_review_comment(
                    session,
                    principal=OWNER_PRINCIPAL,
                    review_revision_id=REVIEW_REVISION_ID,
                    matrix_row_id=None,
                    body="reviewer note",
                    idempotency_key=IDEM_KEY,
                    hmac_key=HMAC_KEY,
                    now=NOW,
                )
            )[1:]
        assert status == 201
        assert replayed is False

    assert len(audit_spy.calls) == 1
    assert audit_spy.calls[0]["occurred_at"] == NOW
    assert audit_spy.calls[0]["action"] == expected_action


def test_create_review_revision_returns_existing_draft_for_run() -> None:
    existing = _review_revision(status="draft", version=3)
    disposition = _disposition()
    session = _RecordingSession(
        [
            _scalar_result(_run_row()),
            _scalar_result(_revision()),
            _scalar_result(_system()),
            _scalars_result([]),
            _scalar_result(existing),
        ]
    )

    with patch(
        "ato_service.review_revisions._load_dispositions",
        new=AsyncMock(return_value=[disposition]),
    ):
        result = _run(
            create_review_revision(
                session,
                principal=OWNER_PRINCIPAL,
                run_id=RUN_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert result.status == 200
    assert result.payload["review_revision_id"] == str(REVIEW_REVISION_ID).lower()
    assert result.payload["version"] == 3
    assert not session.added


def test_create_review_revision_returns_existing_submitted_for_run() -> None:
    existing = _review_revision(status="submitted", version=5)
    disposition = _disposition(decision="accepted")
    session = _RecordingSession(
        [
            _scalar_result(_run_row()),
            _scalar_result(_revision()),
            _scalar_result(_system()),
            _scalar_result(None),
            _scalar_result(existing),
        ]
    )

    with patch(
        "ato_service.review_revisions._load_dispositions",
        new=AsyncMock(return_value=[disposition]),
    ):
        result = _run(
            create_review_revision(
                session,
                principal=OWNER_PRINCIPAL,
                run_id=RUN_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert result.status == 200
    assert result.payload["status"] == "submitted"
    assert result.payload["review_revision_id"] == str(REVIEW_REVISION_ID).lower()
    assert result.payload["version"] == 5
    assert not session.added
