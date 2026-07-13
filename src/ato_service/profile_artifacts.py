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
) -> GeneratedProfileArtifacts:
    """Generate draft human/machine artifact descriptors without HS-001/HS-002 claims."""
    files: list[dict[str, Any]] = [
        {
            "path": "README.txt",
            "media_type": "text/plain",
            "sha256": _sha256_text(
                "Draft export bundle. Official schema qualification remains blocked by open hard stops."
            ),
            "size_bytes": 88,
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
            "size_bytes": 64,
            "official_schema_id": None,
        },
    ]
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
    return GeneratedProfileArtifacts(files=files)


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
