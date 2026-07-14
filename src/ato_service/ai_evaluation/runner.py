"""Bounded qualification evaluation runner skeleton.

This module orchestrates explicit, operator-supplied evaluation inputs. It never
infers a passing outcome or closes HS-006. Model calls remain behind the existing
model gateway and fake client boundaries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from ato_service.model_gateway import (
    ModelCallLimitExceededError,
    ModelCallRequest,
    ModelCapability,
    ModelRoutingDeniedError,
    invoke_model_call,
)
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.text_llm import TextModelClient

RunnerOutcome = Literal["failed", "invalid"]


@dataclass(frozen=True, slots=True)
class EvaluationCaseInput:
    """Explicit metadata for one bounded qualification case attempt."""

    case_id: str
    prompt_sha256: str
    fact_bundle_sha256: str
    response_sha256: str


@dataclass(frozen=True, slots=True)
class EvaluationRunRequest:
    """Explicit operator inputs for one bounded qualification attempt."""

    evaluation_id: str
    holdout_manifest_sha256: str
    corpus_digest_sha256: str
    model_step: str
    profile_id: str
    profile_version: str
    cases: tuple[EvaluationCaseInput, ...]
    declared_outcome: RunnerOutcome
    declared_blockers: tuple[str, ...]
    gateway_request: ModelCallRequest


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    """Deterministic runner output without inferring qualification success."""

    evaluation_id: str
    outcome: RunnerOutcome
    llm_call_count: int
    failure_codes: tuple[str, ...]
    blockers: tuple[str, ...]
    per_case_attempt_metadata: tuple[dict[str, Any], ...]


TextClientFactory = Callable[[], TextModelClient]
CaseCallbackFactory = Callable[[EvaluationCaseInput], Callable[[], Awaitable[str]]]


def run_bounded_evaluation_sync(
    request: EvaluationRunRequest,
    *,
    callback_factory: CaseCallbackFactory,
) -> EvaluationRunResult:
    """Execute explicit gateway calls and return a non-passing runner result."""
    return asyncio.run(
        run_bounded_evaluation(
            request,
            callback_factory=callback_factory,
        )
    )


async def run_bounded_evaluation(
    request: EvaluationRunRequest,
    *,
    callback_factory: CaseCallbackFactory,
) -> EvaluationRunResult:
    """Execute explicit gateway calls and return a non-passing runner result."""
    if request.declared_outcome == "passed":
        raise ValueError("runner must not accept declared_outcome=passed")
    if not request.declared_blockers:
        raise ValueError("runner requires explicit declared_blockers")
    if not request.cases:
        raise ValueError("runner requires explicit cases")

    llm_call_count = request.gateway_request.current_llm_call_count
    per_case_metadata: list[dict[str, Any]] = []
    failure_codes = list(request.declared_blockers)
    gateway_request = request.gateway_request

    for case in request.cases:
        active_request = _replace_call_count(gateway_request, llm_call_count)
        try:
            result = await invoke_model_call(
                active_request,
                callback_factory(case),
            )
        except (ModelRoutingDeniedError, ModelCallLimitExceededError) as exc:
            failure_codes.append(exc.error_code)
            per_case_metadata.append(_case_metadata(case, request.model_step))
            llm_call_count = exc.llm_call_count
            continue

        llm_call_count = result.llm_call_count
        per_case_metadata.append(_case_metadata(case, request.model_step))

    return EvaluationRunResult(
        evaluation_id=request.evaluation_id,
        outcome=request.declared_outcome,
        llm_call_count=llm_call_count,
        failure_codes=tuple(dict.fromkeys(failure_codes)),
        blockers=request.declared_blockers,
        per_case_attempt_metadata=tuple(per_case_metadata),
    )


def build_explicit_gateway_request(
    *,
    capability: ModelCapability = ModelCapability.SUFFICIENCY_MATRIX,
    current_llm_call_count: int = 0,
    max_llm_calls: int = 1,
) -> ModelCallRequest:
    """Construct one explicit gateway request for qualification runner tests."""
    return ModelCallRequest(
        capability=capability,
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.PUBLIC,
        endpoint_profile=EndpointProfile.MOCK,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
        vision_model_enabled=False,
        current_llm_call_count=current_llm_call_count,
        max_llm_calls=max_llm_calls,
    )


def _replace_call_count(
    request: ModelCallRequest,
    llm_call_count: int,
) -> ModelCallRequest:
    return ModelCallRequest(
        capability=request.capability,
        data_origin=request.data_origin,
        sensitivity=request.sensitivity,
        endpoint_profile=request.endpoint_profile,
        endpoint_policy_approved=request.endpoint_policy_approved,
        cui_boundary_approved=request.cui_boundary_approved,
        vision_model_enabled=request.vision_model_enabled,
        current_llm_call_count=llm_call_count,
        max_llm_calls=request.max_llm_calls,
    )


def _case_metadata(case: EvaluationCaseInput, model_step: str) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "model_step": model_step,
        "attempt_index": 0,
        "prompt_sha256": case.prompt_sha256,
        "fact_bundle_sha256": case.fact_bundle_sha256,
        "response_sha256": case.response_sha256,
    }
