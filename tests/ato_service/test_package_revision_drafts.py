"""Focused tests for package editor draft read/write and draft-path confirm."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
from ato_service.idempotency import IdempotencyReplay
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.package_revision_drafts import (
    PackageRevisionDraftNotFoundError,
    compute_sealed_document_digest,
    get_package_revision_draft,
    save_package_revision_draft,
)
from ato_service.package_revisions import (
    UnconfirmedFactProposalsError,
    confirm_package_revision,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
IDEM_KEY = "idem-key-0123456789"
ROOT = Path(__file__).resolve().parents[2]

SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

OWNER_PRINCIPAL = AuthenticatedPrincipal(
    actor_id="owner@example.test",
    groups=("owners",),
    csrf_token="c" * 32,
    allowed_origins=("https://portal.example.test",),
)
VIEWER_PRINCIPAL = AuthenticatedPrincipal(
    actor_id="viewer@example.test",
    groups=("viewers",),
    csrf_token="d" * 32,
    allowed_origins=("https://portal.example.test",),
)

VALID_DOCUMENT = json.loads(
    (
        ROOT
        / "docs/contracts/fixtures/package-draft-document.valid.fisma-minimal.json"
    ).read_text(encoding="utf-8")
)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


@dataclass
class _SystemRow:
    system_id: uuid.UUID
    owner_group: str
    viewer_groups: list[str]
    display_name: str = "Fixture System"
    external_system_id: str | None = "ext-demo-1"


@dataclass
class _PackageRevisionRow:
    package_revision_id: uuid.UUID
    system_id: uuid.UUID
    profile_id: str
    impact_level: str | None
    revision_version: int
    status: str
    parent_revision_id: uuid.UUID | None = None
    certification_class: str | None = None
    data_origin: str = "synthetic"
    sensitivity: str = "internal_unclassified"
    effective_data_labels: list[str] | None = None
    authority_manifest_id: str = "fixture.draft"
    content_manifest_sha256: str | None = "a" * 64
    package_content_sha256: str | None = None
    system_context_snapshot_id: uuid.UUID | None = None
    created_by: str = "owner@example.test"
    created_at: datetime = NOW

    def __post_init__(self) -> None:
        if self.effective_data_labels is None:
            self.effective_data_labels = ["internal_unclassified", "synthetic"]


@dataclass
class _DraftRow:
    package_revision_id: uuid.UUID
    document_schema_version: str
    document: dict[str, Any]
    field_provenance: dict[str, Any]
    updated_by: str
    updated_at: datetime


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


def _scalar_one_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _one_or_none_result(value: object) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = value
    return result


def _system() -> _SystemRow:
    return _SystemRow(
        system_id=SYSTEM_ID,
        owner_group="owners",
        viewer_groups=["viewers"],
    )


def _revision(*, revision_version: int = 4, status: str = "awaiting_confirmation") -> _PackageRevisionRow:
    return _PackageRevisionRow(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        profile_id="fisma_agency_security",
        impact_level="moderate",
        revision_version=revision_version,
        status=status,
    )


def _draft(*, document: dict[str, Any] | None = None) -> _DraftRow:
    return _DraftRow(
        package_revision_id=REVISION_ID,
        document_schema_version="1.0.0",
        document=dict(document or VALID_DOCUMENT),
        field_provenance={
            "/system/display_name": {
                "source_artifact_id": "33333333-3333-4333-8333-333333333333",
                "source_sha256": "a" * 64,
                "source_locator": {"json_pointer": "/system/display_name"},
                "extraction_method": "deterministic",
                "model_step_id": None,
            }
        },
        updated_by="intake-worker",
        updated_at=NOW,
    )


def test_compute_sealed_document_digest_is_canonical() -> None:
    digest_a = compute_sealed_document_digest(VALID_DOCUMENT)
    digest_b = compute_sealed_document_digest(json.loads(json.dumps(VALID_DOCUMENT)))
    assert digest_a == digest_b
    assert len(digest_a) == 64


def test_get_draft_allows_viewer_read_access() -> None:
    revision = _revision()
    draft = _draft()
    session = _RecordingSession(
        [
            _one_or_none_result((revision, _system())),
            _scalar_result(draft),
        ]
    )

    result = _run(
        get_package_revision_draft(
            session,  # type: ignore[arg-type]
            principal=VIEWER_PRINCIPAL,
            package_revision_id=REVISION_ID,
        )
    )

    assert result.payload["object_type"] == "package_revision_draft"
    assert result.payload["revision_version"] == 4
    assert result.etag == '"v4"'


def test_get_draft_missing_returns_not_found() -> None:
    revision = _revision()
    session = _RecordingSession(
        [
            _one_or_none_result((revision, _system())),
            _scalar_result(None),
        ]
    )

    with pytest.raises(PackageRevisionDraftNotFoundError):
        _run(
            get_package_revision_draft(
                session,  # type: ignore[arg-type]
                principal=VIEWER_PRINCIPAL,
                package_revision_id=REVISION_ID,
            )
        )


@patch("ato_service.package_revision_drafts.load_idempotency_replay", new_callable=AsyncMock)
def test_save_draft_requires_if_match(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    session = _RecordingSession([])

    with pytest.raises(IfMatchRequiredError):
        _run(
            save_package_revision_draft(
                session,  # type: ignore[arg-type]
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                document=VALID_DOCUMENT,
                if_match=None,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revision_drafts.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revision_drafts.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revision_drafts.load_idempotency_replay", new_callable=AsyncMock)
def test_save_draft_persists_document_and_increments_revision_version(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()

    revision = _revision(revision_version=4)
    draft = _draft()
    edited = dict(VALID_DOCUMENT)
    edited["package"] = dict(edited["package"])
    edited["package"]["title"] = "Edited title"

    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_result(draft),
        ]
    )

    result = _run(
        save_package_revision_draft(
            session,  # type: ignore[arg-type]
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            document=edited,
            if_match='"v4"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert draft.document["package"]["title"] == "Edited title"
    assert revision.revision_version == 5
    assert result.etag == '"v5"'
    assert result.payload["revision_version"] == 5
    mock_audit.assert_called_once()
    mock_record.assert_called_once()


@patch("ato_service.package_revision_drafts.load_idempotency_replay", new_callable=AsyncMock)
def test_save_draft_rejects_stale_if_match(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision(revision_version=4)
    draft = _draft()
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_result(draft),
        ]
    )

    with pytest.raises(EtagMismatchError):
        _run(
            save_package_revision_draft(
                session,  # type: ignore[arg-type]
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                document=VALID_DOCUMENT,
                if_match='"v3"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.revision_version == 4


@patch("ato_service.package_revision_drafts.load_idempotency_replay", new_callable=AsyncMock)
def test_save_draft_rejects_non_awaiting_confirmation_state(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision(status="ready", revision_version=6)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(IllegalStateTransitionError):
        _run(
            save_package_revision_draft(
                session,  # type: ignore[arg-type]
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                document=VALID_DOCUMENT,
                if_match='"v6"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revision_drafts.load_idempotency_replay", new_callable=AsyncMock)
def test_save_draft_denied_for_viewer(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    session = _RecordingSession(
        [
            _scalar_result(_revision()),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(AuthorizationDeniedError):
        _run(
            save_package_revision_draft(
                session,  # type: ignore[arg-type]
                principal=VIEWER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                document=VALID_DOCUMENT,
                if_match='"v4"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    mock_load.assert_not_called()


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_with_draft_seals_content_without_fact_proposal_check(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()

    revision = _revision(revision_version=4)
    draft = _draft()
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_result(draft),
            _scalar_one_result(0),
        ]
    )

    result = _run(
        confirm_package_revision(
            session,  # type: ignore[arg-type]
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            if_match='"v4"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    expected_digest = compute_sealed_document_digest(draft.document)
    assert revision.status == "ready"
    assert revision.revision_version == 5
    assert revision.package_content_sha256 == expected_digest
    assert revision.system_context_snapshot_id is not None
    assert result.payload["package_content_sha256"] == expected_digest
    assert len(session.added) == 2
    mock_audit.assert_called_once()
    audit_metadata = mock_audit.await_args.kwargs["metadata"]
    assert audit_metadata["confirm_path"] == "package_editor_draft"
    assert audit_metadata["package_content_sha256"] == expected_digest


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_without_draft_still_requires_non_pending_proposals(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()

    revision = _revision(revision_version=2)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_result(None),
            _scalar_one_result(True),
        ]
    )

    with pytest.raises(UnconfirmedFactProposalsError):
        _run(
            confirm_package_revision(
                session,  # type: ignore[arg-type]
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.revision_version == 2


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_with_draft_replays_without_duplicate_seal_side_effects(
    mock_load: AsyncMock,
) -> None:
    payload = {
        "revision_version": 5,
        "status": "ready",
        "package_content_sha256": "b" * 64,
    }
    mock_load.return_value = IdempotencyReplay(200, payload, response_headers={"ETag": '"v5"'})
    revision = _revision(revision_version=4)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
        ]
    )

    result = _run(
        confirm_package_revision(
            session,  # type: ignore[arg-type]
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            if_match='"v4"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.replayed is True
    assert result.status == 200
    assert revision.revision_version == 4
    assert session.added == []
