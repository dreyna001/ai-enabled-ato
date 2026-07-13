"""Profile-specific draft artifact generators within hard-stop boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


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
) -> GeneratedProfileArtifacts:
    """Generate draft human/machine artifact descriptors without HS-001/HS-002 claims."""
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
        _profile_specific_files(profile_id=profile_id, sealed_document=sealed_document)
    )
    return GeneratedProfileArtifacts(files=files)


def _profile_specific_files(
    *,
    profile_id: str,
    sealed_document: dict[str, Any],
) -> list[dict[str, Any]]:
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
    if profile_id == "fisma_agency_security":
        section = sealed_document.get("fisma_agency_security") or {}
        files.append(
            {
                "path": "machine/fisma-agency-security-draft.json",
                "media_type": "application/json",
                "sha256": _sha256_text(json.dumps(section, sort_keys=True)),
                "size_bytes": len(json.dumps(section, sort_keys=True)),
                "official_schema_id": None,
            }
        )
    return files


def _readiness_summary(*, profile_id: str, document: dict[str, Any]) -> str:
    privacy = document.get("privacy", {})
    assessor_count = len(document.get("assessor_inputs") or {})
    return (
        f"# Draft readiness summary\n\n"
        f"Profile: {profile_id}\n"
        f"Assessor imports: {assessor_count}\n"
        f"Privacy artifacts present: {privacy.get('artifacts_present', False)}\n"
        f"Scope notice: {privacy.get('scope_notice', '')}\n"
    )


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
