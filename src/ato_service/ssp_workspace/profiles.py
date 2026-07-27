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
        raw_manifest = document["manifest"]
        manifest = ProfileManifest(
            schema_version=raw_manifest["schema_version"],
            profile_id=raw_manifest["profile_id"],
            profile_version=raw_manifest["profile_version"],
            display_name=raw_manifest["display_name"],
            sources=tuple(ProfileSource(**item) for item in raw_manifest["sources"]),
            files=tuple(ProfileBundleFile(**item) for item in raw_manifest["files"]),
            sha256=raw_manifest["sha256"],
        )
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
                    evidence_required_for_agent=item[
                        "evidence_required_for_agent"
                    ],
                )
                for item in document["ssp_required_items"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProfileBundleError("stored profile bundle is malformed") from exc


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
    for row in active_rows:
        row.status = ProfileState.INACTIVE.value
        row.activated_at = None
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
        / "agency-fisma-nist-sp800-53-rev5-5.2.0-1"
    )
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
