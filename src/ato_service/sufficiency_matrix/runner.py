"""Orchestrate bounded sufficiency_matrix processing."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, Sequence

from ato_service.analysis_profile import assessment_item_type_for_id
from ato_service.citation_validation import CitationValidationError, validate_citations
from ato_service.db.models import SealedPackageContent
from ato_service.domain_mapping import format_uuid
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.matrix_row_persistence import (
    StatusCeilingViolatedError,
    apply_ceilings_to_matrix_row_payloads,
)
from ato_service.model_gateway import ModelCallRequest
from ato_service.sufficiency_matrix.client import (
    SufficiencyModelCallError,
    SufficiencyModelRoutingError,
    TextClientFactory,
    invoke_sufficiency_model,
    sufficiency_model_request,
)
from ato_service.sufficiency_matrix.constants import MAX_BATCH_SIZE, MAX_LLM_CALLS_PER_BATCH
from ato_service.sufficiency_matrix.fact_bundle import (
    ContextLimitExceededError,
    build_fact_bundle,
    sources_by_id,
)
from ato_service.sufficiency_matrix.parse import ResponseValidationError, validate_and_parse_response
from ato_service.sufficiency_matrix.prompt import prompt_contract_metadata
from ato_service.sufficiency_matrix.types import (
    BatchValidationError,
    ModelCallMetadata,
    ParsedMatrixRow,
    SufficiencyMatrixResult,
    ValidationOutcome,
)
from ato_service.text_llm import TextModelClient


def _failure_result(
    *,
    validation_outcome: ValidationOutcome,
    llm_call_count: int,
    model_calls: tuple[ModelCallMetadata, ...] = (),
    fact_bundle_sha256: str | None = None,
    response_sha256: str | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> SufficiencyMatrixResult:
    contract = prompt_contract_metadata()
    return SufficiencyMatrixResult(
        row_payloads=(),
        validation_outcome=validation_outcome,
        llm_call_count=llm_call_count,
        model_calls=model_calls,
        fact_bundle_sha256=fact_bundle_sha256,
        prompt_version=contract["prompt_version"],
        prompt_sha256=contract["prompt_sha256"],
        response_sha256=response_sha256,
        error_code=error_code,
        retryable=retryable,
    )


def _chunk_assessment_item_ids(
    assessment_item_ids: Sequence[str],
    *,
    batch_size: int = MAX_BATCH_SIZE,
) -> tuple[tuple[str, ...], ...]:
    materialized = tuple(assessment_item_ids)
    if not materialized:
        return ()
    return tuple(
        materialized[index : index + batch_size]
        for index in range(0, len(materialized), batch_size)
    )


def _deterministic_row_payload(
    *,
    run_id: uuid.UUID,
    profile: dict[str, Any],
    assessment_item_id: str,
    status: str,
    summary: str,
    context_complete: bool,
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    matrix_row_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{format_uuid(run_id)}:{assessment_item_id}",
    )
    return {
        "schema_version": "2.0.0",
        "object_type": "matrix_row",
        "matrix_row_id": format_uuid(matrix_row_id),
        "assessment_item_type": assessment_item_type_for_id(profile, assessment_item_id),
        "assessment_item_id": assessment_item_id,
        "model_proposed_status": status,
        "system_status": status,
        "finding_summary": summary,
        "gaps": [] if status == "supported" else ["No usable evidence linked."],
        "assessor_questions": [],
        "citations": citations,
        "context_complete": context_complete,
        "producing_run_id": format_uuid(run_id),
        "source_run_id": format_uuid(run_id),
    }


def _rows_to_payloads(
    *,
    run_id: uuid.UUID,
    profile: dict[str, Any],
    parsed_rows: Sequence[ParsedMatrixRow],
    sources: dict[str, Any],
    status_policy: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for row in parsed_rows:
        citations = [dict(citation) for citation in row.citations]
        try:
            validate_citations(citations=citations, sources=sources)
        except CitationValidationError as exc:
            raise ResponseValidationError(
                failure_kind="citation",
                detail=exc.message,
                repairable=False,
            ) from exc
        payloads.append(
            _deterministic_row_payload(
                run_id=run_id,
                profile=profile,
                assessment_item_id=row.assessment_item_id,
                status=row.model_proposed_status,
                summary=row.finding_summary,
                context_complete=row.context_complete,
                citations=citations,
            )
        )
    try:
        return apply_ceilings_to_matrix_row_payloads(
            payloads,
            status_policy=status_policy,
        )
    except StatusCeilingViolatedError as exc:
        raise ResponseValidationError(
            failure_kind="status_ceiling",
            detail=str(exc),
            repairable=False,
        ) from exc


async def _invoke_batch_with_repair(
    *,
    request: ModelCallRequest,
    bundle: Any,
    max_output_tokens: int,
    model_requested: str,
    expected_ids: tuple[str, ...],
    text_client: TextModelClient | None,
    client_factory: TextClientFactory | None,
) -> tuple[tuple[ParsedMatrixRow, ...], tuple[ModelCallMetadata, ...], int, str | None]:
    model_calls: list[ModelCallMetadata] = []
    llm_call_count = request.current_llm_call_count
    response_sha256: str | None = None
    repair_request = sufficiency_model_request(request)

    try:
        raw, metadata, llm_call_count = await invoke_sufficiency_model(
            request=replace(repair_request, current_llm_call_count=llm_call_count),
            bundle=bundle,
            max_output_tokens=max_output_tokens,
            model_requested=model_requested,
            text_client=text_client,
            client_factory=client_factory,
        )
    except SufficiencyModelRoutingError as exc:
        raise exc
    except SufficiencyModelCallError as exc:
        raise exc

    model_calls.append(metadata)
    response_sha256 = metadata.response_sha256

    parsed = None
    repair_errors: tuple[str, ...] | None = None
    try:
        parsed = validate_and_parse_response(
            raw_text=raw,
            expected_assessment_item_ids=expected_ids,
        )
    except ResponseValidationError as exc:
        if not exc.repairable or llm_call_count >= request.max_llm_calls:
            raise BatchValidationError(
                cause=exc,
                model_calls=tuple(model_calls),
                llm_call_count=llm_call_count,
                response_sha256=response_sha256,
            ) from exc
        repair_errors = (exc.detail,)

    if parsed is None and repair_errors is not None:
        try:
            raw, repair_metadata, llm_call_count = await invoke_sufficiency_model(
                request=replace(repair_request, current_llm_call_count=llm_call_count),
                bundle=bundle,
                max_output_tokens=max_output_tokens,
                model_requested=model_requested,
                repair_errors=repair_errors,
                prior_response=metadata.raw_response,
                text_client=text_client,
                client_factory=client_factory,
            )
        except SufficiencyModelRoutingError as exc:
            raise exc
        except SufficiencyModelCallError as exc:
            raise exc
        model_calls.append(repair_metadata)
        response_sha256 = repair_metadata.response_sha256
        try:
            parsed = validate_and_parse_response(
                raw_text=raw,
                expected_assessment_item_ids=expected_ids,
            )
        except ResponseValidationError as exc:
            raise BatchValidationError(
                cause=exc,
                model_calls=tuple(model_calls),
                llm_call_count=llm_call_count,
                response_sha256=response_sha256,
            ) from exc

    assert parsed is not None
    return parsed.rows, tuple(model_calls), llm_call_count, response_sha256


async def run_sufficiency_matrix(
    *,
    run_id: uuid.UUID,
    profile: dict[str, Any],
    assessment_item_ids: tuple[str, ...],
    sealed: SealedPackageContent,
    model_request: ModelCallRequest,
    context_tokens: int,
    max_output_tokens: int,
    model_requested: str,
    text_client: TextModelClient | None = None,
    client_factory: TextClientFactory | None = None,
) -> SufficiencyMatrixResult:
    """Run bounded sufficiency_matrix for one analysis run without partial persistence."""
    if text_client is None and client_factory is None:
        raise ValueError("text_client or client_factory is required")

    request = sufficiency_model_request(model_request)
    if request.max_llm_calls > MAX_LLM_CALLS_PER_BATCH:
        raise ValueError(f"max_llm_calls must not exceed {MAX_LLM_CALLS_PER_BATCH}")

    status_policy = profile.get("status_policy")
    items_by_id = {
        item["assessment_item_id"]: item
        for item in profile.get("assessment_items", [])
        if isinstance(item, dict)
    }
    all_payloads: list[dict[str, Any]] = []
    all_model_calls: list[ModelCallMetadata] = []
    llm_call_count = request.current_llm_call_count
    last_response_sha256: str | None = None
    last_fact_bundle_sha256: str | None = None

    for batch_ids in _chunk_assessment_item_ids(assessment_item_ids):
        deterministic_ids = tuple(
            assessment_item_id
            for assessment_item_id in batch_ids
            if not bool(items_by_id.get(assessment_item_id, {}).get("model_analysis_allowed"))
        )
        model_ids = tuple(
            assessment_item_id
            for assessment_item_id in batch_ids
            if bool(items_by_id.get(assessment_item_id, {}).get("model_analysis_allowed"))
        )

        for assessment_item_id in deterministic_ids:
            all_payloads.append(
                _deterministic_row_payload(
                    run_id=run_id,
                    profile=profile,
                    assessment_item_id=assessment_item_id,
                    status="insufficient_evidence",
                    summary=(
                        f"No model-assisted analysis is allowed for {assessment_item_id} "
                        "under the pinned profile."
                    ),
                    context_complete=False,
                    citations=[],
                )
            )

        if not model_ids:
            continue

        try:
            bundle = build_fact_bundle(
                profile=profile,
                assessment_item_ids=model_ids,
                sealed=sealed,
                context_tokens=context_tokens,
                max_output_tokens=max_output_tokens,
            )
        except ContextLimitExceededError as exc:
            return _failure_result(
                validation_outcome="rejected_context_limit",
                llm_call_count=llm_call_count,
                model_calls=tuple(all_model_calls),
                error_code=exc.error_code,
            )

        last_fact_bundle_sha256 = bundle.fact_bundle_sha256
        if not bundle.evidence_sources:
            for assessment_item_id in model_ids:
                all_payloads.append(
                    _deterministic_row_payload(
                        run_id=run_id,
                        profile=profile,
                        assessment_item_id=assessment_item_id,
                        status="insufficient_evidence",
                        summary=(
                            f"No usable sealed evidence linked for {assessment_item_id} "
                            "in the package snapshot."
                        ),
                        context_complete=False,
                        citations=[],
                    )
                )
            continue

        batch_calls: tuple[ModelCallMetadata, ...] = ()
        try:
            parsed_rows, batch_calls, llm_call_count, response_sha256 = (
                await _invoke_batch_with_repair(
                    request=replace(request, current_llm_call_count=llm_call_count),
                    bundle=bundle,
                    max_output_tokens=max_output_tokens,
                    model_requested=model_requested,
                    expected_ids=model_ids,
                    text_client=text_client,
                    client_factory=client_factory,
                )
            )
        except SufficiencyModelRoutingError as exc:
            return _failure_result(
                validation_outcome="rejected_routing",
                llm_call_count=exc.llm_call_count,
                model_calls=tuple(all_model_calls),
                fact_bundle_sha256=last_fact_bundle_sha256,
                response_sha256=last_response_sha256,
                error_code=exc.error_code,
            )
        except SufficiencyModelCallError as exc:
            outcome: ValidationOutcome = (
                "model_timeout" if exc.error_code == "model_timeout" else "model_call_failed"
            )
            return _failure_result(
                validation_outcome=outcome,
                llm_call_count=exc.llm_call_count,
                model_calls=tuple(all_model_calls),
                fact_bundle_sha256=last_fact_bundle_sha256,
                response_sha256=last_response_sha256,
                error_code=exc.error_code,
                retryable=exc.retryable,
            )
        except BatchValidationError as exc:
            exc_info = exc.cause
            outcome: ValidationOutcome = (
                "repair_exhausted" if exc_info.repairable else "rejected_policy"
            )
            if exc_info.failure_kind == "citation":
                outcome = "rejected_citation"
            elif exc_info.failure_kind == "coverage":
                outcome = "rejected_coverage"
            elif exc_info.failure_kind == "status_ceiling":
                outcome = "rejected_status_ceiling"
            return _failure_result(
                validation_outcome=outcome,
                llm_call_count=exc.llm_call_count,
                model_calls=tuple(all_model_calls) + exc.model_calls,
                fact_bundle_sha256=last_fact_bundle_sha256,
                response_sha256=exc.response_sha256,
                error_code=exc_info.failure_kind,
            )

        all_model_calls.extend(batch_calls)
        last_response_sha256 = response_sha256
        sources = sources_by_id(bundle.evidence_sources)
        try:
            all_payloads.extend(
                _rows_to_payloads(
                    run_id=run_id,
                    profile=profile,
                    parsed_rows=parsed_rows,
                    sources=sources,
                    status_policy=status_policy,
                )
            )
        except ResponseValidationError as exc:
            outcome: ValidationOutcome = "rejected_policy"
            if exc.failure_kind == "citation":
                outcome = "rejected_citation"
            elif exc.failure_kind == "status_ceiling":
                outcome = "rejected_status_ceiling"
            return _failure_result(
                validation_outcome=outcome,
                llm_call_count=llm_call_count,
                model_calls=tuple(all_model_calls),
                fact_bundle_sha256=last_fact_bundle_sha256,
                response_sha256=last_response_sha256,
                error_code=exc.failure_kind,
            )

    try:
        adjusted = apply_ceilings_to_matrix_row_payloads(
            all_payloads,
            status_policy=status_policy,
        )
        require_exact_matrix_coverage(
            assessment_item_ids,
            [row["assessment_item_id"] for row in adjusted],
        )
    except Exception:
        return _failure_result(
            validation_outcome="rejected_coverage",
            llm_call_count=llm_call_count,
            model_calls=tuple(all_model_calls),
            fact_bundle_sha256=last_fact_bundle_sha256,
            response_sha256=last_response_sha256,
            error_code="matrix_coverage_invalid",
        )

    contract = prompt_contract_metadata()
    return SufficiencyMatrixResult(
        row_payloads=tuple(adjusted),
        validation_outcome="accepted",
        llm_call_count=llm_call_count,
        model_calls=tuple(all_model_calls),
        fact_bundle_sha256=last_fact_bundle_sha256,
        prompt_version=contract["prompt_version"],
        prompt_sha256=contract["prompt_sha256"],
        response_sha256=last_response_sha256,
        error_code=None,
        retryable=False,
    )
