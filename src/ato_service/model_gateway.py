"""Policy-ordered gateway for bounded async model callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from ato_service.lifecycle_transitions import AnalysisRunStatus
from ato_service.model_routing import (
    DataOrigin,
    EndpointProfile,
    Sensitivity,
    evaluate_model_routing,
)

T = TypeVar("T")


class ModelStepType(StrEnum):
    """Domain ``ModelStep.step_type`` values from the published contract."""

    NORMALIZE_PROPOSAL = "normalize_proposal"
    SUFFICIENCY_MATRIX = "sufficiency_matrix"
    CONSISTENCY_BRIEF = "consistency_brief"
    NARRATIVE_FLAGS = "narrative_flags"
    PROVIDER_DRAFT = "provider_draft"
    KSI_SUMMARY = "ksi_summary"
    OCR_SUMMARY = "ocr_summary"
    PACKAGE_CHAT = "package_chat"


class ModelCapability(StrEnum):
    """Model capabilities routed through the gateway."""

    NORMALIZE_PROPOSAL = ModelStepType.NORMALIZE_PROPOSAL.value
    SUFFICIENCY_MATRIX = ModelStepType.SUFFICIENCY_MATRIX.value
    CONSISTENCY_BRIEF = ModelStepType.CONSISTENCY_BRIEF.value
    NARRATIVE_FLAGS = ModelStepType.NARRATIVE_FLAGS.value
    PROVIDER_DRAFT = ModelStepType.PROVIDER_DRAFT.value
    KSI_SUMMARY = ModelStepType.KSI_SUMMARY.value
    OCR_SUMMARY = ModelStepType.OCR_SUMMARY.value
    PACKAGE_CHAT = ModelStepType.PACKAGE_CHAT.value
    VISION_EXTRACTION = "vision_extraction"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class ModelCallRequest:
    """Inputs required to evaluate policy and invoke one model callback."""

    capability: ModelCapability
    data_origin: DataOrigin
    sensitivity: Sensitivity
    endpoint_profile: EndpointProfile
    endpoint_policy_approved: bool
    cui_boundary_approved: bool
    vision_model_enabled: bool
    current_llm_call_count: int
    max_llm_calls: int


@dataclass(frozen=True, slots=True)
class ModelCallResult[T]:
    """Successful model callback output with the incremented call count."""

    value: T
    llm_call_count: int


@dataclass(frozen=True, slots=True)
class ModelRoutingDeniedError(Exception):
    """Routing policy denied the requested model capability."""

    error_code: str
    capability: ModelCapability
    target_run_status: str
    llm_call_count: int

    def __post_init__(self) -> None:
        if self.error_code != "model_routing_denied":
            raise ValueError("error_code must be model_routing_denied")
        if self.target_run_status != AnalysisRunStatus.POLICY_BLOCKED.value:
            raise ValueError("target_run_status must be policy_blocked")
        _validate_policy_denial_call_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )

    def __str__(self) -> str:
        return (
            f"model routing denied for capability {self.capability.value!r} "
            f"with llm_call_count={self.llm_call_count}"
        )


@dataclass(frozen=True, slots=True)
class ClassifiedDataUnsupportedError(Exception):
    """Classified data cannot be sent to any configured model endpoint."""

    error_code: str
    capability: ModelCapability
    target_run_status: str
    llm_call_count: int

    def __post_init__(self) -> None:
        if self.error_code != "classified_data_unsupported":
            raise ValueError("error_code must be classified_data_unsupported")
        if self.target_run_status != AnalysisRunStatus.POLICY_BLOCKED.value:
            raise ValueError("target_run_status must be policy_blocked")
        _validate_policy_denial_call_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )

    def __str__(self) -> str:
        return (
            f"classified data unsupported for capability {self.capability.value!r} "
            f"with llm_call_count={self.llm_call_count}"
        )


@dataclass(frozen=True, slots=True)
class ModelPolicyNotApprovedError(Exception):
    """Endpoint or CUI boundary policy has not approved the requested route."""

    error_code: str
    capability: ModelCapability
    target_run_status: str
    llm_call_count: int

    def __post_init__(self) -> None:
        if self.error_code != "model_policy_not_approved":
            raise ValueError("error_code must be model_policy_not_approved")
        if self.target_run_status != AnalysisRunStatus.POLICY_BLOCKED.value:
            raise ValueError("target_run_status must be policy_blocked")
        _validate_policy_denial_call_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )

    def __str__(self) -> str:
        return (
            f"model policy not approved for capability {self.capability.value!r} "
            f"with llm_call_count={self.llm_call_count}"
        )


@dataclass(frozen=True, slots=True)
class ProhibitedModelActionError(Exception):
    """Requested capability is disabled by product policy."""

    error_code: str
    capability: ModelCapability
    target_run_status: str
    llm_call_count: int

    def __post_init__(self) -> None:
        if self.error_code != "prohibited_model_action":
            raise ValueError("error_code must be prohibited_model_action")
        if self.target_run_status != AnalysisRunStatus.POLICY_BLOCKED.value:
            raise ValueError("target_run_status must be policy_blocked")
        _validate_policy_denial_call_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )

    def __str__(self) -> str:
        return (
            f"prohibited model action for capability {self.capability.value!r} "
            f"with llm_call_count={self.llm_call_count}"
        )


@dataclass(frozen=True, slots=True)
class ModelPolicyOrderingError(Exception):
    """Raised when model policy denial occurs after a prior model call."""

    capability: ModelCapability
    llm_call_count: int

    def __post_init__(self) -> None:
        count = _validate_nonnegative_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )
        if count == 0:
            raise ValueError("llm_call_count must be positive")

    def __str__(self) -> str:
        return "model policy ordering invariant violated"


@dataclass(frozen=True, slots=True)
class ModelCallLimitExceededError(Exception):
    """Configured per-run model call budget is exhausted."""

    error_code: str
    capability: ModelCapability
    target_run_status: str
    llm_call_count: int

    def __post_init__(self) -> None:
        if self.error_code != "model_call_limit_exceeded":
            raise ValueError("error_code must be model_call_limit_exceeded")
        if self.target_run_status != AnalysisRunStatus.FAILED.value:
            raise ValueError("target_run_status must be failed")
        _validate_nonnegative_count(
            self.llm_call_count,
            field_name="llm_call_count",
        )

    def __str__(self) -> str:
        return (
            f"model call limit exceeded for capability {self.capability.value!r} "
            f"with llm_call_count={self.llm_call_count}"
        )


_ROUTING_ERROR_EXCEPTIONS: dict[str, type[Exception]] = {
    "model_routing_denied": ModelRoutingDeniedError,
    "classified_data_unsupported": ClassifiedDataUnsupportedError,
    "model_policy_not_approved": ModelPolicyNotApprovedError,
}


def _validate_real_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    return value


def _validate_nonnegative_count(value: object, *, field_name: str) -> int:
    count = _validate_real_int(value, field_name=field_name)
    if count < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return count


def _validate_policy_denial_call_count(value: object, *, field_name: str) -> None:
    count = _validate_real_int(value, field_name=field_name)
    if count != 0:
        raise ValueError(f"{field_name} must be 0 for policy denial")


def _validate_enum_field(
    value: object,
    *,
    field_name: str,
    enum_type: type[StrEnum],
) -> None:
    if not isinstance(value, enum_type):
        article = "an" if enum_type.__name__.startswith(tuple("AEIOU")) else "a"
        raise TypeError(f"{field_name} must be {article} {enum_type.__name__}")


def _validate_bool_field(value: object, *, field_name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")


def _validate_request(request: ModelCallRequest) -> None:
    if not isinstance(request, ModelCallRequest):
        raise TypeError("request must be a ModelCallRequest")

    _validate_enum_field(
        request.capability,
        field_name="capability",
        enum_type=ModelCapability,
    )
    _validate_enum_field(
        request.data_origin,
        field_name="data_origin",
        enum_type=DataOrigin,
    )
    _validate_enum_field(
        request.sensitivity,
        field_name="sensitivity",
        enum_type=Sensitivity,
    )
    _validate_enum_field(
        request.endpoint_profile,
        field_name="endpoint_profile",
        enum_type=EndpointProfile,
    )
    _validate_bool_field(
        request.endpoint_policy_approved,
        field_name="endpoint_policy_approved",
    )
    _validate_bool_field(
        request.cui_boundary_approved,
        field_name="cui_boundary_approved",
    )
    _validate_bool_field(
        request.vision_model_enabled,
        field_name="vision_model_enabled",
    )
    _validate_nonnegative_count(
        request.current_llm_call_count,
        field_name="current_llm_call_count",
    )
    max_llm_calls = _validate_real_int(
        request.max_llm_calls,
        field_name="max_llm_calls",
    )
    if max_llm_calls <= 0:
        raise ValueError("max_llm_calls must be positive")


def _raise_routing_denial(
    *,
    error_code: str,
    capability: ModelCapability,
    llm_call_count: int,
) -> None:
    if llm_call_count > 0:
        raise ModelPolicyOrderingError(
            capability=capability,
            llm_call_count=llm_call_count,
        )

    exc_type = _ROUTING_ERROR_EXCEPTIONS[error_code]
    raise exc_type(
        error_code=error_code,
        capability=capability,
        target_run_status=AnalysisRunStatus.POLICY_BLOCKED.value,
        llm_call_count=0,
    )


def _raise_prohibited_action(
    *,
    capability: ModelCapability,
    llm_call_count: int,
) -> None:
    if llm_call_count > 0:
        raise ModelPolicyOrderingError(
            capability=capability,
            llm_call_count=llm_call_count,
        )

    raise ProhibitedModelActionError(
        error_code="prohibited_model_action",
        capability=capability,
        target_run_status=AnalysisRunStatus.POLICY_BLOCKED.value,
        llm_call_count=0,
    )


async def invoke_model_call[T](
    request: ModelCallRequest,
    callback: Callable[[], Awaitable[T]],
) -> ModelCallResult[T]:
    """Evaluate policy, then invoke ``callback`` exactly once when allowed."""
    _validate_request(request)

    routing = evaluate_model_routing(
        data_origin=request.data_origin,
        sensitivity=request.sensitivity,
        endpoint_profile=request.endpoint_profile,
        endpoint_policy_approved=request.endpoint_policy_approved,
        cui_boundary_approved=request.cui_boundary_approved,
    )
    if not routing.allowed:
        assert routing.error_code is not None
        _raise_routing_denial(
            error_code=routing.error_code,
            capability=request.capability,
            llm_call_count=request.current_llm_call_count,
        )

    if request.capability is ModelCapability.EMBEDDING:
        _raise_prohibited_action(
            capability=request.capability,
            llm_call_count=request.current_llm_call_count,
        )
    if (
        request.capability is ModelCapability.VISION_EXTRACTION
        and not request.vision_model_enabled
    ):
        _raise_prohibited_action(
            capability=request.capability,
            llm_call_count=request.current_llm_call_count,
        )

    if request.current_llm_call_count >= request.max_llm_calls:
        raise ModelCallLimitExceededError(
            error_code="model_call_limit_exceeded",
            capability=request.capability,
            target_run_status=AnalysisRunStatus.FAILED.value,
            llm_call_count=request.current_llm_call_count,
        )

    value = await callback()
    return ModelCallResult(
        value=value,
        llm_call_count=request.current_llm_call_count + 1,
    )
