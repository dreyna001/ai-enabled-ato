"""Merge validated normalize_proposal results with provenance."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from ato_service.normalize_proposal.json_utils import set_json_pointer, value_at_json_pointer
from ato_service.normalize_proposal.target_catalog import is_target_empty
from ato_service.normalize_proposal.types import ParsedProposal


def merge_proposals(
    *,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
    proposals: tuple[ParsedProposal, ...],
    step_id: uuid.UUID,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """Merge accepted proposals into draft copies."""
    merged_document = copy.deepcopy(document)
    merged_provenance = copy.deepcopy(field_provenance)
    by_target: dict[str, list[ParsedProposal]] = {}
    for proposal in proposals:
        by_target.setdefault(proposal.target, []).append(proposal)

    merged_targets: list[str] = []
    rejected_targets: list[str] = []

    for target in sorted(by_target):
        entries = by_target[target]
        if len(entries) != 1:
            rejected_targets.append(target)
            continue

        proposal = entries[0]
        if not is_target_empty(merged_document, target):
            rejected_targets.append(target)
            continue

        existing = merged_provenance.get(target)
        if isinstance(existing, dict):
            method = existing.get("extraction_method")
            if method in {"deterministic", "text", "vision"}:
                rejected_targets.append(target)
                continue

        try:
            current = value_at_json_pointer(merged_document, target)
        except (KeyError, IndexError, TypeError):
            current = None
        if current not in (None, "", [], {}):
            rejected_targets.append(target)
            continue

        set_json_pointer(merged_document, target, proposal.proposed_value)
        merged_provenance[target] = {
            "source_artifact_id": str(proposal.source_artifact_id).lower(),
            "source_sha256": proposal.source_sha256,
            "source_locator": copy.deepcopy(proposal.source_locator),
            "extraction_method": "llm_normalize",
            "model_step_id": str(step_id).lower(),
        }
        merged_targets.append(target)

    return (
        merged_document,
        merged_provenance,
        tuple(merged_targets),
        tuple(rejected_targets),
    )


def reject_cross_source_duplicates(
    proposals: tuple[ParsedProposal, ...],
) -> tuple[tuple[ParsedProposal, ...], tuple[str, ...]]:
    by_target: dict[str, list[ParsedProposal]] = {}
    for proposal in proposals:
        by_target.setdefault(proposal.target, []).append(proposal)

    accepted: list[ParsedProposal] = []
    rejected: list[str] = []
    for target, entries in by_target.items():
        if len(entries) == 1:
            accepted.append(entries[0])
        else:
            rejected.append(target)
    return tuple(accepted), tuple(rejected)
