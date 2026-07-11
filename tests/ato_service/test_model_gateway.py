"""Tests for policy-ordered model gateway behavior."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ato_service.lifecycle_transitions import AnalysisRunStatus
from ato_service.model_gateway import (
    ClassifiedDataUnsupportedError,
    ModelCallLimitExceededError,
    ModelCallRequest,
    ModelCapability,
    ModelPolicyNotApprovedError,
    ModelPolicyOrderingError,
    ModelRoutingDeniedError,
    ModelStepType,
    ProhibitedModelActionError,
    invoke_model_call,
)
from ato_service.model_routing import (
    DataOrigin,
    EndpointProfile,
    Sensitivity,
    evaluate_model_routing,
)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"

ALL_MODEL_STEP_CAPABILITIES = tuple(
    ModelCapability(step_type.value) for step_type in ModelStepType
)
ROUTING_CAPABILITIES = ALL_MODEL_STEP_CAPABILITIES + (ModelCapability.VISION_EXTRACTION,)


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _assert_internal_ordering_error(
    error: ModelPolicyOrderingError,
    *,
    capability: ModelCapability,
    llm_call_count: int,
) -> None:
    assert error.capability is capability
    assert error.llm_call_count == llm_call_count
    assert str(error) == "model policy ordering invariant violated"
    assert not hasattr(error, "error_code")
    assert not hasattr(error, "target_run_status")


def _request(
    *,
    capability: ModelCapability,
    data_origin: DataOrigin = DataOrigin.SYNTHETIC,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    endpoint_profile: EndpointProfile = EndpointProfile.MOCK,
    endpoint_policy_approved: bool = False,
    cui_boundary_approved: bool = False,
    vision_model_enabled: bool = True,
    current_llm_call_count: int = 0,
    max_llm_calls: int = 120,
) -> ModelCallRequest:
    return ModelCallRequest(
        capability=capability,
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=endpoint_policy_approved,
        cui_boundary_approved=cui_boundary_approved,
        vision_model_enabled=vision_model_enabled,
        current_llm_call_count=current_llm_call_count,
        max_llm_calls=max_llm_calls,
    )


async def _invoke(
    request: ModelCallRequest,
    *,
    return_value: Any = "ok",
) -> tuple[Any, int]:
    callback = AsyncMock(return_value=return_value)
    result = await invoke_model_call(request, callback)
    callback.assert_awaited_once()
    return result.value, result.llm_call_count


@pytest.mark.parametrize("capability", ALL_MODEL_STEP_CAPABILITIES)
def test_allowed_model_step_capability_invokes_callback_once(
    capability: ModelCapability,
) -> None:
    value, call_count = _run(_invoke(_request(capability=capability)))
    assert value == "ok"
    assert call_count == 1


def test_allowed_vision_extraction_invokes_callback_once() -> None:
    value, call_count = _run(
        _invoke(_request(capability=ModelCapability.VISION_EXTRACTION))
    )
    assert value == "ok"
    assert call_count == 1


def test_allowed_route_with_disabled_vision_fails_closed_with_zero_calls() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.VISION_EXTRACTION,
        vision_model_enabled=False,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ProhibitedModelActionError) as exc_info:
            _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "prohibited_model_action"
    assert exc.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    assert exc.llm_call_count == 0
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_disabled_vision_after_model_call_raises_internal_invariant() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.VISION_EXTRACTION,
        vision_model_enabled=False,
        current_llm_call_count=2,
        max_llm_calls=2,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ModelPolicyOrderingError) as exc_info:
            _run(invoke_model_call(request, callback))

    _assert_internal_ordering_error(
        exc_info.value,
        capability=ModelCapability.VISION_EXTRACTION,
        llm_call_count=2,
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
def test_classified_data_denied_with_zero_calls(capability: ModelCapability) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=capability,
        sensitivity=Sensitivity.CLASSIFIED,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )

    with pytest.raises(ClassifiedDataUnsupportedError) as exc_info:
        _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "classified_data_unsupported"
    assert exc.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    assert exc.llm_call_count == 0
    assert exc.capability is capability
    callback.assert_not_awaited()


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
def test_unknown_sensitivity_denied_with_zero_calls(
    capability: ModelCapability,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=capability,
        sensitivity=Sensitivity.UNKNOWN,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )

    with pytest.raises(ModelRoutingDeniedError) as exc_info:
        _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "model_routing_denied"
    assert exc.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    assert exc.llm_call_count == 0
    callback.assert_not_awaited()


def test_nonzero_routing_denial_raises_internal_invariant_before_budget() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        sensitivity=Sensitivity.UNKNOWN,
        current_llm_call_count=3,
        max_llm_calls=3,
    )

    with pytest.raises(ModelPolicyOrderingError) as exc_info:
        _run(invoke_model_call(request, callback))

    _assert_internal_ordering_error(
        exc_info.value,
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        llm_call_count=3,
    )
    callback.assert_not_awaited()


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
def test_external_customer_production_denied_with_zero_calls(
    capability: ModelCapability,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=capability,
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )

    with pytest.raises(ModelRoutingDeniedError):
        _run(invoke_model_call(request, callback))

    callback.assert_not_awaited()


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.CUSTOMER_SENSITIVE, Sensitivity.CUI],
)
def test_external_sensitive_labels_denied_without_bypass(
    capability: ModelCapability,
    sensitivity: Sensitivity,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=capability,
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )

    with pytest.raises(ModelRoutingDeniedError):
        _run(invoke_model_call(request, callback))

    callback.assert_not_awaited()


@pytest.mark.parametrize("capability", ROUTING_CAPABILITIES)
def test_internal_customer_production_requires_policy(
    capability: ModelCapability,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=capability,
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        endpoint_profile=EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
    )

    with pytest.raises(ModelPolicyNotApprovedError) as exc_info:
        _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "model_policy_not_approved"
    assert exc.llm_call_count == 0
    assert exc.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    callback.assert_not_awaited()


def test_allowed_route_with_embedding_fails_closed_without_callback() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(capability=ModelCapability.EMBEDDING)

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ProhibitedModelActionError) as exc_info:
            _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "prohibited_model_action"
    assert exc.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    assert exc.llm_call_count == 0
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_classified_embedding_uses_routing_denial_precedence() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.EMBEDDING,
        sensitivity=Sensitivity.CLASSIFIED,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ClassifiedDataUnsupportedError) as exc_info:
            _run(invoke_model_call(request, callback))

    assert exc_info.value.error_code == "classified_data_unsupported"
    assert exc_info.value.llm_call_count == 0
    assert (
        exc_info.value.target_run_status
        == AnalysisRunStatus.POLICY_BLOCKED.value
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_classified_disabled_vision_uses_routing_denial_precedence() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.VISION_EXTRACTION,
        sensitivity=Sensitivity.CLASSIFIED,
        vision_model_enabled=False,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ClassifiedDataUnsupportedError) as exc_info:
            _run(invoke_model_call(request, callback))

    assert exc_info.value.error_code == "classified_data_unsupported"
    assert exc_info.value.llm_call_count == 0
    assert (
        exc_info.value.target_run_status
        == AnalysisRunStatus.POLICY_BLOCKED.value
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_unknown_embedding_uses_routing_denial_precedence() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.EMBEDDING,
        sensitivity=Sensitivity.UNKNOWN,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ModelRoutingDeniedError) as exc_info:
            _run(invoke_model_call(request, callback))

    assert exc_info.value.error_code == "model_routing_denied"
    assert exc_info.value.llm_call_count == 0
    assert (
        exc_info.value.target_run_status
        == AnalysisRunStatus.POLICY_BLOCKED.value
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_unknown_disabled_vision_uses_routing_denial_precedence() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.VISION_EXTRACTION,
        sensitivity=Sensitivity.UNKNOWN,
        vision_model_enabled=False,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ModelRoutingDeniedError) as exc_info:
            _run(invoke_model_call(request, callback))

    assert exc_info.value.error_code == "model_routing_denied"
    assert exc_info.value.llm_call_count == 0
    assert (
        exc_info.value.target_run_status
        == AnalysisRunStatus.POLICY_BLOCKED.value
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_nonzero_embedding_denial_raises_internal_invariant_before_budget() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.EMBEDDING,
        current_llm_call_count=4,
        max_llm_calls=4,
    )

    with patch(
        "ato_service.model_gateway.evaluate_model_routing",
        wraps=evaluate_model_routing,
    ) as routing:
        with pytest.raises(ModelPolicyOrderingError) as exc_info:
            _run(invoke_model_call(request, callback))

    _assert_internal_ordering_error(
        exc_info.value,
        capability=ModelCapability.EMBEDDING,
        llm_call_count=4,
    )
    callback.assert_not_awaited()
    routing.assert_called_once()


def test_call_limit_exceeded_makes_zero_extra_calls() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        current_llm_call_count=1,
        max_llm_calls=1,
    )

    with pytest.raises(ModelCallLimitExceededError) as exc_info:
        _run(invoke_model_call(request, callback))

    exc = exc_info.value
    assert exc.error_code == "model_call_limit_exceeded"
    assert exc.target_run_status == AnalysisRunStatus.FAILED.value
    assert exc.llm_call_count == 1
    callback.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "current_llm_call_count",
        "max_llm_calls",
        "expected_exception",
        "message",
    ),
    [
        (-1, 10, ValueError, "current_llm_call_count must be nonnegative"),
        (0, 0, ValueError, "max_llm_calls must be positive"),
        (-2, -1, ValueError, "current_llm_call_count must be nonnegative"),
        (False, 10, TypeError, "current_llm_call_count must be an int"),
        ("0", 10, TypeError, "current_llm_call_count must be an int"),
        (0, True, TypeError, "max_llm_calls must be an int"),
        (0, 1.5, TypeError, "max_llm_calls must be an int"),
    ],
)
def test_invalid_call_counters_reject_before_callback(
    current_llm_call_count: Any,
    max_llm_calls: Any,
    expected_exception: type[Exception],
    message: str,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.PACKAGE_CHAT,
        current_llm_call_count=current_llm_call_count,
        max_llm_calls=max_llm_calls,
    )

    with pytest.raises(expected_exception, match=message):
        _run(invoke_model_call(request, callback))

    callback.assert_not_awaited()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("capability", "package_chat", "capability must be a ModelCapability"),
        ("data_origin", "synthetic", "data_origin must be a DataOrigin"),
        ("sensitivity", "public", "sensitivity must be a Sensitivity"),
        (
            "endpoint_profile",
            "mock",
            "endpoint_profile must be an EndpointProfile",
        ),
        (
            "endpoint_policy_approved",
            1,
            "endpoint_policy_approved must be a bool",
        ),
        (
            "cui_boundary_approved",
            "false",
            "cui_boundary_approved must be a bool",
        ),
        (
            "vision_model_enabled",
            1,
            "vision_model_enabled must be a bool",
        ),
    ],
)
def test_malformed_request_fields_reject_before_callback(
    field_name: str,
    invalid_value: object,
    message: str,
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = replace(
        _request(capability=ModelCapability.PACKAGE_CHAT),
        **{field_name: invalid_value},
    )

    with pytest.raises(TypeError, match=message):
        _run(invoke_model_call(request, callback))

    callback.assert_not_awaited()


def test_allowed_call_increments_llm_call_count() -> None:
    request = _request(
        capability=ModelCapability.SUFFICIENCY_MATRIX,
        current_llm_call_count=4,
        max_llm_calls=10,
    )
    _, call_count = _run(_invoke(request, return_value={"rows": []}))
    assert call_count == 5


async def _routing_evaluated_before_callback_on_policy_denial() -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        sensitivity=Sensitivity.UNKNOWN,
    )
    routing_calls: list[tuple[str, str, str, bool, bool]] = []

    def _recording_routing(**kwargs: Any) -> Any:
        routing_calls.append(
            (
                kwargs["data_origin"].value,
                kwargs["sensitivity"].value,
                kwargs["endpoint_profile"].value,
                kwargs["endpoint_policy_approved"],
                kwargs["cui_boundary_approved"],
            )
        )
        return evaluate_model_routing(**kwargs)

    with patch("ato_service.model_gateway.evaluate_model_routing", _recording_routing):
        with pytest.raises(ModelRoutingDeniedError):
            await invoke_model_call(request, callback)

    assert routing_calls == [
        (
            request.data_origin.value,
            request.sensitivity.value,
            request.endpoint_profile.value,
            request.endpoint_policy_approved,
            request.cui_boundary_approved,
        )
    ]
    callback.assert_not_awaited()


def test_routing_evaluated_before_callback_on_policy_denial() -> None:
    _run(_routing_evaluated_before_callback_on_policy_denial())


async def _routing_evaluated_before_callback_on_allowed_call() -> None:
    callback = AsyncMock(return_value="done")
    request = _request(capability=ModelCapability.OCR_SUMMARY)
    routing_calls: list[str] = []

    def _recording_routing(**kwargs: Any) -> Any:
        routing_calls.append(kwargs["sensitivity"].value)
        return evaluate_model_routing(**kwargs)

    with patch("ato_service.model_gateway.evaluate_model_routing", _recording_routing):
        result = await invoke_model_call(request, callback)

    assert routing_calls == [request.sensitivity.value]
    callback.assert_awaited_once()
    assert result.llm_call_count == 1


def test_routing_evaluated_before_callback_on_allowed_call() -> None:
    _run(_routing_evaluated_before_callback_on_allowed_call())


def test_model_step_type_values_match_domain_schema() -> None:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    step_type_enum = schema["$defs"]["ModelStep"]["properties"]["step_type"]["enum"]
    assert [member.value for member in ModelStepType] == step_type_enum


def test_model_capability_includes_all_model_step_types_plus_extras() -> None:
    step_values = {member.value for member in ModelStepType}
    capability_values = {member.value for member in ModelCapability}
    assert step_values.issubset(capability_values)
    assert capability_values - step_values == {
        ModelCapability.VISION_EXTRACTION.value,
        ModelCapability.EMBEDDING.value,
    }


@pytest.mark.parametrize(
    (
        "data_origin",
        "sensitivity",
        "endpoint_profile",
        "policy",
        "cui",
        "expected_exception",
    ),
    [
        (
            DataOrigin.SYNTHETIC,
            Sensitivity.CLASSIFIED,
            EndpointProfile.MOCK,
            True,
            True,
            ClassifiedDataUnsupportedError,
        ),
        (
            DataOrigin.SYNTHETIC,
            Sensitivity.UNKNOWN,
            EndpointProfile.MOCK,
            True,
            True,
            ModelRoutingDeniedError,
        ),
        (
            DataOrigin.CUSTOMER_PRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.EXTERNAL_OPENAI,
            True,
            True,
            ModelRoutingDeniedError,
        ),
        (
            DataOrigin.CUSTOMER_PRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
            False,
            False,
            ModelPolicyNotApprovedError,
        ),
        (
            DataOrigin.REDACTED_NONPRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.EXTERNAL_OPENAI,
            False,
            False,
            ModelPolicyNotApprovedError,
        ),
    ],
)
def test_gateway_routing_matrix_edges_raise_without_callback(
    data_origin: DataOrigin,
    sensitivity: Sensitivity,
    endpoint_profile: EndpointProfile,
    policy: bool,
    cui: bool,
    expected_exception: type[Exception],
) -> None:
    callback = AsyncMock(return_value="must-not-run")
    request = _request(
        capability=ModelCapability.CONSISTENCY_BRIEF,
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=policy,
        cui_boundary_approved=cui,
    )

    with pytest.raises(expected_exception) as exc_info:
        _run(invoke_model_call(request, callback))

    assert exc_info.value.llm_call_count == 0
    assert exc_info.value.target_run_status == AnalysisRunStatus.POLICY_BLOCKED.value
    callback.assert_not_awaited()


def test_domain_exceptions_reject_invalid_error_codes() -> None:
    with pytest.raises(ValueError, match="error_code must be model_routing_denied"):
        ModelRoutingDeniedError(
            error_code="classified_data_unsupported",
            capability=ModelCapability.PACKAGE_CHAT,
            target_run_status=AnalysisRunStatus.POLICY_BLOCKED.value,
            llm_call_count=0,
        )

    with pytest.raises(ValueError, match="llm_call_count must be 0 for policy denial"):
        ModelRoutingDeniedError(
            error_code="model_routing_denied",
            capability=ModelCapability.PACKAGE_CHAT,
            target_run_status=AnalysisRunStatus.POLICY_BLOCKED.value,
            llm_call_count=1,
        )

    with pytest.raises(ValueError, match="target_run_status must be failed"):
        ModelCallLimitExceededError(
            error_code="model_call_limit_exceeded",
            capability=ModelCapability.PACKAGE_CHAT,
            target_run_status=AnalysisRunStatus.POLICY_BLOCKED.value,
            llm_call_count=1,
        )
