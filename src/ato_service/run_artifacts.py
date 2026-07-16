"""Read-only listing of durable run artifact metadata from artifact manifests."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.analysis_runs import AnalysisRunNotFoundError
from ato_service.artifact_manifests import (
    ArtifactManifestError,
    ArtifactManifestValidationError,
    load_run_artifact_manifest,
)
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.db.models import AnalysisRun, PackageRevision, System
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    IllegalStateTransitionError,
)
from ato_service.package_rbac import require_package_role
from ato_service.pagination import (
    InvalidPaginationCursorError,
    validate_page_limit,
)
from ato_service.route_role_matrix import ROLE_VIEWER

_ARTIFACT_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2048
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ARTIFACT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_OPENAPI_PATH_PATTERN = re.compile(
    r"^(human|machine|provenance|validation)/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,127}){0,7}$"
)


def _load_run_with_system_statement(run_id: uuid.UUID) -> Any:
    return (
        select(AnalysisRun, PackageRevision, System)
        .join(
            PackageRevision,
            PackageRevision.package_revision_id == AnalysisRun.package_revision_id,
        )
        .join(System, System.system_id == PackageRevision.system_id)
        .where(AnalysisRun.run_id == run_id)
    )


class RunArtifactManifestMissingError(Exception):
    """Raised when a succeeded run has no durable artifact manifest on disk."""

    error_code = "artifact_manifest_missing"


class RunArtifactDigestMismatchError(Exception):
    """Raised when the stored manifest digest does not match the run record."""

    error_code = "artifact_digest_mismatch"


class RunArtifactDescriptorError(Exception):
    """Raised when manifest file metadata cannot be mapped to the API contract."""

    error_code = "state_artifact_inconsistent"


@dataclass(frozen=True, slots=True)
class RunArtifactsPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


def media_type_for_generated_path(path: str) -> str:
    """Return the contract media type for one generated bundle path."""
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".json"):
        return "application/json"
    return "text/plain"


def artifact_id_for_generated_path(path: str) -> str:
    """Derive a stable allowlisted artifact id from one generated bundle path."""
    basename = path.rsplit("/", maxsplit=1)[-1]
    stem = basename.rsplit(".", maxsplit=1)[0] if "." in basename else basename
    artifact_id = stem.lower()
    if _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None:
        raise RunArtifactDescriptorError(
            f"generated path does not map to a valid artifact id: {path}"
        )
    return artifact_id


def map_manifest_file_to_descriptor(file_entry: dict[str, Any]) -> dict[str, Any]:
    """Map one manifest file record to an OpenAPI ArtifactDescriptor."""
    path = file_entry.get("path")
    sha256 = file_entry.get("sha256")
    size_bytes = file_entry.get("size_bytes")
    if not isinstance(path, str) or _OPENAPI_PATH_PATTERN.fullmatch(path) is None:
        raise RunArtifactDescriptorError("generated file path is invalid")
    if not isinstance(sha256, str):
        raise RunArtifactDescriptorError("generated file sha256 is invalid")
    if not isinstance(size_bytes, int) or size_bytes < 1:
        raise RunArtifactDescriptorError("generated file size_bytes is invalid")

    return {
        "artifact_id": artifact_id_for_generated_path(path),
        "path": path,
        "media_type": media_type_for_generated_path(path),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "official_schema_id": None,
    }


def paginate_manifest_files(
    files: list[dict[str, Any]],
    *,
    cursor: str | None,
    limit: int | None,
) -> RunArtifactsPage:
    """Return one deterministic page of manifest file descriptors."""
    page_limit = validate_page_limit(limit)
    descriptors = [map_manifest_file_to_descriptor(item) for item in files]
    descriptors.sort(key=lambda item: item["path"])

    decoded_path: str | None = None
    if cursor is not None:
        decoded_path = _decode_artifact_cursor(cursor)

    if decoded_path is not None:
        descriptors = [item for item in descriptors if item["path"] > decoded_path]

    page_items = descriptors[: page_limit + 1]
    next_cursor = None
    if len(page_items) > page_limit:
        last = page_items[page_limit - 1]
        next_cursor = _encode_artifact_cursor(path=last["path"])
        page_items = page_items[:page_limit]

    return RunArtifactsPage(items=page_items, next_cursor=next_cursor)


async def list_run_artifacts(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    run_id: uuid.UUID,
    cursor: str | None,
    limit: int | None,
    storage_root: Path,
    project_root: Path | None = None,
) -> RunArtifactsPage:
    """Return paginated artifact metadata for one succeeded analysis run."""
    result = await session.execute(_load_run_with_system_statement(run_id))
    row = result.one_or_none()
    if row is None:
        raise AnalysisRunNotFoundError(run_id=run_id)
    analysis_run, package_revision, system = row
    require_package_role(principal, system=system, revision=package_revision, role=ROLE_VIEWER)
    if AnalysisRunStatus(analysis_run.status) is not AnalysisRunStatus.SUCCEEDED:
        raise IllegalStateTransitionError(
            error_code="illegal_state_transition",
            current_state=analysis_run.status,
            target_state="artifacts_available",
        )

    expected_sha256 = analysis_run.artifact_manifest_sha256
    if expected_sha256 is None:
        raise RunArtifactManifestMissingError()

    try:
        document = load_run_artifact_manifest(
            storage_root=storage_root,
            run_id=str(run_id).lower(),
            expected_sha256=expected_sha256,
            project_root=project_root,
        )
    except ArtifactManifestValidationError as exc:
        message = str(exc)
        if "digest mismatch" in message:
            raise RunArtifactDigestMismatchError() from exc
        raise RunArtifactDescriptorError(message) from exc
    except ArtifactManifestError as exc:
        raise RunArtifactManifestMissingError() from exc

    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise RunArtifactDescriptorError("artifact manifest files are invalid")

    return paginate_manifest_files(files, cursor=cursor, limit=limit)


def _encode_artifact_cursor(*, path: str) -> str:
    payload = {"v": _ARTIFACT_CURSOR_VERSION, "p": path}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    if len(encoded) > _MAX_CURSOR_LENGTH:
        raise InvalidPaginationCursorError()
    return encoded


def _decode_artifact_cursor(cursor: str) -> str:
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH or not _CURSOR_PATTERN.fullmatch(cursor):
        raise InvalidPaginationCursorError()
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidPaginationCursorError() from exc
    if payload.get("v") != _ARTIFACT_CURSOR_VERSION:
        raise InvalidPaginationCursorError()
    path = payload.get("p")
    if not isinstance(path, str) or _OPENAPI_PATH_PATTERN.fullmatch(path) is None:
        raise InvalidPaginationCursorError()
    return path
