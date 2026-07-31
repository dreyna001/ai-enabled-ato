"""Persistence helpers for immutable, locally supplied SSP profile bundles."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.ssp_workspace.contracts import ProfileState
from ato_service.ssp_workspace.profile_bundles import (
    ProfileBundle,
    ProfileBundleError,
    ProfileBundleFile,
    ProfileControl,
    ProfileManifest,
    ProfileSource,
    ResolvedProfile,
    SspRequiredItem,
    ControlResponsePolicy,
    StandardCoverage,
    ImplementationStatementAgentInstructions,
    ImplementationStatementAuthorityRef,
    ImplementationStatementDeterministicPolicy,
    ImplementationStatementPolicy,
    default_control_response_policy,
    default_implementation_statement_policy,
    load_profile_bundle,
    resolve_profile,
)

MAX_PROFILE_ARCHIVE_BYTES = 52_428_800


class ProfilePersistenceError(ValueError):
    """Base for deterministic profile lifecycle failures."""


class ProfileAlreadyImportedError(ProfilePersistenceError):
    error_code = "profile_already_imported"


class ProfileNotFoundError(ProfilePersistenceError):
    error_code = "resource_not_found"


def parse_profile_archive(content: bytes) -> ProfileBundle:
    """Validate a bounded ZIP bundle without retaining a temporary extraction."""

    if not content:
        raise ProfileBundleError("profile bundle must not be empty")
    if len(content) > MAX_PROFILE_ARCHIVE_BYTES:
        raise ProfileBundleError(
            f"profile bundle exceeds {MAX_PROFILE_ARCHIVE_BYTES} bytes"
        )
    with tempfile.TemporaryDirectory(prefix="ssp-profile-") as directory:
        archive = Path(directory) / "profile.zip"
        archive.write_bytes(content)
        return load_profile_bundle(archive)


def serialize_profile_bundle(bundle: ProfileBundle) -> dict[str, Any]:
    """Return a JSON-compatible immutable storage representation."""

    return asdict(bundle)


def deserialize_profile_bundle(document: dict[str, Any]) -> ProfileBundle:
    """Rebuild the validated runtime shape from a stored profile document."""

    if not isinstance(document, dict):
        raise ProfileBundleError("stored profile bundle must be an object")
    try:
        manifest = _deserialize_stored_manifest(document["manifest"])
        return ProfileBundle(
            manifest=manifest,
            catalog_controls=tuple(
                ProfileControl(**item) for item in document["catalog_controls"]
            ),
            low_control_ids=tuple(document["low_control_ids"]),
            moderate_control_ids=tuple(document["moderate_control_ids"]),
            high_control_ids=tuple(document["high_control_ids"]),
            ssp_required_items=tuple(
                SspRequiredItem(
                    item_id=item["item_id"],
                    title=item["title"],
                    value_type=item["value_type"],
                    min_length=item["min_length"],
                    allowed_values=tuple(item["allowed_values"]),
                    evidence_required_for_agent=item["evidence_required_for_agent"],
                    required=item.get("required", True),
                    standard_refs=tuple(item.get("standard_refs", ())),
                )
                for item in document["ssp_required_items"]
            ),
            control_response=_deserialize_control_response(
                document.get("control_response")
            ),
            standard_coverage=tuple(
                StandardCoverage(**entry)
                for entry in document.get("standard_coverage", ())
            ),
            implementation_statement_policy=_deserialize_implementation_statement_policy(
                document.get("implementation_statement_policy")
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileBundleError("stored profile bundle is malformed") from exc


def _deserialize_stored_manifest(raw_manifest: dict[str, Any]) -> ProfileManifest:
    sources = tuple(ProfileSource(**item) for item in raw_manifest["sources"])
    catalog_release = raw_manifest.get("nist_control_catalog_release")
    if not catalog_release:
        for source in sources:
            if source.source_id == "nist-sp-800-53":
                catalog_release = source.version
                break
    if not catalog_release:
        raise ProfileBundleError(
            "stored profile manifest is missing nist_control_catalog_release"
        )
    return ProfileManifest(
        schema_version=raw_manifest["schema_version"],
        profile_id=raw_manifest["profile_id"],
        profile_version=raw_manifest["profile_version"],
        nist_control_catalog_release=catalog_release,
        display_name=raw_manifest["display_name"],
        sources=sources,
        files=tuple(ProfileBundleFile(**item) for item in raw_manifest["files"]),
        sha256=raw_manifest["sha256"],
    )


def _deserialize_implementation_statement_policy(
    raw: dict[str, Any] | None,
) -> ImplementationStatementPolicy:
    if not isinstance(raw, dict):
        return default_implementation_statement_policy()
    deterministic = raw.get("deterministic")
    instructions = raw.get("agent_instructions")
    if not isinstance(deterministic, dict) or not isinstance(instructions, dict):
        return default_implementation_statement_policy()
    try:
        return ImplementationStatementPolicy(
            policy_version=raw["policy_version"],
            deterministic=ImplementationStatementDeterministicPolicy(
                reject_oscal_parameter_insert_syntax=deterministic[
                    "reject_oscal_parameter_insert_syntax"
                ],
                require_question_for_unresolved_parameterized_controls=deterministic[
                    "require_question_for_unresolved_parameterized_controls"
                ],
                require_evidence_for_agent_non_unknown_claims=deterministic[
                    "require_evidence_for_agent_non_unknown_claims"
                ],
                require_statement_gap_or_question_before_approval=deterministic[
                    "require_statement_gap_or_question_before_approval"
                ],
                semantic_quality_findings_are_advisory=deterministic[
                    "semantic_quality_findings_are_advisory"
                ],
            ),
            agent_instructions=ImplementationStatementAgentInstructions(
                statement_content=tuple(instructions["statement_content"]),
                organization_defined_parameters=tuple(
                    instructions["organization_defined_parameters"]
                ),
                inherited_and_hybrid_responsibility=tuple(
                    instructions["inherited_and_hybrid_responsibility"]
                ),
                semantic_review=tuple(instructions["semantic_review"]),
            ),
            authority_refs=tuple(
                ImplementationStatementAuthorityRef(
                    source_id=entry["source_id"],
                    requirement_ids=tuple(entry["requirement_ids"]),
                )
                for entry in raw.get("authority_refs", ())
            ),
        )
    except (KeyError, TypeError, ValueError):
        return default_implementation_statement_policy()


def _deserialize_control_response(
    raw: dict[str, Any] | None,
) -> ControlResponsePolicy:
    if raw is None:
        return default_control_response_policy()
    return ControlResponsePolicy(
        implementation_statuses=tuple(raw["implementation_statuses"]),
        responsibilities=tuple(raw["responsibilities"]),
        question_owner_types=tuple(raw["question_owner_types"]),
        evidence_required_for_agent_statement=raw[
            "evidence_required_for_agent_statement"
        ],
    )


async def import_profile(
    session: AsyncSession,
    *,
    bundle: ProfileBundle,
    imported_by: str,
    now: datetime,
) -> Any:
    """Persist one validated profile version as inactive."""

    from ato_service.db.models import SspProfileVersion

    actor = imported_by.strip()
    if not actor:
        raise ValueError("imported_by cannot be empty")
    existing = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_key == bundle.manifest.profile_id,
                SspProfileVersion.version == bundle.manifest.profile_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ProfileAlreadyImportedError("profile version is already imported")
    document = serialize_profile_bundle(bundle)
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    row = SspProfileVersion(
        profile_version_id=uuid.uuid4(),
        profile_key=bundle.manifest.profile_id,
        version=bundle.manifest.profile_version,
        status=ProfileState.INACTIVE.value,
        bundle_sha256=hashlib.sha256(canonical).hexdigest(),
        bundle=document,
        imported_by=actor,
        imported_at=now,
        activated_at=None,
    )
    session.add(row)
    await session.flush()
    return row


async def activate_profile(
    session: AsyncSession,
    *,
    profile_version_id: uuid.UUID,
    now: datetime,
) -> Any:
    """Atomically make one version active for its profile key."""

    from ato_service.db.models import SspProfileVersion

    target = (
        await session.execute(
            select(SspProfileVersion)
            .where(SspProfileVersion.profile_version_id == profile_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if target is None or target.status == ProfileState.ARCHIVED.value:
        raise ProfileNotFoundError("profile version not found")
    active_rows = (
        await session.execute(
            select(SspProfileVersion)
            .where(
                SspProfileVersion.profile_key == target.profile_key,
                SspProfileVersion.status == ProfileState.ACTIVE.value,
                SspProfileVersion.profile_version_id != profile_version_id,
            )
            .with_for_update()
        )
    ).scalars()
    rows_to_deactivate = list(active_rows)
    for row in rows_to_deactivate:
        row.status = ProfileState.INACTIVE.value
        row.activated_at = None
    if rows_to_deactivate:
        await session.flush()
    target.status = ProfileState.ACTIVE.value
    target.activated_at = now
    await session.flush()
    return target


async def list_profiles(session: AsyncSession) -> list[Any]:
    from ato_service.db.models import SspProfileVersion

    result = await session.execute(
        select(SspProfileVersion).order_by(
            SspProfileVersion.profile_key.asc(),
            SspProfileVersion.imported_at.desc(),
        )
    )
    return list(result.scalars())


async def ensure_builtin_profile(
    session: AsyncSession,
    *,
    project_root: Path,
    now: datetime,
) -> Any:
    """Idempotently import the repository-pinned Rev5 profile for first boot."""

    from ato_service.db.models import SspProfileVersion

    bundle = load_profile_bundle(
        project_root
        / "reference"
        / "ssp_profiles"
        / "agency-fisma-nist-sp800-53-rev5-1.2.0"
    )
    document = serialize_profile_bundle(bundle)
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    bundle_sha256 = hashlib.sha256(canonical).hexdigest()
    row = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_key == bundle.manifest.profile_id,
                SspProfileVersion.version == bundle.manifest.profile_version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = await import_profile(
            session,
            bundle=bundle,
            imported_by="system:built-in-profile",
            now=now,
        )
    elif row.bundle_sha256 != bundle_sha256:
        row.bundle = document
        row.bundle_sha256 = bundle_sha256
        await session.flush()
    active = (
        await session.execute(
            select(SspProfileVersion).where(
                SspProfileVersion.profile_key == row.profile_key,
                SspProfileVersion.status == ProfileState.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if active is None:
        row = await activate_profile(
            session,
            profile_version_id=row.profile_version_id,
            now=now,
        )
    return row


def resolve_stored_profile(row: Any, impact_level: str) -> ResolvedProfile:
    if impact_level not in {"low", "moderate", "high"}:
        raise ProfileBundleError("impact_level must be low, moderate, or high")
    return resolve_profile(
        deserialize_profile_bundle(row.bundle),
        impact_level,  # type: ignore[arg-type]
    )
