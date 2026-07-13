"""Orchestrate bounded normalize_proposal processing."""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from typing import Any, Sequence

from ato_service.model_gateway import ModelCallRequest
from ato_service.normalize_proposal.client import (
    BeforeCallHook,
    NormalizeModelCallError,
    NormalizeModelRoutingError,
    TextClientFactory,
    invoke_normalize_model,
    normalize_model_request,
)
from ato_service.normalize_proposal.fact_bundle import ContextLimitExceededError, build_fact_bundle
from ato_service.normalize_proposal.merge import merge_proposals, reject_cross_source_duplicates
from ato_service.normalize_proposal.parse import ResponseValidationError, validate_and_parse_response
from ato_service.normalize_proposal.prompt import frozen_prompt_sha256, prompt_contract_metadata
from ato_service.normalize_proposal.source_binding import verify_proposal_source_binding
from ato_service.normalize_proposal.target_catalog import list_empty_targets
from ato_service.normalize_proposal.types import (
    ArtifactFacts,
    ModelCallMetadata,
    NormalizeProposalResult,
    ParsedProposal,
    ValidationOutcome,
)
from ato_service.normalize_proposal.value_validation import verify_proposal_value
from ato_service.text_llm import TextModelClient


def _unchanged_result(
    *,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
    validation_outcome: ValidationOutcome,
    llm_call_count: int = 0,
    omitted_segment_ids: tuple[str, ...] = (),
    context_complete: bool = True,
    fact_bundle_sha256: str | None = None,
    model_calls: tuple[ModelCallMetadata, ...] = (),
    error_code: str | None = None,
    response_sha256: str | None = None,
) -> NormalizeProposalResult:
    contract = prompt_contract_metadata()
    return NormalizeProposalResult(
        document=document,
        field_provenance=field_provenance,
        validation_outcome=validation_outcome,
        llm_call_count=llm_call_count,
        merged_targets=(),
        rejected_proposals=(),
        omitted_segment_ids=omitted_segment_ids,
        context_complete=context_complete,
        fact_bundle_sha256=fact_bundle_sha256,
        prompt_version=contract["prompt_version"],
        prompt_sha256=contract["prompt_sha256"],
        response_sha256=response_sha256,
        model_calls=model_calls,
        error_code=error_code,
    )


def _model_call_failure_outcome(error_code: str) -> ValidationOutcome:
    if error_code == "model_not_configured":
        return "model_not_configured"
    return "model_call_failed"


def _segment_lookup(artifacts: Sequence[ArtifactFacts]) -> dict[tuple[uuid.UUID, int], Any]:
    lookup: dict[tuple[uuid.UUID, int], Any] = {}
    for artifact in artifacts:
        for segment in artifact.segments:
            lookup[(artifact.artifact_id, segment.segment_index)] = segment
    return lookup


def _verify_all_proposals(
    *,
    profile_id: str,
    proposals: tuple[ParsedProposal, ...],
    artifacts: Sequence[ArtifactFacts],
    document_shell: dict[str, Any],
) -> tuple[tuple[ParsedProposal, ...], tuple[str, ...]]:
    segments = _segment_lookup(artifacts)
    accepted: list[ParsedProposal] = []
    rejected: list[str] = []
    for proposal in proposals:
        segment = segments.get((proposal.source_artifact_id, proposal.segment_index))
        if segment is None or not verify_proposal_source_binding(
            proposal=proposal,
            segment=segment,
        ):
            rejected.append(proposal.target)
            continue
        try:
            verify_proposal_value(
                profile_id=profile_id,
                proposal=proposal,
                segment_text=segment.text,
                document_shell=document_shell,
            )
        except ResponseValidationError:
            rejected.append(proposal.target)
            continue
        accepted.append(proposal)
    return tuple(accepted), tuple(rejected)


