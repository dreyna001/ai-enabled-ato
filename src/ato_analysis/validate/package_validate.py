"""Deterministic validation for canonical evidence packages."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from ato_analysis.config import Settings
from ato_analysis.models.package_schema import PackageModel

_SENSITIVE_RAW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bCUI\b", re.IGNORECASE),
    re.compile(r"controlled unclassified information", re.IGNORECASE),
    re.compile(r"\bclassified\b", re.IGNORECASE),
    re.compile(r"\b(top secret|secret)\b", re.IGNORECASE),
    re.compile(r"customer[- ]sensitive", re.IGNORECASE),
    re.compile(r"\bproduction data\b", re.IGNORECASE),
)

_SENSITIVE_CLASSIFICATION_VALUES: frozenset[str] = frozenset(
    {
        "cui",
        "controlled unclassified information",
        "classified",
        "secret",
        "top secret",
        "customer-sensitive",
        "customer sensitive",
        "production",
        "production data",
    }
)


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    package: PackageModel | None
    stale_evidence_ids: list[str] = field(default_factory=list)


def validate_package(data: dict[str, Any], *, expected_package_id: str) -> ValidationResult:
    """Run all deterministic package checks after normalize."""
    errors: list[str] = []
    warnings: list[str] = []
    package: PackageModel | None = None
    stale_evidence_ids: list[str] = []

    try:
        package = PackageModel.model_validate(data)
    except ValidationError as exc:
        for issue in exc.errors():
            loc = ".".join(str(part) for part in issue.get("loc", ()))
            msg = issue.get("msg", "validation error")
            prefix = f"{loc}: " if loc else ""
            errors.append(f"{prefix}{msg}")
        return ValidationResult(
            valid=False,
            errors=errors,
            warnings=warnings,
            package=None,
            stale_evidence_ids=stale_evidence_ids,
        )

    if package.package_id != expected_package_id:
        errors.append(
            f"package_id {package.package_id!r} does not match expected "
            f"{expected_package_id!r} (filename stem)"
        )

    if package.authorization_path != "fisma_agency":
        errors.append(
            f"authorization_path must be 'fisma_agency'; got {package.authorization_path!r}"
        )

    errors.extend(_check_duplicate_ids([c.control_id for c in package.controls], "control_id"))
    errors.extend(
        _check_duplicate_ids([e.evidence_id for e in package.evidence_items], "evidence_id")
    )

    evidence_ids = {item.evidence_id for item in package.evidence_items}
    for control in package.controls:
        for evidence_id in control.linked_evidence_ids:
            if evidence_id not in evidence_ids:
                errors.append(
                    f"Control {control.control_id} links to unknown evidence_id: {evidence_id!r}"
                )

    linked_ids: set[str] = set()
    for control in package.controls:
        linked_ids.update(control.linked_evidence_ids)

    for item in package.evidence_items:
        if item.evidence_id not in linked_ids:
            warnings.append(
                f"Orphan evidence (not linked to any control): {item.evidence_id!r}"
            )

    stale_evidence_ids = detect_stale_evidence(package)
    for evidence_id in stale_evidence_ids:
        warnings.append(f"Stale evidence (collected_at exceeds freshness threshold): {evidence_id!r}")

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        package=package if not errors else None,
        stale_evidence_ids=stale_evidence_ids,
    )


def detect_stale_evidence(package: PackageModel) -> list[str]:
    """Return evidence IDs whose collected_at is older than the freshness threshold."""
    cutoff = package.assessment_date - timedelta(days=package.freshness_threshold_days)
    return [
        item.evidence_id
        for item in package.evidence_items
        if item.collected_at < cutoff
    ]


def check_sensitive_content(
    raw_text: str,
    package: PackageModel | None,
    settings: Settings,
) -> list[str]:
    """Return errors when sensitive indicators are present and OpenAI use is restricted."""
    if settings.allow_sensitive_openai:
        return []

    errors: list[str] = []

    for pattern in _SENSITIVE_RAW_PATTERNS:
        if pattern.search(raw_text):
            errors.append(
                "Sensitive content indicator found in raw input; "
                "set ALLOW_SENSITIVE_OPENAI=true to proceed with OpenAI"
            )
            break

    if package is not None:
        classification = package.data_classification.strip().lower()
        if classification in _SENSITIVE_CLASSIFICATION_VALUES:
            errors.append(
                f"data_classification {package.data_classification!r} is not allowed "
                "when ALLOW_SENSITIVE_OPENAI=false"
            )

    return errors


def _check_duplicate_ids(ids: list[str], label: str) -> list[str]:
    counts = Counter(ids)
    return [f"Duplicate {label}: {dup!r}" for dup, count in counts.items() if count > 1]
