"""Fact bundle construction and context budgeting."""

from __future__ import annotations

import copy
import uuid
from typing import Sequence

from ato_service.normalize_proposal.constants import (
    INSTRUCTION_OVERHEAD_TOKENS,
    MAX_SEGMENT_EXCERPT_CHARS,
    MINIMUM_BUNDLE_RESERVE_TOKENS,
    sha256_text,
)
from ato_service.normalize_proposal.json_utils import stable_json_dumps
from ato_service.normalize_proposal.target_catalog import (
    catalog_entries_for_empty_targets,
    search_terms_for_empty_targets,
)
from ato_service.normalize_proposal.tokens import (
    compute_input_token_budget,
    estimate_tokens_from_object,
    estimate_tokens_from_text,
)
from ato_service.normalize_proposal.types import ArtifactFacts, FactBundle, SegmentFact


class ContextLimitExceededError(Exception):
    """Minimum fact bundle cannot fit in the configured context budget."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.error_code = "context_limit_exceeded"


def _segment_id(*, artifact_id: uuid.UUID, segment_index: int) -> str:
    return f"{artifact_id}:{segment_index}"


def _cap_segment_excerpt(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_SEGMENT_EXCERPT_CHARS:
        return text, False
    return text[:MAX_SEGMENT_EXCERPT_CHARS], True


def _segment_relevance_score(*, text: str, search_terms: frozenset[str]) -> int:
    if not search_terms:
        return 0
    normalized = text.casefold()
    return sum(1 for term in search_terms if term in normalized)


def _minimum_bundle_tokens(
    *,
    profile_id: str,
    empty_targets: Sequence[str],
    artifact: ArtifactFacts,
) -> int:
    payload = {
        "profile_id": profile_id,
        "empty_targets": list(empty_targets),
        "target_catalog": catalog_entries_for_empty_targets(
            profile_id=profile_id,
            empty_targets=tuple(empty_targets),
        ),
        "artifacts": [
            {
                "artifact_id": str(artifact.artifact_id).lower(),
                "sha256": artifact.sha256,
                "filename": artifact.filename,
                "detected_format": artifact.detected_format,
                "segments": [
                    {
                        "segment_index": 1,
                        "text": "",
                        "locator_kind": None,
                        "extraction_method": "text",
                        "text_truncated": False,
                    }
                ],
            }
        ],
    }
    return estimate_tokens_from_object(payload)


def build_fact_bundle(
    *,
    profile_id: str,
    empty_targets: Sequence[str],
    artifacts: Sequence[ArtifactFacts],
    context_tokens: int,
    max_output_tokens: int,
    instruction_overhead_tokens: int = INSTRUCTION_OVERHEAD_TOKENS,
) -> FactBundle:
    """Build a bounded immutable fact bundle from artifact segments."""
    input_budget = compute_input_token_budget(
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        instruction_overhead_tokens=instruction_overhead_tokens,
    )
    if input_budget < MINIMUM_BUNDLE_RESERVE_TOKENS:
        raise ContextLimitExceededError(
            "configured context budget cannot fit the minimum normalize_proposal bundle"
        )

    target_catalog = catalog_entries_for_empty_targets(
        profile_id=profile_id,
        empty_targets=tuple(empty_targets),
    )
    fixed_payload = {
        "profile_id": profile_id,
        "empty_targets": list(empty_targets),
        "target_catalog": target_catalog,
    }
    used_tokens = estimate_tokens_from_object(fixed_payload)
    if used_tokens >= input_budget:
        raise ContextLimitExceededError(
            "empty target metadata exceeds normalize_proposal context budget"
        )

    remaining = input_budget - used_tokens
    omitted_segment_ids: list[str] = []
    context_complete = True
    search_terms = search_terms_for_empty_targets(
        profile_id=profile_id,
        empty_targets=tuple(empty_targets),
    )

    all_segments: list[tuple[ArtifactFacts, SegmentFact, int]] = []
    for artifact in artifacts:
        for segment in artifact.segments:
            excerpt, _ = _cap_segment_excerpt(segment.text)
            relevance = _segment_relevance_score(text=excerpt, search_terms=search_terms)
            all_segments.append((artifact, segment, relevance))

    all_segments.sort(
        key=lambda item: (
            -item[2],
            str(item[0].artifact_id),
            item[1].segment_index,
        )
    )

    if not all_segments:
        payload = {
            **fixed_payload,
            "artifacts": [],
            "omitted_segment_ids": [],
            "context_complete": True,
        }
        digest = sha256_text(stable_json_dumps(payload))
        return FactBundle(
            profile_id=profile_id,
            empty_targets=tuple(empty_targets),
            artifacts=tuple(),
            omitted_segment_ids=tuple(),
            fact_bundle_sha256=digest,
            context_complete=True,
            prompt_payload=payload,
        )

    minimum = min(
        _minimum_bundle_tokens(
            profile_id=profile_id,
            empty_targets=empty_targets,
            artifact=artifact,
        )
        for artifact, _segment, _relevance in all_segments
    )
    if minimum > input_budget:
        raise ContextLimitExceededError(
            "minimum single-segment fact bundle exceeds normalize_proposal context budget"
        )

    artifact_segments: dict[uuid.UUID, list[SegmentFact]] = {}
    artifact_lookup: dict[uuid.UUID, ArtifactFacts] = {}

    for artifact, segment, _relevance in all_segments:
        artifact_lookup[artifact.artifact_id] = artifact
        excerpt, excerpt_truncated = _cap_segment_excerpt(segment.text)
        segment_tokens = estimate_tokens_from_object(
            {
                "artifact_id": str(artifact.artifact_id).lower(),
                "sha256": artifact.sha256,
                "filename": artifact.filename,
                "detected_format": artifact.detected_format,
                "segment_index": segment.segment_index,
                "text": excerpt,
                "locator_kind": segment.locator.get("kind"),
                "extraction_method": segment.extraction_method,
                "text_truncated": excerpt_truncated,
            }
        )
        if segment_tokens > remaining:
            omitted_segment_ids.append(
                _segment_id(artifact_id=artifact.artifact_id, segment_index=segment.segment_index)
            )
            context_complete = False
            continue

        included_text = excerpt
        text_truncated = excerpt_truncated
        if estimate_tokens_from_text(included_text) > remaining - 32:
            max_chars = max(1, min((remaining - 32) * 4, MAX_SEGMENT_EXCERPT_CHARS))
            included_text = included_text[:max_chars]
            text_truncated = True

        included = SegmentFact(
            segment_index=segment.segment_index,
            text=included_text,
            locator=copy.deepcopy(segment.locator),
            extraction_method=segment.extraction_method,
            text_truncated=text_truncated,
        )
        artifact_segments.setdefault(artifact.artifact_id, []).append(included)
        remaining -= estimate_tokens_from_object(
            {
                "segment_index": included.segment_index,
                "text": included.text,
                "locator_kind": included.locator.get("kind"),
                "extraction_method": included.extraction_method,
                "text_truncated": included.text_truncated,
            }
        )
        if remaining <= 0:
            context_complete = False

    bundled_artifacts: list[ArtifactFacts] = []
    for artifact_id, segments in sorted(artifact_segments.items(), key=lambda item: str(item[0])):
        source = artifact_lookup[artifact_id]
        bundled_artifacts.append(
            ArtifactFacts(
                artifact_id=source.artifact_id,
                sha256=source.sha256,
                filename=source.filename,
                detected_format=source.detected_format,
                segments=tuple(segments),
            )
        )

    payload = {
        **fixed_payload,
        "artifacts": [
            {
                "artifact_id": str(artifact.artifact_id).lower(),
                "sha256": artifact.sha256,
                "filename": artifact.filename,
                "detected_format": artifact.detected_format,
                "segments": [
                    {
                        "segment_index": segment.segment_index,
                        "text": segment.text,
                        "locator_kind": segment.locator.get("kind"),
                        "extraction_method": segment.extraction_method,
                        "text_truncated": segment.text_truncated,
                    }
                    for segment in artifact.segments
                ],
            }
            for artifact in bundled_artifacts
        ],
        "omitted_segment_ids": omitted_segment_ids,
        "context_complete": context_complete,
    }
    digest = sha256_text(stable_json_dumps(payload))
    return FactBundle(
        profile_id=profile_id,
        empty_targets=tuple(empty_targets),
        artifacts=tuple(bundled_artifacts),
        omitted_segment_ids=tuple(omitted_segment_ids),
        fact_bundle_sha256=digest,
        context_complete=context_complete,
        prompt_payload=payload,
    )
