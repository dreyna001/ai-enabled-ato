"""Sanitized export ZIP assembly within hard-stop boundaries."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from ato_service.profile_artifacts import GeneratedProfileArtifacts, generate_profile_artifacts

AI_DISCLOSURE = (
    "AI Disclosure: This report was produced with machine assistance. All findings,\n"
    "summaries, and status labels are draft inference bound to the evidence provided\n"
    "in the package. They do not constitute an official compliance determination,\n"
    "risk acceptance, certification, or authorization decision. A qualified human\n"
    "reviewer must review and approve the content before use in an authoritative\n"
    "government or customer process."
)

_ALLOWED_EXPORT_PATH = re.compile(
    r"^(?:README\.txt|"
    r"(?:human|machine|provenance|validation)/"
    r"(?!\.\\.?/)(?!.*(?:/\\.\\.?/|/\\.\\.?$))(?!.*//)"
    r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,502}[A-Za-z0-9])?)$"
)


@dataclass(frozen=True, slots=True)
class AssembledExportBundle:
    """In-memory export bundle ready for durable storage."""

    export_id: str
    manifest: dict[str, Any]
    zip_bytes: bytes
    storage_key: str


class ExportAssemblyError(ValueError):
    def __init__(self, message: str, *, error_code: str = "request_schema_invalid") -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


def manifest_sha256(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_draft_manifest(
    *,
    profile_id: str,
    package_revision_id: str,
    run_id: str,
    review_revision_id: str,
    authority_manifest_id: str,
    artifacts: GeneratedProfileArtifacts,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "profile_id": profile_id,
        "package_revision_id": package_revision_id,
        "run_id": run_id,
        "review_revision_id": review_revision_id,
        "authority_manifest_id": authority_manifest_id,
        "files": artifacts.files,
    }


def build_export_file_contents(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
) -> dict[str, bytes]:
    """Return path -> bytes for every allowlisted export artifact."""
    artifacts = generate_profile_artifacts(
        profile_id=profile_id,
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
    )
    contents: dict[str, bytes] = {}
    for descriptor in artifacts.files:
        path = descriptor["path"]
        _assert_allowed_path(path)
        if path == "README.txt":
            payload = (
                "Draft export bundle. Official schema qualification remains blocked "
                "by open hard stops."
            )
        elif path == "human/readiness-summary.md":
            from ato_service.profile_artifacts import _readiness_summary

            payload = _readiness_summary(profile_id=profile_id, document=sealed_document)
        elif path == "machine/package-document.json":
            payload = json.dumps(sealed_document, sort_keys=True)
        elif path == "provenance/review-run.json":
            payload = json.dumps(
                {
                    "review_revision_id": str(review_revision_id).lower(),
                    "run_id": str(run_id).lower(),
                },
                sort_keys=True,
            )
        elif path == "provenance/dispositions.json":
            payload = json.dumps({"dispositions": dispositions}, sort_keys=True)
        elif path == "machine/assessment-matrix.json":
            payload = json.dumps({"rows": matrix_rows}, sort_keys=True)
        elif path.startswith("machine/") and path.endswith(".json"):
            section_key = {
                "machine/fedramp-20x-draft.json": "fedramp_20x",
                "machine/fedramp-rev5-transition-draft.json": "fedramp_rev5_transition",
                "machine/fisma-agency-security-draft.json": "fisma_agency_security",
            }.get(path)
            section = sealed_document.get(section_key) if section_key else None
            payload = json.dumps(section or {}, sort_keys=True)
        else:
            raise ExportAssemblyError(f"unsupported export artifact path: {path}")
        contents[path] = payload.encode("utf-8")
    return contents


def assemble_export_bundle(
    *,
    export_id: str,
    profile_id: str,
    system_id: str,
    package_revision_id: str,
    run_id: str,
    review_revision_id: str,
    approval_id: str,
    authority_manifest_id: str,
    created_at: str,
    sealed_document: dict[str, Any],
    dispositions: list[dict[str, Any]],
    matrix_rows: list[dict[str, Any]],
    expected_payload_manifest_sha256: str,
) -> AssembledExportBundle:
    """Build a sanitized ZIP and verify the approved payload manifest hash."""
    artifacts = generate_profile_artifacts(
        profile_id=profile_id,
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
    )
    draft_manifest = build_draft_manifest(
        profile_id=profile_id,
        package_revision_id=package_revision_id,
        run_id=run_id,
        review_revision_id=review_revision_id,
        authority_manifest_id=authority_manifest_id,
        artifacts=artifacts,
    )
    actual_hash = manifest_sha256(draft_manifest)
    if actual_hash != expected_payload_manifest_sha256:
        raise ExportAssemblyError(
            "approved payload manifest no longer matches sealed content",
            error_code="approval_payload_mismatch",
        )

    file_contents = build_export_file_contents(
        profile_id=profile_id,
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
    )
    files: list[dict[str, Any]] = []
    for path, payload in sorted(file_contents.items()):
        _assert_allowed_path(path)
        files.append(
            {
                "path": path,
                "media_type": _media_type_for_path(path),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "official_schema_id": None,
            }
        )

    manifest = {
        "schema_version": "1.0.0",
        "export_id": export_id,
        "profile_id": profile_id,
        "system_id": system_id,
        "package_revision_id": package_revision_id,
        "run_id": run_id,
        "review_revision_id": review_revision_id,
        "approval_id": approval_id,
        "created_at": created_at,
        "ai_disclosure": AI_DISCLOSURE,
        "authority_manifest_id": authority_manifest_id,
        "files": files,
    }
    zip_bytes = _build_zip(manifest=manifest, file_contents=file_contents)
    storage_key = f"exports/{export_id}.zip"
    return AssembledExportBundle(
        export_id=export_id,
        manifest=manifest,
        zip_bytes=zip_bytes,
        storage_key=storage_key,
    )


def _media_type_for_path(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".json"):
        return "application/json"
    return "text/plain"


def _assert_allowed_path(path: str) -> None:
    if not _ALLOWED_EXPORT_PATH.fullmatch(path):
        raise ExportAssemblyError(
            f"export path is not allowlisted: {path}",
            error_code="request_schema_invalid",
        )


def _build_zip(*, manifest: dict[str, Any], file_contents: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(file_contents):
            archive.writestr(path, file_contents[path])
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
    return buffer.getvalue()