async def run_normalize_proposal(
    *,
    profile_id: str,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
    artifacts: Sequence[ArtifactFacts],
    context_tokens: int,
    max_output_tokens: int,
    model_request: ModelCallRequest,
    step_id: uuid.UUID,
    text_client: TextModelClient | None = None,
    client_factory: TextClientFactory | None = None,
    before_call: BeforeCallHook | None = None,
) -> NormalizeProposalResult:
    """Run bounded normalize_proposal; leaves draft unchanged on failure."""
    if text_client is None and client_factory is None:
        raise ValueError("text_client or client_factory is required")
    document_copy = copy.deepcopy(document)
    provenance_copy = copy.deepcopy(field_provenance)
    request = normalize_model_request(model_request)

    empty_targets = list_empty_targets(
        profile_id=profile_id,
        document=document_copy,
        field_provenance=provenance_copy,
    )
    if not empty_targets:
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome="skipped_no_targets",
        )

    if not any(artifact.segments for artifact in artifacts):
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome="skipped_no_segments",
        )

    try:
        bundle = build_fact_bundle(
            profile_id=profile_id,
            empty_targets=empty_targets,
            artifacts=artifacts,
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
        )
    except ContextLimitExceededError as exc:
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome="rejected_context_limit",
            error_code=exc.error_code,
        )

    model_calls: list[ModelCallMetadata] = []
    llm_call_count = request.current_llm_call_count
    response_sha256: str | None = None

    try:
        raw, metadata, llm_call_count = await invoke_normalize_model(
            request=request,
            bundle=bundle,
            max_output_tokens=max_output_tokens,
            text_client=text_client,
            client_factory=client_factory,
            before_call=before_call,
        )
    except NormalizeModelRoutingError as exc:
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome="rejected_routing",
            llm_call_count=exc.llm_call_count,
            omitted_segment_ids=bundle.omitted_segment_ids,
            context_complete=bundle.context_complete,
            fact_bundle_sha256=bundle.fact_bundle_sha256,
            model_calls=tuple(model_calls),
            error_code=exc.error_code,
        )
    except NormalizeModelCallError as exc:
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome=_model_call_failure_outcome(exc.error_code),
            llm_call_count=exc.llm_call_count,
            omitted_segment_ids=bundle.omitted_segment_ids,
            context_complete=bundle.context_complete,
            fact_bundle_sha256=bundle.fact_bundle_sha256,
            model_calls=tuple(model_calls),
            error_code=exc.error_code,
        )

    model_calls.append(metadata)
    response_sha256 = metadata.response_sha256

    repair_errors: tuple[str, ...] | None = None
    parsed = None
    try:
        parsed = validate_and_parse_response(
            raw_text=raw,
            profile_id=profile_id,
            empty_targets=empty_targets,
            artifacts=bundle.artifacts,
        )
    except ResponseValidationError as exc:
        if not exc.repairable or llm_call_count >= request.max_llm_calls:
            return _unchanged_result(
                document=document_copy,
                field_provenance=provenance_copy,
                validation_outcome="repair_exhausted" if exc.repairable else "rejected_policy",
                llm_call_count=llm_call_count,
                omitted_segment_ids=bundle.omitted_segment_ids,
                context_complete=bundle.context_complete,
                fact_bundle_sha256=bundle.fact_bundle_sha256,
                model_calls=tuple(model_calls),
                error_code=exc.failure_kind,
                response_sha256=response_sha256,
            )
        repair_errors = (exc.detail,)

    if parsed is None and repair_errors is not None:
        repair_request = replace_request_count(request, llm_call_count)
        try:
            raw, repair_metadata, llm_call_count = await invoke_normalize_model(
                request=repair_request,
                bundle=bundle,
                max_output_tokens=max_output_tokens,
                repair_errors=repair_errors,
                prior_response=metadata.raw_response,
                text_client=text_client,
                client_factory=client_factory,
                before_call=before_call,
            )
        except NormalizeModelRoutingError as exc:
            return _unchanged_result(
                document=document_copy,
                field_provenance=provenance_copy,
                validation_outcome="rejected_routing",
                llm_call_count=exc.llm_call_count,
                omitted_segment_ids=bundle.omitted_segment_ids,
                context_complete=bundle.context_complete,
                fact_bundle_sha256=bundle.fact_bundle_sha256,
                model_calls=tuple(model_calls),
                error_code=exc.error_code,
                response_sha256=response_sha256,
            )
        except NormalizeModelCallError as exc:
            return _unchanged_result(
                document=document_copy,
                field_provenance=provenance_copy,
                validation_outcome=_model_call_failure_outcome(exc.error_code),
                llm_call_count=exc.llm_call_count,
                omitted_segment_ids=bundle.omitted_segment_ids,
                context_complete=bundle.context_complete,
                fact_bundle_sha256=bundle.fact_bundle_sha256,
                model_calls=tuple(model_calls),
                error_code=exc.error_code,
                response_sha256=response_sha256,
            )

        model_calls.append(repair_metadata)
        response_sha256 = repair_metadata.response_sha256
        try:
            parsed = validate_and_parse_response(
                raw_text=raw,
                profile_id=profile_id,
                empty_targets=empty_targets,
                artifacts=bundle.artifacts,
            )
        except ResponseValidationError as exc:
            outcome: ValidationOutcome = (
                "repair_exhausted" if exc.repairable else "rejected_policy"
            )
            return _unchanged_result(
                document=document_copy,
                field_provenance=provenance_copy,
                validation_outcome=outcome,
                llm_call_count=llm_call_count,
                omitted_segment_ids=bundle.omitted_segment_ids,
                context_complete=bundle.context_complete,
                fact_bundle_sha256=bundle.fact_bundle_sha256,
                model_calls=tuple(model_calls),
                error_code=exc.failure_kind,
                response_sha256=response_sha256,
            )

    assert parsed is not None
    deduped, duplicate_rejects = reject_cross_source_duplicates(parsed.proposals)
    verified, value_rejects = _verify_all_proposals(
        profile_id=profile_id,
        proposals=deduped,
        artifacts=bundle.artifacts,
        document_shell=document_copy,
    )
    if not verified:
        return _unchanged_result(
            document=document_copy,
            field_provenance=provenance_copy,
            validation_outcome="rejected_value",
            llm_call_count=llm_call_count,
            omitted_segment_ids=bundle.omitted_segment_ids,
            context_complete=bundle.context_complete,
            fact_bundle_sha256=bundle.fact_bundle_sha256,
            model_calls=tuple(model_calls),
            error_code="value_support",
            response_sha256=response_sha256,
        )

    merged_document, merged_provenance, merged_targets, merge_rejects = merge_proposals(
        document=document_copy,
        field_provenance=provenance_copy,
        proposals=verified,
        step_id=step_id,
    )
    rejected = tuple(sorted(set(duplicate_rejects + value_rejects + merge_rejects)))
    outcome: ValidationOutcome = "accepted" if merged_targets else "rejected_merge"
    if repair_errors is not None and merged_targets:
        outcome = "repair_succeeded"

    return NormalizeProposalResult(
        document=merged_document,
        field_provenance=merged_provenance,
        validation_outcome=outcome,
        llm_call_count=llm_call_count,
        merged_targets=merged_targets,
        rejected_proposals=rejected,
        omitted_segment_ids=bundle.omitted_segment_ids,
        context_complete=bundle.context_complete,
        fact_bundle_sha256=bundle.fact_bundle_sha256,
        prompt_version=prompt_contract_metadata()["prompt_version"],
        prompt_sha256=frozen_prompt_sha256(),
        response_sha256=response_sha256,
        model_calls=tuple(model_calls),
        error_code=None if merged_targets else "merge_conflict",
    )


def replace_request_count(request: ModelCallRequest, current_llm_call_count: int) -> ModelCallRequest:
    return replace(request, current_llm_call_count=current_llm_call_count)
