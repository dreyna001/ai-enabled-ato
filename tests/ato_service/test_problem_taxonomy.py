"""HTTP Problem mapping tests for P0 typed domain errors."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from ato_service.health import ReadinessChecks
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    require_analysis_run_transition,
)
from ato_service.main import create_app
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.model_gateway import (
    ModelCallRequest,
    ModelCapability,
    invoke_model_call,
)
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.problems import (
    DEFAULT_DETAILS,
    ERROR_TITLES,
    KNOWN_ERROR_CODES,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TYPE_BASE,
)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
LIFECYCLE_ERRORS_PATH = ROOT / "docs" / "contracts" / "LIFECYCLE_AND_ERRORS.md"
FORMAT_CHECKER = FormatChecker()

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

P0_TYPED_ERROR_CODES = frozenset(
    {
        "illegal_state_transition",
        "model_routing_denied",
        "classified_data_unsupported",
        "model_policy_not_approved",
        "prohibited_model_action",
        "model_call_limit_exceeded",
        "matrix_coverage_invalid",
    }
)

ALL_OK_CHECKS: ReadinessChecks = {
    "database": "ok",
    "storage": "ok",
    "authority_manifest": "ok",
    "jobs": "ok",
    "configuration": "ok",
}


async def _asserting_probe() -> ReadinessChecks:
    raise AssertionError("readiness probe must not be called")


def _problem_validator() -> Draft202012Validator:
    domain_schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    problem_schema = {
        **domain_schema["$defs"]["Problem"],
        "$defs": domain_schema["$defs"],
    }
    return Draft202012Validator(problem_schema, format_checker=FORMAT_CHECKER)


def _assert_problem_matches_contract(payload: dict[str, Any]) -> None:
    _problem_validator().validate(payload)


def _problem_payload(response) -> dict[str, Any]:
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    payload = response.json()
    assert set(payload) == {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "error_code",
        "request_id",
        "field_errors",
        "retryable",
    }
    return payload


def _assert_nonretryable_problem(
    response,
    *,
    expected_status: int,
    expected_code: str,
    expected_instance: str,
) -> dict[str, Any]:
    assert response.status_code == expected_status
    payload = _problem_payload(response)
    assert payload["error_code"] == expected_code
    assert payload["status"] == expected_status
    assert payload["title"] == ERROR_TITLES[expected_code]
    assert payload["detail"] == DEFAULT_DETAILS[expected_code]
    assert payload["type"] == f"{PROBLEM_TYPE_BASE}{expected_code}"
    assert payload["instance"] == expected_instance
    assert payload["field_errors"] == []
    assert payload["retryable"] is False
    assert "Retry-After" not in response.headers
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)
    return payload


def _create_base_app() -> FastAPI:
    return create_app(readiness_probe=_asserting_probe)


@pytest.fixture
def run_id() -> str:
    return "2f356c35-9fda-4d60-a90e-7d42cdfe5d34"


def test_p0_typed_error_codes_are_in_known_registry() -> None:
    assert P0_TYPED_ERROR_CODES <= KNOWN_ERROR_CODES


def test_p0_typed_error_codes_are_documented_in_lifecycle_taxonomy() -> None:
    lifecycle = LIFECYCLE_ERRORS_PATH.read_text(encoding="utf-8")
    section_start = lifecycle.index("### 4.1")
    section_end = lifecycle.index("## 5.")
    section = lifecycle[section_start:section_end]
    documented = set(
        re.findall(
            r"^\| `([a-z][a-z0-9_]{2,127})` \|",
            section,
            re.MULTILINE,
        )
    )
    undocumented = P0_TYPED_ERROR_CODES - documented
    assert not undocumented, (
        "P0 typed Problem error_code values must appear in "
        f"LIFECYCLE_AND_ERRORS.md Section 4: {sorted(undocumented)}"
    )


def test_illegal_state_transition_returns_409_and_preserves_state(
    run_id: str,
) -> None:
    app = _create_base_app()
    run_states = {run_id: AnalysisRunStatus.SUCCEEDED}

    @app.post("/api/v1/runs/{requested_run_id}/cancel")
    async def cancel_run(requested_run_id: str) -> None:
        require_analysis_run_transition(
            run_states[requested_run_id],
            AnalysisRunStatus.CANCELLED,
            condition=AnalysisRunTransitionCondition.AUTHORIZED_CANCELLATION,
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = f"/api/v1/runs/{run_id}/cancel"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=409,
        expected_code="illegal_state_transition",
        expected_instance=path,
    )
    assert run_states[run_id] is AnalysisRunStatus.SUCCEEDED
    assert "succeeded" not in payload["detail"].lower()
    assert "cancelled" not in payload["detail"].lower()
    assert run_id not in payload["detail"]


@pytest.mark.parametrize(
    ("expected_code", "request_factory", "path_suffix"),
    [
        (
            "model_routing_denied",
            lambda: ModelCallRequest(
                capability=ModelCapability.PACKAGE_CHAT,
                data_origin=DataOrigin.SYNTHETIC,
                sensitivity=Sensitivity.UNKNOWN,
                endpoint_profile=EndpointProfile.MOCK,
                endpoint_policy_approved=True,
                cui_boundary_approved=True,
                vision_model_enabled=True,
                current_llm_call_count=0,
                max_llm_calls=120,
            ),
            "routing-denied",
        ),
        (
            "classified_data_unsupported",
            lambda: ModelCallRequest(
                capability=ModelCapability.PACKAGE_CHAT,
                data_origin=DataOrigin.SYNTHETIC,
                sensitivity=Sensitivity.CLASSIFIED,
                endpoint_profile=EndpointProfile.MOCK,
                endpoint_policy_approved=True,
                cui_boundary_approved=True,
                vision_model_enabled=True,
                current_llm_call_count=0,
                max_llm_calls=120,
            ),
            "classified",
        ),
        (
            "model_policy_not_approved",
            lambda: ModelCallRequest(
                capability=ModelCapability.PACKAGE_CHAT,
                data_origin=DataOrigin.CUSTOMER_PRODUCTION,
                sensitivity=Sensitivity.PUBLIC,
                endpoint_profile=EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
                endpoint_policy_approved=False,
                cui_boundary_approved=False,
                vision_model_enabled=True,
                current_llm_call_count=0,
                max_llm_calls=120,
            ),
            "policy-not-approved",
        ),
        (
            "prohibited_model_action",
            lambda: ModelCallRequest(
                capability=ModelCapability.EMBEDDING,
                data_origin=DataOrigin.SYNTHETIC,
                sensitivity=Sensitivity.PUBLIC,
                endpoint_profile=EndpointProfile.MOCK,
                endpoint_policy_approved=True,
                cui_boundary_approved=True,
                vision_model_enabled=True,
                current_llm_call_count=0,
                max_llm_calls=120,
            ),
            "prohibited",
        ),
    ],
)
def test_model_gateway_policy_errors_return_403_without_callback(
    expected_code: str,
    request_factory: Callable[[], ModelCallRequest],
    path_suffix: str,
) -> None:
    app = _create_base_app()
    callback = AsyncMock(return_value="must-not-run")

    @app.post("/api/v1/package-revisions/{revision_id}/chat")
    async def package_chat(revision_id: str) -> None:
        request = request_factory()
        await invoke_model_call(request, callback)

    client = TestClient(app, raise_server_exceptions=False)
    path = f"/api/v1/package-revisions/{path_suffix}/chat"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=403,
        expected_code=expected_code,
        expected_instance=path,
    )
    callback.assert_not_awaited()
    for leaked in ("package_chat", "llm_call_count", "policy_blocked", "unknown"):
        assert leaked not in payload["detail"]


def test_model_call_limit_exceeded_returns_422_without_callback(
    run_id: str,
) -> None:
    app = _create_base_app()
    callback = AsyncMock(return_value="must-not-run")

    @app.post("/api/v1/package-revisions/{revision_id}/chat")
    async def package_chat(revision_id: str) -> None:
        request = ModelCallRequest(
            capability=ModelCapability.PACKAGE_CHAT,
            data_origin=DataOrigin.SYNTHETIC,
            sensitivity=Sensitivity.PUBLIC,
            endpoint_profile=EndpointProfile.MOCK,
            endpoint_policy_approved=True,
            cui_boundary_approved=True,
            vision_model_enabled=True,
            current_llm_call_count=1,
            max_llm_calls=1,
        )
        await invoke_model_call(request, callback)

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/limit-exceeded/chat"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="model_call_limit_exceeded",
        expected_instance=path,
    )
    callback.assert_not_awaited()
    assert "1" not in payload["detail"]
    assert run_id not in payload["detail"]


def test_matrix_coverage_invalid_returns_422_without_exposing_ids(
    run_id: str,
) -> None:
    app = _create_base_app()

    @app.get("/api/v1/runs/{requested_run_id}/matrix")
    async def get_run_matrix(requested_run_id: str) -> None:
        require_exact_matrix_coverage(
            ["AC-1", "AC-2", "IA-5"],
            ["AC-1", "AC-2"],
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = f"/api/v1/runs/{run_id}/matrix"
    response = client.get(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="matrix_coverage_invalid",
        expected_instance=path,
    )
    assert "IA-5" not in payload["detail"]
    assert "AC-1" not in payload["detail"]
    assert run_id not in payload["detail"]
