"""Package editor draft read/write and sealing helpers for Component A Diff 5."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import append_audit_event
from ato_service.authorization_boundary import (
    UnsupportedAuthorizationPathError,
    validate_system_context_authorization_path,
)
from ato_service.auth_context import (
    AuthenticatedPrincipal,
    require_system_mutation_access,
    require_system_read_access,
)
from ato_service.concurrency import (
    IfMatchRequiredError,
    assert_if_match,
    format_package_revision_etag,
)
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionDraft,
    SealedPackageContent,
    System,
    SystemContextSnapshot,
)
from ato_service.domain_mapping import (
    format_uuid,
    map_package_revision_draft_to_domain,
)
from ato_service.draft_builder import validate_package_draft_document
from ato_service.legacy_proposal_compat import ensure_legacy_draft_for_read
from ato_service.idempotency import (
    IdempotencyReplay,
    canonical_json_bytes,
    load_idempotency_replay,
    record_idempotency_outcome,
    replay_etag_from_outcome,
    request_digest_from_payload,
)
from ato_service.lifecycle_transitions import (
    IllegalStateTransitionError,
    PackageRevisionStatus,
    PackageRevisionTransitionCondition,
)


def _validation_error(message: str, *, error_code: str = "request_schema_invalid") -> Exception:
    from ato_service.package_revisions import PackageRevisionValidationError

    return PackageRevisionValidationError(message, error_code=error_code)


def _revision_not_found(*, package_revision_id: uuid.UUID) -> Exception:
    from ato_service.package_revisions import PackageRevisionNotFoundError

    return PackageRevisionNotFoundError(package_revision_id=package_revision_id)

OPERATION_SAVE_DRAFT = "package_revisions.save_draft"

HTTP_OK = 200


class PackageRevisionDraftNotFoundError(Exception):
    """Raised when a package revision draft cannot be loaded for the caller."""

    error_code = "resource_not_found"

    def __init__(self, *, package_revision_id: uuid.UUID) -> None:
        self.package_revision_id = package_revision_id
        super().__init__("requested resource was not found")


@dataclass(frozen=True, slots=True)
class PackageRevisionDraftViewResult:
    """Draft read outcome including the parent revision ETag."""

    payload: dict[str, Any]
    etag: str


@dataclass(frozen=True, slots=True)
class SavePackageRevisionDraftResult:
    """Mutation outcome for replay-safe draft save operations."""

    payload: dict[str, Any]
    status: int
    etag: str
    replayed: bool


def compute_sealed_document_digest(document: dict[str, Any]) -> str:
    """Return the canonical SHA-256 digest for one sealed package document."""
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def save_draft_request_digest(
    *,
    package_revision_id: uuid.UUID,
    if_match: str,
    document: dict[str, Any],
) -> str:
    """Return the normalized request digest for draft save."""
    return request_digest_from_payload(
        {
            "package_revision_id": format_uuid(package_revision_id),
            "if_match": if_match,
            "document": document,
        }
    )


def build_system_context_document(
    *,
    draft_document: dict[str, Any],
    system: System,
    revision: PackageRevision,
) -> dict[str, Any]:
    """Build one SystemContextDocument from a sealed draft and system metadata."""
    system_section = draft_document.get("system")
    if not isinstance(system_section, dict):
        raise _validation_error(
            "draft system section is required to seal package content",
        )

    control_set = draft_document.get("control_set")
    control_source: dict[str, Any] = {}
    if isinstance(control_set, dict) and isinstance(control_set.get("source"), dict):
        control_source = dict(control_set["source"])

    impact_level = system_section.get("impact_level")
    if impact_level is None:
        impact_level = revision.impact_level
    if impact_level is None:
        raise _validation_error(
            "system impact_level is required to seal package content",
        )

    return {
        "display_name": system_section.get("display_name") or system.display_name,
        "external_system_id": system.external_system_id,
        "mission_summary": system_section.get("mission_summary", ""),
        "authorization_boundary": system_section.get("authorization_boundary", ""),
        "environments": _copy_string_list(system_section.get("environments")),
        "hosting_locations": _copy_string_list(system_section.get("hosting_locations")),
        "major_components": _copy_string_list(system_section.get("major_components")),
        "external_dependencies": _copy_string_list(
            system_section.get("external_dependencies")
        ),
        "information_types": _copy_string_list(system_section.get("information_types")),
        "fips_199_rationale": system_section.get("fips_199_rationale", ""),
        "impact_level": impact_level,
        "authorization_path": system_section.get("authorization_path", ""),
        "approved_control_set_reference": control_source,
    }


def validate_system_context_document(document: dict[str, Any]) -> None:
    """Validate one system-context document against the published domain schema."""
    errors = sorted(
        _system_context_validator().iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise _validation_error(_format_validation_error(errors[0]))


def validate_draft_profile_match(
    *,
    document: dict[str, Any],
    profile_id: str,
) -> None:
    """Ensure the draft document profile matches the owning revision."""
    package = document.get("package")
    if not isinstance(package, dict):
        raise _validation_error("draft package section is required")
    draft_profile = package.get("profile_id")
    if draft_profile != profile_id:
        raise _validation_error(
            "draft profile_id does not match package revision profile",
        )


def _copy_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _validation_error(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _load_package_revision_for_update_statement(
    package_revision_id: uuid.UUID,
) -> Any:
    return (
        select(PackageRevision)
        .where(PackageRevision.package_revision_id == package_revision_id)
        .with_for_update()
    )


def _load_package_revision_with_system_statement(
    package_revision_id: uuid.UUID,
) -> Any:
    return (
        select(PackageRevision, System)
        .join(System, System.system_id == PackageRevision.system_id)
        .where(PackageRevision.package_revision_id == package_revision_id)
    )


def _load_draft_statement(package_revision_id: uuid.UUID) -> Any:
    return select(PackageRevisionDraft).where(
        PackageRevisionDraft.package_revision_id == package_revision_id
    )


def _load_draft_for_update_statement(package_revision_id: uuid.UUID) -> Any:
    return _load_draft_statement(package_revision_id).with_for_update()


def _load_system_for_update_statement(system_id: uuid.UUID) -> Any:
    from ato_service.db.models import System as SystemModel

    return select(SystemModel).where(SystemModel.system_id == system_id).with_for_update()


def _load_next_system_context_version_statement(system_id: uuid.UUID) -> Any:
    return select(func.coalesce(func.max(SystemContextSnapshot.version), 0)).where(
        SystemContextSnapshot.system_id == system_id
    )


def _draft_view_payload(
    draft: PackageRevisionDraft,
    *,
    revision_version: int,
) -> dict[str, Any]:
    payload = map_package_revision_draft_to_domain(draft)
    payload["revision_version"] = revision_version
    return payload


def _save_result_from_domain(
    payload: dict[str, Any],
    *,
    replayed: bool,
) -> SavePackageRevisionDraftResult:
    revision_version = payload["revision_version"]
    return SavePackageRevisionDraftResult(
        payload=payload,
        status=HTTP_OK,
        etag=format_package_revision_etag(revision_version),
        replayed=replayed,
    )


def _save_result_from_replay(replay: IdempotencyReplay) -> SavePackageRevisionDraftResult:
    payload = dict(replay.response_body)
    etag = replay_etag_from_outcome(
        response_body=replay.response_body,
        response_headers=replay.response_headers,
    )
    if etag is None:
        etag = format_package_revision_etag(payload["revision_version"])
    return SavePackageRevisionDraftResult(
        payload=payload,
        status=replay.response_status,
        etag=etag,
        replayed=True,
    )


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"{path}: {error.message}"
    return error.message


@cache
def _system_context_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "contracts" / "domain.schema.json"


@cache
def _system_context_validator() -> Draft202012Validator:
    domain_schema = json.loads(_system_context_schema_path().read_text(encoding="utf-8"))
    system_context_schema = domain_schema["$defs"]["SystemContextDocument"]
    validator = Draft202012Validator(system_context_schema)
    validator.check_schema(system_context_schema)
    return validator


async def get_package_revision_draft(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    hmac_key: bytes | None = None,
) -> PackageRevisionDraftViewResult:
    """Load one package revision draft after read authorization."""
    result = await session.execute(
        _load_package_revision_with_system_statement(package_revision_id)
    )
    row = result.one_or_none()
    if row is None:
        raise _revision_not_found(package_revision_id=package_revision_id)

    package_revision, system = row
    require_system_read_access(principal, system)

    draft_result = await session.execute(_load_draft_statement(package_revision_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        draft = await ensure_legacy_draft_for_read(
            session,
            revision=package_revision,
            system=system,
            hmac_key=hmac_key,
        )
    if draft is None:
        raise PackageRevisionDraftNotFoundError(package_revision_id=package_revision_id)

    revision_version = package_revision.revision_version
    return PackageRevisionDraftViewResult(
        payload=_draft_view_payload(draft, revision_version=revision_version),
        etag=format_package_revision_etag(revision_version),
    )


async def save_package_revision_draft(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    package_revision_id: uuid.UUID,
    document: dict[str, Any],
    if_match: str | None,
    idempotency_key: str,
    hmac_key: bytes,
    now: datetime,
) -> SavePackageRevisionDraftResult:
    """Persist one edited draft document without committing the caller transaction."""
    validated_now = _require_aware_utc(now, field_name="now")
    if if_match is None:
        raise IfMatchRequiredError()

    request_digest = save_draft_request_digest(
        package_revision_id=package_revision_id,
        if_match=if_match,
        document=document,
    )

    revision_result = await session.execute(
        _load_package_revision_for_update_statement(package_revision_id)
    )
    package_revision = revision_result.scalar_one_or_none()
    if package_revision is None:
        raise _revision_not_found(package_revision_id=package_revision_id)

    system_result = await session.execute(
        _load_system_for_update_statement(package_revision.system_id)
    )
    system = system_result.scalar_one()
    require_system_mutation_access(principal, system)

    replay = await load_idempotency_replay(
        session,
        principal.actor_id,
        OPERATION_SAVE_DRAFT,
        idempotency_key,
        request_digest,
        validated_now,
    )
    if replay is not None:
        return _save_result_from_replay(replay)

    current_status = PackageRevisionStatus(package_revision.status)
    if current_status is not PackageRevisionStatus.AWAITING_CONFIRMATION:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=package_revision.status,
            target_state=PackageRevisionStatus.AWAITING_CONFIRMATION.value,
            condition=PackageRevisionTransitionCondition.NORMAL_PROGRESSION.value,
        )

    draft_result = await session.execute(
        _load_draft_for_update_statement(package_revision_id)
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise PackageRevisionDraftNotFoundError(package_revision_id=package_revision_id)

    assert_if_match(if_match, package_revision.revision_version)

    validate_package_draft_document(document)
    validate_draft_profile_match(document=document, profile_id=package_revision.profile_id)
    try:
        validate_system_context_authorization_path(document)
    except UnsupportedAuthorizationPathError as exc:
        raise _validation_error(
            "authorization path is outside product scope",
            error_code=exc.error_code,
        ) from exc

    draft.document = document
    draft.updated_by = principal.actor_id
    draft.updated_at = validated_now
    package_revision.revision_version += 1

    payload = _draft_view_payload(
        draft,
        revision_version=package_revision.revision_version,
    )
    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="user",
        actor_id=principal.actor_id,
        action="package_revision.draft_saved",
        object_type="package_revision",
        object_id=format_uuid(package_revision_id),
        outcome="succeeded",
        reason_code=None,
        metadata={"revision_version": package_revision.revision_version},
        occurred_at=validated_now,
    )
    await record_idempotency_outcome(
        session,
        principal=principal.actor_id,
        operation=OPERATION_SAVE_DRAFT,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        response_status=HTTP_OK,
        response_body=payload,
        response_headers={
            "ETag": format_package_revision_etag(package_revision.revision_version)
        },
        now=validated_now,
    )
    return _save_result_from_domain(payload, replayed=False)


async def seal_package_revision_draft(
    session: AsyncSession,
    *,
    package_revision: PackageRevision,
    system: System,
    draft: PackageRevisionDraft,
    principal: AuthenticatedPrincipal,
    now: datetime,
) -> tuple[str, uuid.UUID]:
    """Seal the current draft into immutable content and a system-context snapshot."""
    validate_package_draft_document(draft.document)
    validate_draft_profile_match(
        document=draft.document,
        profile_id=package_revision.profile_id,
    )

    content_sha256 = compute_sealed_document_digest(draft.document)
    system_context_document = build_system_context_document(
        draft_document=draft.document,
        system=system,
        revision=package_revision,
    )
    validate_system_context_document(system_context_document)
    system_context_sha256 = compute_sealed_document_digest(system_context_document)

    version_result = await session.execute(
        _load_next_system_context_version_statement(system.system_id)
    )
    next_version = int(version_result.scalar_one()) + 1
    snapshot_id = uuid.uuid4()
    session.add(
        SystemContextSnapshot(
            system_context_snapshot_id=snapshot_id,
            system_id=system.system_id,
            version=next_version,
            content_sha256=system_context_sha256,
            document=system_context_document,
            created_by=principal.actor_id,
            created_at=now,
        )
    )
    session.add(
        SealedPackageContent(
            package_revision_id=package_revision.package_revision_id,
            document_schema_version=draft.document_schema_version,
            document=dict(draft.document),
            field_provenance=dict(draft.field_provenance),
            content_sha256=content_sha256,
            system_context_snapshot_id=snapshot_id,
            sealed_by=principal.actor_id,
            sealed_at=now,
        )
    )
    package_revision.package_content_sha256 = content_sha256
    package_revision.system_context_snapshot_id = snapshot_id
    return content_sha256, snapshot_id


async def draft_exists(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
) -> bool:
    """Return whether the revision has a persisted package editor draft row."""
    result = await session.execute(_load_draft_statement(package_revision_id))
    return result.scalar_one_or_none() is not None


async def load_draft_for_confirm(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
) -> PackageRevisionDraft | None:
    """Load the draft row participating in package-level confirm."""
    result = await session.execute(_load_draft_statement(package_revision_id))
    draft = result.scalar_one_or_none()
    return draft


__all__ = [
    "OPERATION_SAVE_DRAFT",
    "PackageRevisionDraftNotFoundError",
    "PackageRevisionDraftViewResult",
    "SavePackageRevisionDraftResult",
    "build_system_context_document",
    "compute_sealed_document_digest",
    "draft_exists",
    "get_package_revision_draft",
    "load_draft_for_confirm",
    "save_draft_request_digest",
    "save_package_revision_draft",
    "seal_package_revision_draft",
    "validate_draft_profile_match",
    "validate_system_context_document",
]
