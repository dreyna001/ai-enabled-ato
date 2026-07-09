"""Pre-flight readiness scoring before LLM matrix analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from ato_analysis.config import Settings
from ato_analysis.models.package_schema import PackageModel

_PREFLIGHT_WEIGHT = 0.25


@dataclass(slots=True)
class PreflightOutcome:
    score: float
    blocked: bool
    metadata_complete: bool
    controls_non_empty: bool
    all_controls_have_evidence: bool
    no_broken_links: bool
    warnings: list[str] = field(default_factory=list)


def compute_preflight(
    package: PackageModel,
    validation_warnings: list[str],
    settings: Settings,
) -> PreflightOutcome:
    """Compute weighted pre-flight score and block decision."""
    metadata_complete = _metadata_complete(package)
    controls_non_empty = len(package.controls) > 0
    all_controls_have_evidence = controls_non_empty and all(
        len(control.linked_evidence_ids) >= 1 for control in package.controls
    )
    no_broken_links = _no_broken_links(package)

    criteria = (
        metadata_complete,
        controls_non_empty,
        all_controls_have_evidence,
        no_broken_links,
    )
    score = round(sum(_PREFLIGHT_WEIGHT for passed in criteria if passed), 4)
    blocked = score < settings.preflight_block_threshold

    return PreflightOutcome(
        score=score,
        blocked=blocked,
        metadata_complete=metadata_complete,
        controls_non_empty=controls_non_empty,
        all_controls_have_evidence=all_controls_have_evidence,
        no_broken_links=no_broken_links,
        warnings=list(validation_warnings),
    )


def _metadata_complete(package: PackageModel) -> bool:
    required = (
        package.system_name,
        package.impact_level,
        package.data_classification,
        package.authorization_boundary,
    )
    return all(value.strip() for value in required)


def _no_broken_links(package: PackageModel) -> bool:
    evidence_ids = {item.evidence_id for item in package.evidence_items}
    for control in package.controls:
        for evidence_id in control.linked_evidence_ids:
            if evidence_id not in evidence_ids:
                return False
    return True
