"""Profile-specific draft artifact generators within hard-stop boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ato_service.fisma_generator import (
    FISMA_EXPORT_PATHS,
    PRIVACY_SCOPE_NOTICE,
    generate_fisma_security_artifacts,
)
from ato_service.fisma_template_pack import (
    FismaTemplatePackError,
    load_template_pack_reference,
    load_verified_template_pack,
)


@dataclass(frozen=True, slots=True)
class GeneratedProfileArtifacts:
    files: list[dict[str, Any]]


def generate_profile_artifacts(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None = None,
    matrix_rows: list[dict[str, Any]] | None = None,
    runtime_config_document: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> GeneratedProfileArtifacts:
    """Generate draft human/machine artifact descriptors without HS-001/HS-002 claims."""
    del project_root
    readme_text = (
        "Draft export bundle. Official schema qualification remains blocked by open hard stops."
    )
    files: list[dict[str, Any]] = [
        {
            "path": "README.txt",
            "media_type": "text/plain",
            "sha256": _sha256_text(readme_text),
            "size_bytes": len(readme_text.encode("utf-8")),
            "official_schema_id": None,
        },
        {
            "path": "human/readiness-summary.md",
            "media_type": "text/markdown",
            "sha256": _sha256_text(_readiness_summary(profile_id=profile_id, document=sealed_document)),
            "size_bytes": len(_readiness_summary(profile_id=profile_id, document=sealed_document).encode("utf-8")),
            "official_schema_id": None,
        },
        {
            "path": "machine/package-document.json",
            "media_type": "application/json",
            "sha256": _sha256_text(json.dumps(sealed_document, sort_keys=True)),
            "size_bytes": len(json.dumps(sealed_document, sort_keys=True)),
            "official_schema_id": None,
        },
        {
            "path": "provenance/review-run.json",
            "media_type": "application/json",
            "sha256": _sha256_text(
                json.dumps(
                    {
                        "review_revision_id": str(review_revision_id).lower(),
                        "run_id": str(run_id).lower(),
                    },
                    sort_keys=True,
                )
            ),
            "size_bytes": len(
                json.dumps(
                    {
                        "review_revision_id": str(review_revision_id).lower(),
                        "run_id": str(run_id).lower(),
                    },
                    sort_keys=True,
                )
            ),
            "official_schema_id": None,
        },
    ]
    if dispositions is not None:
        disposition_bytes = json.dumps({"dispositions": dispositions}, sort_keys=True)
        files.append(
            {
                "path": "provenance/dispositions.json",
                "media_type": "application/json",
                "sha256": _sha256_text(disposition_bytes),
                "size_bytes": len(disposition_bytes.encode("utf-8")),
                "official_schema_id": None,
            }
        )
    if matrix_rows is not None:
        matrix_bytes = json.dumps({"rows": matrix_rows}, sort_keys=True)
        files.append(
            {
                "path": "machine/assessment-matrix.json",
                "media_type": "application/json",
                "sha256": _sha256_text(matrix_bytes),
                "size_bytes": len(matrix_bytes.encode("utf-8")),
                "official_schema_id": None,
            }
        )
    files.extend(
        _profile_specific_files(
            profile_id=profile_id,
            sealed_document=sealed_document,
            review_revision_id=review_revision_id,
            run_id=run_id,
            dispositions=dispositions,
            matrix_rows=matrix_rows,
            runtime_config_document=runtime_config_document,
        )
    )
    return GeneratedProfileArtifacts(files=files)


def build_profile_file_contents(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None = None,
    matrix_rows: list[dict[str, Any]] | None = None,
    runtime_config_document: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return path -> text for profile-specific export artifacts."""
    if profile_id != "fisma_agency_security":
        return {}
    template_pack = _load_optional_template_pack(runtime_config_document)
    result = generate_fisma_security_artifacts(
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        template_pack=template_pack,
    )
    return dict(result.contents)


def _profile_specific_files(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None,
    matrix_rows: list[dict[str, Any]] | None,
    runtime_config_document: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if profile_id == "fisma_agency_security":
        return _fisma_profile_files(
            sealed_document=sealed_document,
            review_revision_id=review_revision_id,
            run_id=run_id,
            dispositions=dispositions,
            matrix_rows=matrix_rows,
            runtime_config_document=runtime_config_document,
        )
    files: list[dict[str, Any]] = []
    if profile_id == "fedramp_20x_program":
        section = sealed_document.get("fedramp_20x") or {}
        files.append(
            {
                "path": "machine/fedramp-20x-draft.json",
                "media_type": "application/json",
                "sha256": _sha256_text(json.dumps(section, sort_keys=True)),
                "size_bytes": len(json.dumps(section, sort_keys=True)),
                "official_schema_id": None,
            }
        )
    if profile_id == "fedramp_rev5_transition":
        section = sealed_document.get("fedramp_rev5_transition") or {}
        files.append(
            {
                "path": "machine/fedramp-rev5-transition-draft.json",
                "media_type": "application/json",
                "sha256": _sha256_text(json.dumps(section, sort_keys=True)),
                "size_bytes": len(json.dumps(section, sort_keys=True)),
                "official_schema_id": None,
            }
        )
    return files


def _fisma_profile_files(
    *,
    sealed_document: dict[str, Any],
    review_revision_id: Any,
    run_id: Any,
    dispositions: list[dict[str, Any]] | None,
    matrix_rows: list[dict[str, Any]] | None,
    runtime_config_document: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    contents = build_profile_file_contents(
        profile_id="fisma_agency_security",
        sealed_document=sealed_document,
        review_revision_id=review_revision_id,
        run_id=run_id,
        dispositions=dispositions,
        matrix_rows=matrix_rows,
        runtime_config_document=runtime_config_document,
    )
    files: list[dict[str, Any]] = []
    for path in FISMA_EXPORT_PATHS:
        payload = contents[path]
        files.append(
            {
                "path": path,
                "media_type": _media_type_for_path(path),
                "sha256": _sha256_text(payload),
                "size_bytes": len(payload.encode("utf-8")),
                "official_schema_id": None,
            }
        )
    return files


def _load_optional_template_pack(runtime_config_document: dict[str, Any] | None):
    reference = load_template_pack_reference(runtime_config_document)
    if reference is None:
        return None
    try:
        return load_verified_template_pack(reference)
    except FismaTemplatePackError:
        return None


def _readiness_summary(*, profile_id: str, document: dict[str, Any]) -> str:
    if profile_id == "fisma_agency_security":
        controls = document.get("security_controls")
        control_count = len(controls) if isinstance(controls, dict) else 0
        return (
            "# Draft readiness summary\n\n"
            f"Profile: {profile_id}\n"
            f"Security controls present: {control_count}\n"
            f"Privacy scope notice: {PRIVACY_SCOPE_NOTICE}\n"
            "Agency field parity claimed: false\n"
        )
    privacy = document.get("privacy", {})
    assessor_count = len(document.get("assessor_inputs") or {})
    return (
        f"# Draft readiness summary\n\n"
        f"Profile: {profile_id}\n"
        f"Assessor imports: {assessor_count}\n"
        f"Privacy artifacts present: {privacy.get('artifacts_present', False)}\n"
        f"Scope notice: {privacy.get('scope_notice', '')}\n"
    )


def _media_type_for_path(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".json"):
        return "application/json"
    return "text/plain"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
