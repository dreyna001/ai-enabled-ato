"""Tests for the FastAPI health boundary and Problem response plumbing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from ato_service.health import ReadinessChecks
from ato_service.main import create_app
from ato_service.problems import (
    DEFAULT_DETAILS,
    DEFAULT_RETRY_AFTER_SECONDS,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TYPE_BASE,
    ServiceProblem,
    build_problem,
    sanitize_detail,
)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"
FORMAT_CHECKER = FormatChecker()

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

ALL_OK_CHECKS: ReadinessChecks = {
    "database": "ok",
    "storage": "ok",
    "authority_manifest": "ok",
    "jobs": "ok",
    "configuration": "ok",
}


def _make_probe(checks: ReadinessChecks):
    async def probe() -> ReadinessChecks:
        return checks

    return probe


def _make_failing_probe(exc: Exception):
    async def probe() -> ReadinessChecks:
        raise exc

    return probe


def _make_asserting_probe() -> Any:
    async def probe() -> ReadinessChecks:
        raise AssertionError("readiness probe must not be called")

    return probe


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


def _problem_validator() -> Draft202012Validator:
    domain_schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    problem_schema = {
        **domain_schema["$defs"]["Problem"],
        "$defs": domain_schema["$defs"],
    }
    return Draft202012Validator(problem_schema, format_checker=FORMAT_CHECKER)


def _assert_problem_matches_contract(payload: dict[str, Any]) -> None:
    _problem_validator().validate(payload)


def test_liveness_reports_process_only() -> None:
    client = TestClient(create_app(readiness_probe=_make_asserting_probe()))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "checks": {"process": "ok"}}


def test_readiness_all_ok() -> None:
    client = TestClient(create_app(readiness_probe=_make_probe(ALL_OK_CHECKS)))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok", "checks": ALL_OK_CHECKS}


@pytest.mark.parametrize(
    ("failing_check", "expected_code", "expected_detail"),
    [
        ("database", "database_unavailable", DEFAULT_DETAILS["database_unavailable"]),
        ("storage", "storage_unavailable", DEFAULT_DETAILS["storage_unavailable"]),
        ("authority_manifest", "reconciliation_required", DEFAULT_DETAILS["reconciliation_required"]),
        ("jobs", "reconciliation_required", DEFAULT_DETAILS["reconciliation_required"]),
        ("configuration", "reconciliation_required", DEFAULT_DETAILS["reconciliation_required"]),
    ],
)
def test_readiness_unavailable_maps_to_stable_problem(
    failing_check: str,
    expected_code: str,
    expected_detail: str,
) -> None:
    checks = dict(ALL_OK_CHECKS)
    checks[failing_check] = "unavailable"
    client = TestClient(create_app(readiness_probe=_make_probe(checks)))

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == expected_code
    assert payload["status"] == 503
    assert payload["detail"] == expected_detail
    assert payload["title"]
    assert payload["type"] == f"{PROBLEM_TYPE_BASE}{expected_code}"
    assert payload["instance"] == "/health/ready"
    assert payload["field_errors"] == []
    assert payload["retryable"] is True
    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)


@pytest.mark.parametrize("failing_check", ["authority_manifest", "jobs", "configuration"])
def test_readiness_degraded_maps_to_reconciliation_required(
    failing_check: str,
) -> None:
    checks = dict(ALL_OK_CHECKS)
    checks[failing_check] = "degraded"
    client = TestClient(create_app(readiness_probe=_make_probe(checks)))

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == "reconciliation_required"
    assert payload["detail"] == DEFAULT_DETAILS["reconciliation_required"]
    assert payload["retryable"] is True
    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)


def test_readiness_probe_exception_returns_problem_not_500() -> None:
    client = TestClient(
        create_app(
            readiness_probe=_make_failing_probe(
                RuntimeError("secret DSN at C:\\secrets\\db.txt")
            )
        ),
        raise_server_exceptions=False,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == "reconciliation_required"
    assert payload["retryable"] is True
    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)
    assert "secret" not in payload["detail"].lower()
    assert "C:\\" not in payload["detail"]
    _assert_problem_matches_contract(payload)


@pytest.mark.parametrize(
    "checks",
    [
        {"database": "ok", "storage": "ok"},
        {
            "database": "ok",
            "storage": "ok",
            "authority_manifest": "ok",
            "jobs": "ok",
            "configuration": "unknown",
        },
    ],
)
def test_readiness_malformed_check_output_returns_problem(
    checks: dict[str, str],
) -> None:
    client = TestClient(create_app(readiness_probe=_make_probe(checks)))  # type: ignore[arg-type]

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == "reconciliation_required"
    assert payload["retryable"] is True
    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)
    _assert_problem_matches_contract(payload)


def test_database_unavailable_takes_priority_over_storage() -> None:
    checks = dict(ALL_OK_CHECKS)
    checks["database"] = "unavailable"
    checks["storage"] = "unavailable"
    client = TestClient(create_app(readiness_probe=_make_probe(checks)))

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == "database_unavailable"


def test_problem_detail_is_sanitized() -> None:
    raw = (
        "Dependency failed at C:\\secrets\\db.log and /var/lib/ato/data\n"
        "Traceback (most recent call last):\n  File \"main.py\""
    )
    sanitized = sanitize_detail(raw)
    assert "Traceback" not in sanitized
    assert "C:\\secrets" not in sanitized
    assert "/var/lib" not in sanitized
    assert "[path]" in sanitized


def test_service_problem_handler_returns_problem_json() -> None:
    app = create_app(readiness_probe=_make_probe(ALL_OK_CHECKS))

    @app.get("/raise-problem")
    async def raise_problem() -> None:
        raise ServiceProblem(
            error_code="reconciliation_required",
            status=503,
            instance="/raise-problem",
            retryable=True,
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/raise-problem")

    assert response.status_code == 503
    payload = _problem_payload(response)
    assert payload["error_code"] == "reconciliation_required"
    assert payload["instance"] == "/raise-problem"
    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)


def test_openapi_health_operation_ids_and_responses() -> None:
    app = create_app(readiness_probe=_make_probe(ALL_OK_CHECKS))
    schema = app.openapi()

    live = schema["paths"]["/health/live"]["get"]
    assert live["operationId"] == "getLiveness"
    assert live["security"] == []
    assert live["responses"]["200"]["content"]["application/json"]
    assert live["responses"]["default"]["content"]["application/problem+json"]
    assert [server["url"] for server in live["servers"]] == ["/"]

    ready = schema["paths"]["/health/ready"]["get"]
    assert ready["operationId"] == "getReadiness"
    assert ready["security"] == []
    assert ready["responses"]["200"]["content"]["application/json"]
    assert ready["responses"]["503"]["content"]["application/problem+json"]
    assert ready["responses"]["default"]["content"]["application/problem+json"]
    assert [server["url"] for server in ready["servers"]] == ["/"]


def test_build_problem_request_id_is_uuid() -> None:
    problem = build_problem(
        error_code="storage_unavailable",
        status=503,
        instance="/health/ready",
        request_id=UUID("a2d9dc6f-b291-4d9f-b297-5cb5b45cc791"),
        retryable=True,
    )
    assert problem.request_id == UUID("a2d9dc6f-b291-4d9f-b297-5cb5b45cc791")
    assert problem.field_errors == []


def test_retryable_503_problem_includes_retry_after_header() -> None:
    from ato_service.problems import problem_json_response

    problem = build_problem(
        error_code="database_unavailable",
        status=503,
        instance="/health/ready",
        request_id=UUID("a2d9dc6f-b291-4d9f-b297-5cb5b45cc791"),
        retryable=True,
    )
    response = problem_json_response(problem)

    assert response.headers["Retry-After"] == str(DEFAULT_RETRY_AFTER_SECONDS)


def test_non_retryable_problem_omits_retry_after_header() -> None:
    from ato_service.problems import problem_json_response

    problem = build_problem(
        error_code="reconciliation_required",
        status=503,
        instance="/health/ready",
        request_id=UUID("a2d9dc6f-b291-4d9f-b297-5cb5b45cc791"),
        retryable=False,
    )
    response = problem_json_response(problem)

    assert "Retry-After" not in response.headers
