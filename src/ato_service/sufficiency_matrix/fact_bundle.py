"""Fact bundle construction for sufficiency_matrix batches."""

from __future__ import annotations

from typing import Any

from ato_service.citation_validation import CitableSource, build_sealed_citable_sources
from ato_service.db.models import SealedPackageContent
from ato_service.normalize_proposal.json_utils import stable_json_dumps
from ato_service.sufficiency_matrix.constants import (
    INSTRUCTION_OVERHEAD_TOKENS,
    MAX_EVIDENCE_EXCERPT_CHARS,
    MINIMUM_BUNDLE_RESERVE_TOKENS,
    sha256_text,
)
from ato_service.sufficiency_matrix.profile_catalog import assessment_items_for_prompt
from ato_service.sufficiency_matrix.tokens import (
    compute_input_token_budget,
    estimate_tokens_from_object,
)
from ato_service.sufficiency_matrix.types import EvidenceSource, FactBundle


class ContextLimitExceededError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.error_code = "context_limit_exceeded"


def _cap_excerpt(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return text, False
    return text[:MAX_EVIDENCE_EXCERPT_CHARS], True


def _index_evidence_sources(
    *,
    sealed: SealedPackageContent,
) -> tuple[EvidenceSource, ...]:
    sources = build_sealed_citable_sources(
        sealed_document=sealed.document,
        field_provenance=sealed.field_provenance,
    )
    indexed: list[EvidenceSource] = []
    controls = sealed.document.get("security_controls") or {}
    for source_id, source in sources.items():
        control_id = None
        for candidate_id, control in controls.items():
            if not isinstance(control, dict):
                continue
            statement = control.get("implementation_statement")
            if (
                isinstance(statement, str)
                and statement
                and source.text == statement
            ):
                control_id = str(candidate_id)
                break
        indexed.append(
            EvidenceSource(
                source_id=source.source_id.lower(),
                source_sha256=source.source_sha256,
                text=source.text,
                control_id=control_id,
            )
        )
    return tuple(sorted(indexed, key=lambda item: item.source_id))


def build_fact_bundle(
    *,
    profile: dict[str, Any],
    assessment_item_ids: tuple[str, ...],
    sealed: SealedPackageContent,
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int = INSTRUCTION_OVERHEAD_TOKENS,
) -> FactBundle:
    """Build one bounded immutable fact bundle for a matrix batch."""
    profile_id = str(profile["profile_id"])
    input_budget = compute_input_token_budget(
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        instruction_overhead_tokens=instruction_overhead_tokens,
    )
    if input_budget < MINIMUM_BUNDLE_RESERVE_TOKENS:
        raise ContextLimitExceededError(
            "configured context budget cannot fit the minimum sufficiency_matrix bundle"
        )

    assessment_items = tuple(
        assessment_items_for_prompt(
            profile=profile,
            assessment_item_ids=assessment_item_ids,
        )
    )
    all_sources = _index_evidence_sources(sealed=sealed)
    fixed_payload = {
        "profile_id": profile_id,
        "assessment_item_ids": list(assessment_item_ids),
        "assessment_items": list(assessment_items),
        "evidence_sources": [],
        "omitted_source_ids": [],
    }
    used_tokens = estimate_tokens_from_object(fixed_payload)
    if used_tokens >= input_budget:
        raise ContextLimitExceededError(
            "assessment item metadata exceeds sufficiency_matrix context budget"
        )

    included_sources: list[dict[str, Any]] = []
    omitted_source_ids: list[str] = []
    remaining = input_budget - used_tokens
    for source in all_sources:
        excerpt, truncated = _cap_excerpt(source.text)
        entry = {
            "source_id": source.source_id,
            "source_sha256": source.source_sha256,
            "control_id": source.control_id,
            "text": excerpt,
            "text_truncated": truncated,
        }
        entry_tokens = estimate_tokens_from_object(entry)
        if entry_tokens > remaining:
            omitted_source_ids.append(source.source_id)
            continue
        included_sources.append(entry)
        remaining -= entry_tokens

    prompt_payload = {
        "profile_id": profile_id,
        "assessment_item_ids": list(assessment_item_ids),
        "assessment_items": list(assessment_items),
        "evidence_sources": included_sources,
        "omitted_source_ids": omitted_source_ids,
    }
    return FactBundle(
        profile_id=profile_id,
        assessment_item_ids=assessment_item_ids,
        assessment_items=assessment_items,
        evidence_sources=all_sources,
        omitted_source_ids=tuple(omitted_source_ids),
        fact_bundle_sha256=sha256_text(stable_json_dumps(prompt_payload)),
        context_complete=not omitted_source_ids,
        prompt_payload=prompt_payload,
    )


def sources_by_id(sources: tuple[EvidenceSource, ...]) -> dict[str, CitableSource]:
    return {
        source.source_id: CitableSource(
            source_id=source.source_id,
            source_sha256=source.source_sha256,
            text=source.text,
        )
        for source in sources
    }
