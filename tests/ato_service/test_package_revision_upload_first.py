"""Focused tests for upload-first PackageRevision metadata deferral and PATCH."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
from ato_service.db.models import PackageRevision, System
from ato_service.idempotency import IdempotencyReplay
from ato_service.intake import IntakeRevisionSnapshot
from ato_service.normalization_service import revision_metadata_ready_for_model
from ato_service.package_revisions import (
    CreatePackageRevisionInput,
    PackageRevisionValidationError,
    ParentRevisionNotReadyError,
    PatchMetadataStateError,
    PatchPackageRevisionMetadataInput,
    ProfileBoundaryError,
    create_package_revision,
    create_request_digest,
    patch_package_revision_metadata,
    patch_metadata_request_digest,
    validate_create_input,
    validate_patch_metadata_boundaries,
    validate_patch_metadata_input,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
IDEM_KEY = "idem-key-0123456789"

SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PARENT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")

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


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


@dataclass
class _SystemRow:
    system_id: uuid.UUID
    owner_group: str
    viewer_groups: list[str]
    display_name: str = "Example System"


class _RecordingSession:
    def __init__(self, execute_results: list[Any]) -> None:
        self._execute_results = list(execute_results)
        self.added: list[object] = []
        self.execute_calls: list[object] = []

    async def execute(self, statement: object) -> Any:
        self.execute_calls.append(statement)
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


def _revision_row(
    *,
    status: str = "scanning",
    revision_version: int = 2,
    profile_id: str | None = None,
    parent_revision_id: uuid.UUID | None = None,
) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=SYSTEM_ID,
        parent_revision_id=parent_revision_id,
        profile_id=profile_id,
        certification_class=None,
        impact_level="moderate" if profile_id == "fisma_agency_security" else None,
        data_origin=None,
        sensitivity=None,
        effective_data_labels=[],
        authority_manifest_id="authority.v2",
        content_manifest_sha256="a" * 64,
        revision_version=revision_version,
        status=status,
        created_by=OWNER_PRINCIPAL.actor_id,
        created_at=NOW,
    )


def _system_row() -> _SystemRow:
    return _SystemRow(system_id=SYSTEM_ID, owner_group="owners", viewer_groups=["viewers"])


def _fisma_create_input(*, parent_revision_id: uuid.UUID | None = None) -> CreatePackageRevisionInput:
    return CreatePackageRevisionInput(
        parent_revision_id=parent_revision_id,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
    )


def test_validate_create_input_requires_profile_id() -> None:
    with pytest.raises(PackageRevisionValidationError, match="profile_id is required"):
        validate_create_input(
            parent_revision_id=None,
            profile_id=None,
            certification_class=None,
            impact_level="moderate",
            data_origin="synthetic",
            sensitivity="internal_unclassified",
        )


def test_validate_create_input_rejects_invalid_profile_boundary() -> None:
    with pytest.raises(ProfileBoundaryError):
        validate_create_input(
            parent_revision_id=None,
            profile_id="fedramp_20x_program",
            certification_class="B",
            impact_level="moderate",
            data_origin="synthetic",
            sensitivity="internal_unclassified",
        )


def test_validate_create_input_rejects_classified_sensitivity() -> None:
    with pytest.raises(PackageRevisionValidationError) as exc_info:
        validate_create_input(
            parent_revision_id=None,
            profile_id="fisma_agency_security",
            certification_class=None,
            impact_level="moderate",
            data_origin="synthetic",
            sensitivity="classified",
        )
    assert exc_info.value.error_code == "classified_data_unsupported"


def test_create_request_digest_includes_metadata() -> None:
    request = _fisma_create_input()
    digest = create_request_digest(system_id=SYSTEM_ID, request=request)
    other = create_request_digest(
        system_id=SYSTEM_ID,
        request=CreatePackageRevisionInput(
            parent_revision_id=None,
            profile_id="fedramp_20x_program",
            certification_class="B",
            impact_level=None,
            data_origin="synthetic",
            sensitivity="internal_unclassified",
        ),
    )
    assert digest != other
    assert len(digest) == 64


def test_validate_patch_metadata_requires_at_least_one_field() -> None:
    with pytest.raises(PackageRevisionValidationError):
        validate_patch_metadata_input(provided={})


def test_validate_patch_metadata_boundaries_require_profile_before_impact() -> None:
    patch = validate_patch_metadata_input(provided={"impact_level": "moderate"})
    with pytest.raises(PackageRevisionValidationError):
        validate_patch_metadata_boundaries(
            current_profile_id=None,
            current_certification_class=None,
            current_impact_level=None,
            patch=patch,
        )


def test_patch_metadata_request_digest_is_stable() -> None:
    patch = validate_patch_metadata_input(
        provided={
            "profile_id": "fisma_agency_security",
            "impact_level": "moderate",
        }
    )
    first = patch_metadata_request_digest(
        package_revision_id=REVISION_ID,
        if_match='"v2"',
        patch=patch,
    )
    second = patch_metadata_request_digest(
        package_revision_id=REVISION_ID,
        if_match='"v2"',
        patch=patch,
    )
    assert first == second


@patch("ato_service.package_revisions.synchronize_draft_after_metadata_patch", new_callable=AsyncMock)
@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_updates_revision_and_increments_version(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
    mock_sync: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()
    mock_sync.return_value = None

    revision = _revision_row(status="scanning", revision_version=2)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system_row()),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={
            "profile_id": "fisma_agency_security",
            "impact_level": "moderate",
            "data_origin": "synthetic",
            "sensitivity": "internal_unclassified",
        }
    )

    result = _run(
        patch_package_revision_metadata(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            patch=patch,
            if_match='"v2"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.status == 200
    assert result.etag == '"v3"'
    assert revision.profile_id == "fisma_agency_security"
    assert revision.data_origin == "synthetic"
    assert revision.sensitivity == "internal_unclassified"
    assert revision.effective_data_labels == ["internal_unclassified", "synthetic"]
    assert revision.revision_version == 3
    mock_sync.assert_awaited_once()
    audit_metadata = mock_audit.await_args.kwargs["metadata"]
    assert audit_metadata["provided_fields"] == (
        "data_origin,impact_level,profile_id,sensitivity"
    )
    assert isinstance(audit_metadata["provided_fields"], str)


@patch("ato_service.package_revisions.synchronize_draft_after_metadata_patch", new_callable=AsyncMock)
@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_audit_metadata_passes_audit_validation(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
    mock_sync: AsyncMock,
) -> None:
    from ato_service.audit import append_audit_event

    mock_load.return_value = None
    mock_audit.side_effect = append_audit_event
    mock_record.return_value = MagicMock()
    mock_sync.return_value = None

    revision = _revision_row(status="awaiting_confirmation", revision_version=5)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system_row()),
            _scalar_result(None),
            _scalar_result(None),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={
            "profile_id": "fisma_agency_security",
            "impact_level": "moderate",
            "data_origin": "synthetic",
            "sensitivity": "internal_unclassified",
        }
    )

    result = _run(
        patch_package_revision_metadata(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            patch=patch,
            if_match='"v5"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.status == 200
    assert len(session.added) == 1


@patch("ato_service.package_revisions.synchronize_draft_after_metadata_patch", new_callable=AsyncMock)
@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_allows_uploading_state(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
    mock_sync: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()
    mock_sync.return_value = None

    revision = _revision_row(status="uploading", revision_version=1)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system_row()),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={
            "profile_id": "fisma_agency_security",
            "impact_level": "high",
        }
    )

    result = _run(
        patch_package_revision_metadata(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            patch=patch,
            if_match='"v1"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.status == 200
    assert revision.impact_level == "high"
    assert revision.revision_version == 2


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_rejects_ready_state(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision_row(status="ready", revision_version=3, profile_id="fisma_agency_security")
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system_row()),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={"profile_id": "fisma_agency_security", "impact_level": "moderate"}
    )

    with pytest.raises(PatchMetadataStateError):
        _run(
            patch_package_revision_metadata(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                patch=patch,
                if_match='"v3"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_requires_if_match(mock_load: AsyncMock) -> None:
    session = _RecordingSession([])
    patch = validate_patch_metadata_input(
        provided={"profile_id": "fisma_agency_security", "impact_level": "moderate"}
    )

    with pytest.raises(IfMatchRequiredError):
        _run(
            patch_package_revision_metadata(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                patch=patch,
                if_match=None,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_rejects_stale_etag(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision_row(status="extracting", revision_version=4)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system_row()),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={"profile_id": "fisma_agency_security", "impact_level": "moderate"}
    )

    with pytest.raises(EtagMismatchError):
        _run(
            patch_package_revision_metadata(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                patch=patch,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_patch_metadata_replay_denied_for_viewer(mock_load: AsyncMock) -> None:
    mock_load.return_value = IdempotencyReplay(200, {"revision_version": 3})
    session = _RecordingSession(
        [
            _scalar_result(_revision_row(status="scanning")),
            _scalar_one_result(_system_row()),
        ]
    )
    patch = validate_patch_metadata_input(
        provided={"profile_id": "fisma_agency_security", "impact_level": "moderate"}
    )

    with pytest.raises(AuthorizationDeniedError):
        _run(
            patch_package_revision_metadata(
                session,
                principal=VIEWER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                patch=patch,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    mock_load.assert_not_called()


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_child_requires_ready_parent(mock_load: AsyncMock, mock_audit: AsyncMock, mock_record: AsyncMock) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()
    parent = _revision_row(status="awaiting_confirmation", profile_id="fisma_agency_security")
    parent.package_revision_id = PARENT_ID
    session = _RecordingSession(
        [
            _scalar_result(_system_row()),
            _scalar_result(parent),
        ]
    )

    with pytest.raises(ParentRevisionNotReadyError):
        _run(
            create_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                system_id=SYSTEM_ID,
                request=_fisma_create_input(parent_revision_id=PARENT_ID),
                authority_manifest_id="authority.v2",
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_child_uses_caller_metadata_not_parent(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()
    parent = _revision_row(status="ready", profile_id="fisma_agency_security")
    parent.package_revision_id = PARENT_ID
    parent.impact_level = "moderate"
    session = _RecordingSession(
        [
            _scalar_result(_system_row()),
            _scalar_result(parent),
        ]
    )
    request = CreatePackageRevisionInput(
        parent_revision_id=PARENT_ID,
        profile_id="fedramp_20x_program",
        certification_class="C",
        impact_level=None,
        data_origin="synthetic",
        sensitivity="internal_unclassified",
    )

    result = _run(
        create_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            system_id=SYSTEM_ID,
            request=request,
            authority_manifest_id="authority.v2",
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.status == 201
    created = session.added[0]
    assert created.profile_id == "fedramp_20x_program"
    assert created.certification_class == "C"
    assert created.impact_level is None
    assert created.data_origin == "synthetic"
    assert created.sensitivity == "internal_unclassified"
    assert created.effective_data_labels == ["internal_unclassified", "synthetic"]
    audit_metadata = mock_audit.await_args.kwargs["metadata"]
    assert audit_metadata["profile_id"] == "fedramp_20x_program"
    assert audit_metadata["data_origin"] == "synthetic"
    assert audit_metadata["sensitivity"] == "internal_unclassified"


def test_revision_metadata_ready_for_model_requires_human_labels() -> None:
    snapshot = IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=2,
        status="extracting",
        profile_id="fisma_agency_security",
        impact_level="moderate",
        content_manifest_sha256="a" * 64,
        data_origin=None,
        sensitivity=None,
        system_id=SYSTEM_ID,
        system_display_name="Example System",
        artifacts=(),
    )
    assert revision_metadata_ready_for_model(snapshot) is False

    complete = IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=2,
        status="extracting",
        profile_id="fisma_agency_security",
        impact_level="moderate",
        content_manifest_sha256="a" * 64,
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        system_id=SYSTEM_ID,
        system_display_name="Example System",
        artifacts=(),
    )
    assert revision_metadata_ready_for_model(complete) is True
