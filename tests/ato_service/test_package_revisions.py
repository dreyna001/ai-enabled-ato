"""Focused tests for PackageRevision persistence and lifecycle service."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal, AuthorizationDeniedError
from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
from ato_service.content_manifests import StoredContentManifest
from ato_service.idempotency import IdempotencyReplay
from ato_service.lifecycle_transitions import IllegalStateTransitionError
from ato_service.package_revisions import (
    CreatePackageRevisionInput,
    EmptyPackageRevisionError,
    PackageRevisionNotFoundError,
    PackageRevisionStorageError,
    ParentRevisionNotFoundError,
    ProfileBoundaryError,
    SystemNotFoundError,
    UnconfirmedFactProposalsError,
    _load_package_revision_for_update_statement,
    _load_source_artifacts_statement,
    _load_system_for_update_statement,
    _pending_fact_proposal_exists_statement,
    confirm_package_revision,
    confirm_request_digest,
    create_package_revision,
    create_request_digest,
    finalize_package_revision,
    finalize_request_digest,
    get_package_revision,
    list_package_revisions,
    validate_profile_boundaries,
)
from ato_service.runtime_config import RuntimeLimits

UTC = timezone.utc
NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
IDEM_KEY = "idem-key-0123456789"
ROOT = Path(__file__).resolve().parents[2]

SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
PARENT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ARTIFACT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")

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

LIMITS = RuntimeLimits(
    max_model_calls_per_run=120,
    max_package_bytes=2_147_483_648,
    max_single_file_bytes=104_857_600,
    max_files_per_revision=500,
)


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _compile_sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@dataclass
class _SystemRow:
    system_id: uuid.UUID
    owner_group: str
    viewer_groups: list[str]


@dataclass
class _PackageRevisionRow:
    package_revision_id: uuid.UUID
    system_id: uuid.UUID
    parent_revision_id: uuid.UUID | None
    profile_id: str
    certification_class: str | None
    impact_level: str | None
    data_origin: str
    sensitivity: str
    effective_data_labels: list[str]
    authority_manifest_id: str
    content_manifest_sha256: str | None
    revision_version: int
    status: str
    created_by: str
    created_at: datetime


@dataclass
class _SourceArtifactRow:
    artifact_id: uuid.UUID
    package_revision_id: uuid.UUID
    storage_key: str
    sha256: str
    size_bytes: int


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


def _scalars_all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _one_or_none_result(value: object) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = value
    return result


def _fisma_create_input() -> CreatePackageRevisionInput:
    return CreatePackageRevisionInput(
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
    )


def _system(*, owner_group: str = "owners", viewer_groups: list[str] | None = None) -> _SystemRow:
    return _SystemRow(
        system_id=SYSTEM_ID,
        owner_group=owner_group,
        viewer_groups=viewer_groups or ["viewers"],
    )


def _revision(
    *,
    status: str = "uploading",
    revision_version: int = 1,
    content_manifest_sha256: str | None = None,
    system_id: uuid.UUID = SYSTEM_ID,
) -> _PackageRevisionRow:
    return _PackageRevisionRow(
        package_revision_id=REVISION_ID,
        system_id=system_id,
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        effective_data_labels=["internal_unclassified", "synthetic"],
        authority_manifest_id="authority.v2",
        content_manifest_sha256=content_manifest_sha256,
        revision_version=revision_version,
        status=status,
        created_by=OWNER_PRINCIPAL.actor_id,
        created_at=NOW,
    )


def _artifact() -> _SourceArtifactRow:
    digest = "a" * 64
    return _SourceArtifactRow(
        artifact_id=ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        storage_key=f"{digest[:2]}/{digest}",
        sha256=digest,
        size_bytes=12,
    )


@pytest.mark.parametrize(
    ("profile_id", "certification_class", "impact_level"),
    [
        ("fisma_agency_security", None, "moderate"),
        ("fedramp_rev5_transition", None, "high"),
        ("fedramp_20x_program", "B", None),
        ("fedramp_20x_program", "C", None),
    ],
)
def test_validate_profile_boundaries_accepts_contract_values(
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> None:
    validate_profile_boundaries(
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
    )


@pytest.mark.parametrize(
    ("profile_id", "certification_class", "impact_level"),
    [
        ("fisma_agency_security", "B", "moderate"),
        ("fedramp_rev5_transition", "C", "moderate"),
        ("fedramp_20x_program", "B", "low"),
        ("fedramp_20x_program", "C", "moderate"),
        ("fedramp_20x_program", "B", "moderate"),
        ("fedramp_20x_program", "C", "low"),
        ("fedramp_20x_program", None, None),
    ],
)
def test_validate_profile_boundaries_rejects_invalid_combinations(
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> None:
    with pytest.raises(ProfileBoundaryError):
        validate_profile_boundaries(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
        )


def test_create_request_digest_is_stable() -> None:
    request = _fisma_create_input()
    first = create_request_digest(system_id=SYSTEM_ID, request=request)
    second = create_request_digest(system_id=SYSTEM_ID, request=request)
    assert first == second
    assert len(first) == 64


def test_confirm_request_digest_includes_if_match() -> None:
    digest_with = confirm_request_digest(
        package_revision_id=REVISION_ID,
        if_match='"v2"',
    )
    digest_without = finalize_request_digest(package_revision_id=REVISION_ID)
    assert digest_with != digest_without


def test_lock_statements_use_for_update() -> None:
    system_sql = _compile_sql(_load_system_for_update_statement(SYSTEM_ID))
    revision_sql = _compile_sql(
        _load_package_revision_for_update_statement(REVISION_ID)
    )
    assert "FOR UPDATE" in system_sql
    assert "FOR UPDATE" in revision_sql


def test_source_artifact_query_orders_by_artifact_id() -> None:
    sql = _compile_sql(_load_source_artifacts_statement(REVISION_ID))
    assert "ORDER BY source_artifacts.artifact_id ASC" in sql


def test_pending_fact_proposal_exists_uses_exists_subquery() -> None:
    sql = _compile_sql(_pending_fact_proposal_exists_statement(REVISION_ID))
    assert "EXISTS" in sql
    assert "review_status" in sql


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_replays_after_system_lock_and_owner_auth(mock_load: AsyncMock) -> None:
    payload = {"revision_version": 1, "status": "uploading"}
    mock_load.return_value = IdempotencyReplay(201, payload)

    session = _RecordingSession([_scalar_result(_system())])

    result = _run(
        create_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            system_id=SYSTEM_ID,
            request=_fisma_create_input(),
            authority_manifest_id="authority.v2",
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.replayed is True
    assert result.status == 201
    assert result.payload == payload
    assert len(session.execute_calls) == 1
    system_sql = _compile_sql(session.execute_calls[0])
    assert "systems" in system_sql
    assert "FOR UPDATE" in system_sql
    assert session.added == []


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_replay_denied_for_revoked_viewer(mock_load: AsyncMock) -> None:
    mock_load.return_value = IdempotencyReplay(201, {"revision_version": 1})
    session = _RecordingSession([_scalar_result(_system())])

    with pytest.raises(AuthorizationDeniedError):
        _run(
            create_package_revision(
                session,
                principal=VIEWER_PRINCIPAL,
                system_id=SYSTEM_ID,
                request=_fisma_create_input(),
                authority_manifest_id="authority.v2",
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    mock_load.assert_not_called()
    assert session.added == []


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_persists_uploading_revision_and_records_outcome(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()

    session = _RecordingSession([_scalar_result(_system())])

    result = _run(
        create_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            system_id=SYSTEM_ID,
            request=_fisma_create_input(),
            authority_manifest_id="authority.v2",
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.replayed is False
    assert result.status == 201
    assert result.etag == '"v1"'
    assert len(session.added) == 1
    created = session.added[0]
    assert created.status == "uploading"
    assert created.content_manifest_sha256 is None
    assert created.revision_version == 1
    assert created.effective_data_labels == ["internal_unclassified", "synthetic"]
    assert created.authority_manifest_id == "authority.v2"
    mock_audit.assert_called_once()
    mock_record.assert_called_once()


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_rejects_viewer_mutation_before_add(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    session = _RecordingSession([_scalar_result(_system())])
    with pytest.raises(AuthorizationDeniedError):
        _run(
            create_package_revision(
                session,
                principal=VIEWER_PRINCIPAL,
                system_id=SYSTEM_ID,
                request=_fisma_create_input(),
                authority_manifest_id="authority.v2",
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert session.added == []


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_create_rejects_missing_parent_without_mutation(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    request = CreatePackageRevisionInput(
        parent_revision_id=PARENT_ID,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level="moderate",
        data_origin="synthetic",
        sensitivity="internal_unclassified",
    )
    session = _RecordingSession(
        [
            _scalar_result(_system()),
            _scalar_result(None),
        ]
    )

    with pytest.raises(ParentRevisionNotFoundError):
        _run(
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

    assert session.added == []


def test_not_found_errors_expose_ids_internally_but_generic_str() -> None:
    revision_error = PackageRevisionNotFoundError(package_revision_id=REVISION_ID)
    system_error = SystemNotFoundError(system_id=SYSTEM_ID)
    parent_error = ParentRevisionNotFoundError(parent_revision_id=PARENT_ID)

    assert revision_error.package_revision_id == REVISION_ID
    assert system_error.system_id == SYSTEM_ID
    assert parent_error.parent_revision_id == PARENT_ID
    assert str(revision_error) == "requested resource was not found"
    assert str(system_error) == "requested resource was not found"
    assert str(parent_error) == "requested resource was not found"
    assert str(REVISION_ID) not in str(revision_error)
    assert str(SYSTEM_ID) not in str(system_error)
    assert str(PARENT_ID) not in str(parent_error)


def test_get_raises_not_found_for_missing_revision() -> None:
    session = _RecordingSession([_one_or_none_result(None)])

    with pytest.raises(PackageRevisionNotFoundError) as exc_info:
        _run(
            get_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
            )
        )

    assert exc_info.value.package_revision_id == REVISION_ID
    assert str(exc_info.value) == "requested resource was not found"


def test_get_returns_domain_payload_for_authorized_reader() -> None:
    revision = _revision()
    system = _system()
    session = _RecordingSession([_one_or_none_result((revision, system))])

    payload = _run(
        get_package_revision(
            session,
            principal=VIEWER_PRINCIPAL,
            package_revision_id=REVISION_ID,
        )
    )

    assert payload["package_revision_id"] == str(REVISION_ID).lower()
    assert payload["revision_version"] == 1


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
@patch("ato_service.package_revisions.asyncio.to_thread", new_callable=AsyncMock)
def test_finalize_writes_manifest_before_db_mutation(
    mock_to_thread: AsyncMock,
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()
    manifest = StoredContentManifest(
        manifest_storage_key="manifests/packages/x/content-manifest.json",
        sha256="b" * 64,
        size_bytes=128,
        document={"schema_version": "1.0.0"},
    )
    mock_to_thread.return_value = manifest

    revision = _revision()
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalars_all_result([_artifact()]),
        ]
    )

    result = _run(
        finalize_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            storage_root=ROOT / "tmp-storage",
            project_root=ROOT,
            limits=LIMITS,
            now=NOW,
        )
    )

    mock_to_thread.assert_called_once()
    mock_audit.assert_called_once()
    mock_record.assert_called_once()
    assert revision.status == "scanning"
    assert revision.content_manifest_sha256 == manifest.sha256
    assert revision.revision_version == 2
    assert result.status == 202
    assert result.etag == '"v2"'


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
@patch("ato_service.package_revisions.asyncio.to_thread", new_callable=AsyncMock)
def test_finalize_manifest_error_does_not_mutate_revision(
    mock_to_thread: AsyncMock,
    mock_load: AsyncMock,
) -> None:
    from ato_service.content_manifests import ContentManifestCommitError

    mock_load.return_value = None
    mock_to_thread.side_effect = ContentManifestCommitError("commit failed")

    revision = _revision()
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalars_all_result([_artifact()]),
        ]
    )

    with pytest.raises(PackageRevisionStorageError):
        _run(
            finalize_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                storage_root=ROOT / "tmp-storage",
                project_root=ROOT,
                limits=LIMITS,
                now=NOW,
            )
        )

    assert revision.status == "uploading"
    assert revision.content_manifest_sha256 is None
    assert revision.revision_version == 1


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_finalize_rejects_empty_package_without_manifest_call(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision()
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalars_all_result([]),
        ]
    )

    with patch("ato_service.package_revisions.asyncio.to_thread") as mock_to_thread:
        with pytest.raises(EmptyPackageRevisionError):
            _run(
                finalize_package_revision(
                    session,
                    principal=OWNER_PRINCIPAL,
                    package_revision_id=REVISION_ID,
                    idempotency_key=IDEM_KEY,
                    hmac_key=HMAC_KEY,
                    storage_root=ROOT / "tmp-storage",
                    project_root=ROOT,
                    limits=LIMITS,
                    now=NOW,
                )
            )
        mock_to_thread.assert_not_called()

    assert revision.revision_version == 1


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_finalize_rejects_non_uploading_state(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision(status="scanning")
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(IllegalStateTransitionError):
        _run(
            finalize_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                storage_root=ROOT / "tmp-storage",
                project_root=ROOT,
                limits=LIMITS,
                now=NOW,
            )
        )

    assert revision.revision_version == 1


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_finalize_replay_after_lock_and_auth(mock_load: AsyncMock) -> None:
    payload = {"revision_version": 2, "status": "scanning"}
    mock_load.return_value = IdempotencyReplay(202, payload)
    session = _RecordingSession(
        [
            _scalar_result(_revision()),
            _scalar_one_result(_system()),
        ]
    )

    result = _run(
        finalize_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            storage_root=ROOT / "tmp-storage",
            project_root=ROOT,
            limits=LIMITS,
            now=NOW,
        )
    )

    assert result.replayed is True
    assert result.status == 202
    assert len(session.execute_calls) == 2
    mock_load.assert_called_once()


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_finalize_replay_denied_for_revoked_viewer(mock_load: AsyncMock) -> None:
    mock_load.return_value = IdempotencyReplay(202, {"revision_version": 2})
    session = _RecordingSession(
        [
            _scalar_result(_revision()),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(AuthorizationDeniedError):
        _run(
            finalize_package_revision(
                session,
                principal=VIEWER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                storage_root=ROOT / "tmp-storage",
                project_root=ROOT,
                limits=LIMITS,
                now=NOW,
            )
        )

    mock_load.assert_not_called()


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_replays_after_lock_and_auth_bypassing_state_checks(
    mock_load: AsyncMock,
) -> None:
    payload = {"revision_version": 4, "status": "ready"}
    mock_load.return_value = IdempotencyReplay(200, payload)
    session = _RecordingSession(
        [
            _scalar_result(_revision(status="uploading", revision_version=1)),
            _scalar_one_result(_system()),
        ]
    )

    result = _run(
        confirm_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            if_match='"v99"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert result.replayed is True
    assert result.status == 200
    assert len(session.execute_calls) == 2
    revision_sql = _compile_sql(session.execute_calls[0])
    system_sql = _compile_sql(session.execute_calls[1])
    assert "package_revisions" in revision_sql
    assert "systems" in system_sql
    assert "FOR UPDATE" in revision_sql
    assert "FOR UPDATE" in system_sql
    mock_load.assert_called_once()


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_replay_denied_for_revoked_viewer(mock_load: AsyncMock) -> None:
    mock_load.return_value = IdempotencyReplay(200, {"revision_version": 3})
    session = _RecordingSession(
        [
            _scalar_result(_revision(status="awaiting_confirmation", revision_version=2)),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(AuthorizationDeniedError):
        _run(
            confirm_package_revision(
                session,
                principal=VIEWER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    mock_load.assert_not_called()


def test_confirm_requires_if_match_before_idempotency() -> None:
    session = _RecordingSession([])

    with pytest.raises(IfMatchRequiredError):
        _run(
            confirm_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                if_match=None,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_rejects_stale_if_match_without_mutation(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision(status="awaiting_confirmation", revision_version=3)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
        ]
    )

    with pytest.raises(EtagMismatchError):
        _run(
            confirm_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.status == "awaiting_confirmation"
    assert revision.revision_version == 3


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_rejects_pending_fact_proposals(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    revision = _revision(status="awaiting_confirmation", revision_version=2)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_one_result(True),
        ]
    )

    with pytest.raises(UnconfirmedFactProposalsError):
        _run(
            confirm_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                if_match='"v2"',
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                now=NOW,
            )
        )

    assert revision.revision_version == 2


@patch("ato_service.package_revisions.record_idempotency_outcome", new_callable=AsyncMock)
@patch("ato_service.package_revisions.append_audit_event", new_callable=AsyncMock)
@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_confirm_transitions_and_increments_version_once(
    mock_load: AsyncMock,
    mock_audit: AsyncMock,
    mock_record: AsyncMock,
) -> None:
    mock_load.return_value = None
    mock_audit.return_value = MagicMock()
    mock_record.return_value = MagicMock()

    revision = _revision(status="awaiting_confirmation", revision_version=2)
    session = _RecordingSession(
        [
            _scalar_result(revision),
            _scalar_one_result(_system()),
            _scalar_one_result(False),
        ]
    )

    result = _run(
        confirm_package_revision(
            session,
            principal=OWNER_PRINCIPAL,
            package_revision_id=REVISION_ID,
            if_match='"v2"',
            idempotency_key=IDEM_KEY,
            hmac_key=HMAC_KEY,
            now=NOW,
        )
    )

    assert revision.status == "ready"
    assert revision.revision_version == 3
    assert result.status == 200
    assert result.etag == '"v3"'
    mock_audit.assert_called_once()
    mock_record.assert_called_once()


def test_list_requires_system_read_access() -> None:
    session = _RecordingSession([_scalar_result(_system()), _scalars_all_result([])])

    result = _run(
        list_package_revisions(
            session,
            principal=VIEWER_PRINCIPAL,
            system_id=SYSTEM_ID,
            limit=50,
        )
    )

    assert result.items == ()
    assert result.next_cursor is None


def test_list_raises_when_system_missing() -> None:
    session = _RecordingSession([_scalar_result(None)])

    with pytest.raises(SystemNotFoundError):
        _run(
            list_package_revisions(
                session,
                principal=OWNER_PRINCIPAL,
                system_id=SYSTEM_ID,
            )
        )


@patch("ato_service.package_revisions.load_idempotency_replay", new_callable=AsyncMock)
def test_finalize_lock_order_is_revision_then_system(mock_load: AsyncMock) -> None:
    mock_load.return_value = None
    session = _RecordingSession(
        [
            _scalar_result(_revision()),
            _scalar_one_result(_system()),
            _scalars_all_result([]),
        ]
    )

    with pytest.raises(EmptyPackageRevisionError):
        _run(
            finalize_package_revision(
                session,
                principal=OWNER_PRINCIPAL,
                package_revision_id=REVISION_ID,
                idempotency_key=IDEM_KEY,
                hmac_key=HMAC_KEY,
                storage_root=ROOT / "tmp-storage",
                project_root=ROOT,
                limits=LIMITS,
                now=NOW,
            )
        )

    revision_sql = _compile_sql(session.execute_calls[0])
    system_sql = _compile_sql(session.execute_calls[1])
    assert "package_revisions" in revision_sql
    assert "systems" in system_sql
    assert "FOR UPDATE" in revision_sql
    assert "FOR UPDATE" in system_sql
