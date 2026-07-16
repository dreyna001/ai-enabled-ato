"""HTTP Problem mapping tests for P0 and P1.1 typed domain errors."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, Field

from ato_service.auth_context import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    CsrfValidationError,
)
from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
from ato_service.health import ReadinessChecks
from ato_service.idempotency import (
    IdempotencyConflictError,
    IdempotencyValidationError,
)
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
from ato_service.package_revisions import (
    PackageRevisionNotFoundError,
    PackageRevisionStorageError,
    PackageRevisionValidationError,
    ParentRevisionNotFoundError,
    SystemNotFoundError,
    UnconfirmedFactProposalsError,
)
from ato_service.pagination import InvalidPageLimitError, InvalidPaginationCursorError
from ato_service.problems import (
    DEFAULT_DETAILS,
    ERROR_TITLES,
    KNOWN_ERROR_CODES,
    PROBLEM_MEDIA_TYPE,
    PROBLEM_TYPE_BASE,
    register_problem_handlers,
)
from ato_service.source_artifacts import (
    DuplicateSourceArtifactError,
    PackageLimitExceededError,
    RequestSchemaInvalidError as SourceRequestSchemaInvalidError,
    ResourceNotFoundError as SourceResourceNotFoundError,
    SourceArtifactStorageError,
    SourceSizeLimitExceededError,
    SourceTypeMismatchError,
    UnsupportedMediaTypeError,
)
from ato_service.systems import (
    FieldValidationError,
    RequestSchemaInvalidError as SystemRequestSchemaInvalidError,
    ResourceNotFoundError as SystemResourceNotFoundError,
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

P1_1_TYPED_ERROR_CODES = frozenset(
    {
        "authentication_required",
        "authorization_denied",
        "csrf_validation_failed",
        "resource_not_found",
        "request_schema_invalid",
        "malformed_request",
        "unsupported_media_type",
        "source_size_limit_exceeded",
        "package_limit_exceeded",
        "source_type_mismatch",
        "duplicate_canonical_id",
        "unconfirmed_fact_proposals",
        "idempotency_key_conflict",
        "idempotency_key_required",
        "if_match_required",
        "etag_mismatch",
        "artifact_digest_mismatch",
        "state_artifact_inconsistent",
    }
)

SENSITIVE_LEAK_MARKERS = (
    "2f356c35-9fda-4d60-a90e-7d42cdfe5d34",
    "secret-owner-group",
    "/var/lib/ato/secrets",
    "C:\\secrets\\package.db",
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
    expected_detail: str | None = None,
    expected_field_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    assert response.status_code == expected_status
    payload = _problem_payload(response)
    assert payload["error_code"] == expected_code
    assert payload["status"] == expected_status
    assert payload["title"] == ERROR_TITLES[expected_code]
    assert payload["detail"] == (
        expected_detail if expected_detail is not None else DEFAULT_DETAILS[expected_code]
    )
    assert payload["type"] == f"{PROBLEM_TYPE_BASE}{expected_code}"
    assert payload["instance"] == expected_instance
    assert payload["field_errors"] == (expected_field_errors or [])
    assert payload["retryable"] is False
    assert "Retry-After" not in response.headers
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)
    return payload


def _assert_problem(
    response,
    *,
    expected_status: int,
    expected_code: str,
    expected_instance: str,
    retryable: bool,
    field_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    assert response.status_code == expected_status
    payload = _problem_payload(response)
    assert payload["error_code"] == expected_code
    assert payload["status"] == expected_status
    assert payload["title"] == ERROR_TITLES[expected_code]
    assert payload["detail"] == DEFAULT_DETAILS[expected_code]
    assert payload["type"] == f"{PROBLEM_TYPE_BASE}{expected_code}"
    assert payload["instance"] == expected_instance
    assert payload["retryable"] is retryable
    if field_errors is None:
        assert payload["field_errors"] == []
    else:
        assert payload["field_errors"] == field_errors
    if retryable and expected_status in {429, 503}:
        assert "Retry-After" in response.headers
    else:
        assert "Retry-After" not in response.headers
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)
    return payload


def _assert_no_sensitive_leaks(payload: dict[str, Any]) -> None:
    leak_targets = [
        payload["detail"],
        payload["title"],
        *(
            f"{item['path']} {item['code']} {item['message']}"
            for item in payload["field_errors"]
        ),
    ]
    serialized = "\n".join(leak_targets)
    for marker in SENSITIVE_LEAK_MARKERS:
        assert marker not in serialized
        assert marker.lower() not in serialized.lower()


def _create_base_app() -> FastAPI:
    return create_app(readiness_probe=_asserting_probe)


def _create_problem_test_app() -> FastAPI:
    """Minimal app for P1.1 handler tests without package route dependencies."""
    app = FastAPI()
    register_problem_handlers(app)
    return app


@pytest.fixture
def run_id() -> str:
    return "2f356c35-9fda-4d60-a90e-7d42cdfe5d34"


def test_p0_typed_error_codes_are_in_known_registry() -> None:
    assert P0_TYPED_ERROR_CODES <= KNOWN_ERROR_CODES


def test_p1_1_typed_error_codes_are_in_known_registry() -> None:
    assert P1_1_TYPED_ERROR_CODES <= KNOWN_ERROR_CODES


def test_p1_1_typed_error_codes_are_documented_in_lifecycle_taxonomy() -> None:
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
    undocumented = P1_1_TYPED_ERROR_CODES - documented
    assert not undocumented, (
        "P1.1 typed Problem error_code values must appear in "
        f"LIFECYCLE_AND_ERRORS.md Section 4: {sorted(undocumented)}"
    )


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
    app = _create_problem_test_app()
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
    app = _create_problem_test_app()
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
    app = _create_problem_test_app()
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
    app = _create_problem_test_app()

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


@pytest.mark.parametrize(
    ("exc_factory", "expected_status", "expected_code"),
    [
        (AuthenticationRequiredError, 401, "authentication_required"),
        (AuthorizationDeniedError, 403, "authorization_denied"),
        (CsrfValidationError, 403, "csrf_validation_failed"),
    ],
)
def test_auth_context_errors_return_problem_without_leaks(
    exc_factory: type[Exception],
    expected_status: int,
    expected_code: str,
) -> None:
    app = _create_problem_test_app()

    @app.get("/api/v1/systems/{system_id}")
    async def read_system(system_id: str) -> None:
        raise exc_factory(
            f"denied for {SENSITIVE_LEAK_MARKERS[0]} at {SENSITIVE_LEAK_MARKERS[2]}"
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = f"/api/v1/systems/{SENSITIVE_LEAK_MARKERS[0]}"
    response = client.get(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=expected_status,
        expected_code=expected_code,
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


@pytest.mark.parametrize(
    ("exc_factory", "raise_callable"),
    [
        (SystemResourceNotFoundError, lambda: SystemResourceNotFoundError()),
        (SourceResourceNotFoundError, lambda: SourceResourceNotFoundError()),
        (
            PackageRevisionNotFoundError,
            lambda: PackageRevisionNotFoundError(
                package_revision_id=uuid.UUID(SENSITIVE_LEAK_MARKERS[0])
            ),
        ),
        (
            SystemNotFoundError,
            lambda: SystemNotFoundError(
                system_id=uuid.UUID(SENSITIVE_LEAK_MARKERS[0])
            ),
        ),
        (
            ParentRevisionNotFoundError,
            lambda: ParentRevisionNotFoundError(
                parent_revision_id=uuid.UUID(SENSITIVE_LEAK_MARKERS[0])
            ),
        ),
    ],
)
def test_resource_not_found_variants_return_404_without_identifier_leaks(
    exc_factory: type[Exception],
    raise_callable: Callable[[], Exception],
) -> None:
    app = _create_problem_test_app()

    @app.get("/api/v1/package-revisions/{revision_id}")
    async def read_revision(revision_id: str) -> None:
        raise raise_callable()

    client = TestClient(app, raise_server_exceptions=False)
    path = f"/api/v1/package-revisions/{SENSITIVE_LEAK_MARKERS[0]}"
    response = client.get(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=404,
        expected_code="resource_not_found",
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


def test_system_request_schema_invalid_returns_bounded_field_errors() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/systems")
    async def create_system() -> None:
        raise SystemRequestSchemaInvalidError(
            [
                FieldValidationError(
                    path="display_name",
                    code="length",
                    message=f"too long for {SENSITIVE_LEAK_MARKERS[1]}",
                )
            ]
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.post(path)

    payload = _assert_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
        retryable=False,
        field_errors=[
            {
                "path": "display_name",
                "code": "length",
                "message": f"too long for {SENSITIVE_LEAK_MARKERS[1]}",
            }
        ],
    )
    assert SENSITIVE_LEAK_MARKERS[1] not in payload["detail"]


def test_source_request_schema_invalid_returns_422_without_exception_detail() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/files")
    async def upload_file(revision_id: str) -> None:
        raise SourceRequestSchemaInvalidError(
            f"invalid filename at {SENSITIVE_LEAK_MARKERS[2]}"
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/upload/files"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


def test_package_revision_validation_error_maps_dynamic_code() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/confirm")
    async def confirm_revision(revision_id: str) -> None:
        raise PackageRevisionValidationError(
            "draft package section is required",
            error_code="request_schema_invalid",
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/confirm-target/confirm"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
        expected_detail="draft package section is required",
        expected_field_errors=[
            {
                "path": "/package",
                "code": "request_schema_invalid",
                "message": "draft package section is required",
            }
        ],
    )
    _assert_no_sensitive_leaks(payload)


def test_package_revision_validation_error_maps_control_statement_pointer() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/confirm")
    async def confirm_revision(revision_id: str) -> None:
        raise PackageRevisionValidationError(
            "security control AC-1 requires an implementation statement",
            error_code="request_schema_invalid",
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/confirm-target/confirm"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
        expected_detail="security control AC-1 requires an implementation statement",
        expected_field_errors=[
            {
                "path": "/security_controls/AC-1/implementation_statement",
                "code": "request_schema_invalid",
                "message": "security control AC-1 requires an implementation statement",
            }
        ],
    )
    _assert_no_sensitive_leaks(payload)


def test_package_revision_validation_error_maps_field_pointer() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/confirm")
    async def confirm_revision(revision_id: str) -> None:
        raise PackageRevisionValidationError(
            "system impact_level is required to seal package content",
            error_code="request_schema_invalid",
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/confirm-target/confirm"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
        expected_detail="system impact_level is required to seal package content",
        expected_field_errors=[
            {
                "path": "/system/impact_level",
                "code": "request_schema_invalid",
                "message": "system impact_level is required to seal package content",
            }
        ],
    )
    _assert_no_sensitive_leaks(payload)


def test_draft_build_error_maps_to_request_schema_invalid() -> None:
    from ato_service.draft_builder import DraftBuildError

    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/draft")
    async def save_draft(revision_id: str) -> None:
        raise DraftBuildError(
            "system.impact_level: 'low' is not of type 'null'",
            error_code="draft_schema_invalid",
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/draft-target/draft"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=422,
        expected_code="request_schema_invalid",
        expected_instance=path,
        expected_detail="system.impact_level: 'low' is not of type 'null'",
        expected_field_errors=[
            {
                "path": "/system/impact_level",
                "code": "request_schema_invalid",
                "message": "system.impact_level: 'low' is not of type 'null'",
            }
        ],
    )
    _assert_no_sensitive_leaks(payload)


@pytest.mark.parametrize(
    ("exc_factory",),
    [
        (InvalidPaginationCursorError,),
        (InvalidPageLimitError,),
    ],
)
def test_pagination_errors_map_to_malformed_request_400(
    exc_factory: type[Exception],
) -> None:
    """Section 4.1 maps malformed pagination inputs to HTTP 400 malformed_request."""
    app = _create_problem_test_app()

    @app.get("/api/v1/systems")
    async def list_systems() -> None:
        raise exc_factory(f"bad cursor {SENSITIVE_LEAK_MARKERS[2]}")

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.get(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=400,
        expected_code="malformed_request",
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


def test_idempotency_validation_error_maps_to_malformed_request_400() -> None:
    app = _create_problem_test_app()

    @app.get("/api/v1/systems")
    async def list_systems() -> None:
        raise IdempotencyValidationError(
            f"bad idempotency input at {SENSITIVE_LEAK_MARKERS[2]}"
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.get(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=400,
        expected_code="malformed_request",
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


def test_idempotency_key_conflict_returns_409() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/systems")
    async def create_system() -> None:
        raise IdempotencyConflictError(
            principal="actor",
            operation="systems.create",
            idempotency_key="duplicate-key-value",
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=409,
        expected_code="idempotency_key_conflict",
        expected_instance=path,
    )
    assert "duplicate-key-value" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("exc_factory", "expected_status", "expected_code", "retryable"),
    [
        (IfMatchRequiredError, 428, "if_match_required", False),
        (EtagMismatchError, 412, "etag_mismatch", True),
    ],
)
def test_concurrency_errors_return_expected_problem(
    exc_factory: type[Exception],
    expected_status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/confirm")
    async def confirm_revision(revision_id: str) -> None:
        raise exc_factory()

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/concurrency/confirm"
    response = client.post(path)

    _assert_problem(
        response,
        expected_status=expected_status,
        expected_code=expected_code,
        expected_instance=path,
        retryable=retryable,
    )


@pytest.mark.parametrize(
    ("exc_factory", "expected_status", "expected_code"),
    [
        (UnsupportedMediaTypeError, 415, "unsupported_media_type"),
        (SourceSizeLimitExceededError, 413, "source_size_limit_exceeded"),
        (PackageLimitExceededError, 413, "package_limit_exceeded"),
        (SourceTypeMismatchError, 422, "source_type_mismatch"),
        (DuplicateSourceArtifactError, 422, "duplicate_canonical_id"),
        (UnconfirmedFactProposalsError, 422, "unconfirmed_fact_proposals"),
    ],
)
def test_source_and_revision_validation_errors_return_problem(
    exc_factory: type[Exception],
    expected_status: int,
    expected_code: str,
) -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/files")
    async def upload_file(revision_id: str) -> None:
        raise exc_factory(f"failed for {SENSITIVE_LEAK_MARKERS[2]}")

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/upload/files"
    response = client.post(path)

    payload = _assert_nonretryable_problem(
        response,
        expected_status=expected_status,
        expected_code=expected_code,
        expected_instance=path,
    )
    _assert_no_sensitive_leaks(payload)


@pytest.mark.parametrize(
    ("error_code", "retryable", "expected_status"),
    [
        ("artifact_digest_mismatch", False, 500),
        ("state_artifact_inconsistent", False, 500),
        ("storage_unavailable", True, 503),
        ("request_schema_invalid", False, 422),
    ],
)
def test_package_revision_storage_error_maps_dynamic_code(
    error_code: str,
    retryable: bool,
    expected_status: int,
) -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/finalize")
    async def finalize_revision(revision_id: str) -> None:
        raise PackageRevisionStorageError(
            f"storage failed at {SENSITIVE_LEAK_MARKERS[2]}",
            error_code=error_code,
            retryable=retryable,
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/finalize-target/finalize"
    response = client.post(path)

    payload = _assert_problem(
        response,
        expected_status=expected_status,
        expected_code=error_code,
        expected_instance=path,
        retryable=retryable,
    )
    _assert_no_sensitive_leaks(payload)


def test_package_revision_storage_error_defaults_to_retryable_storage_unavailable() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/finalize")
    async def finalize_revision(revision_id: str) -> None:
        raise PackageRevisionStorageError(
            f"storage failed at {SENSITIVE_LEAK_MARKERS[2]}"
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/finalize-target/finalize"
    response = client.post(path)

    payload = _assert_problem(
        response,
        expected_status=503,
        expected_code="storage_unavailable",
        expected_instance=path,
        retryable=True,
    )
    assert response.headers["Retry-After"]
    _assert_no_sensitive_leaks(payload)


def test_source_artifact_storage_error_returns_retryable_503() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/package-revisions/{revision_id}/files")
    async def upload_file(revision_id: str) -> None:
        raise SourceArtifactStorageError(
            f"blob read failed at {SENSITIVE_LEAK_MARKERS[2]}"
        )

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/package-revisions/upload/files"
    response = client.post(path)

    payload = _assert_problem(
        response,
        expected_status=503,
        expected_code="storage_unavailable",
        expected_instance=path,
        retryable=True,
    )
    _assert_no_sensitive_leaks(payload)


@pytest.mark.parametrize(
    ("dependency_name", "expected_code", "retryable"),
    [
        ("database", "database_unavailable", True),
        ("runtime", "reconciliation_required", True),
        ("audit", "reconciliation_required", True),
    ],
)
def test_runtime_dependency_errors_map_to_contract_codes(
    dependency_name: str,
    expected_code: str,
    retryable: bool,
) -> None:
    from ato_service.api_dependencies import (
        AuditDependencyUnavailableError,
        DatabaseSessionUnavailableError,
        RuntimeStateUnavailableError,
    )

    exc_factory = {
        "database": DatabaseSessionUnavailableError,
        "runtime": RuntimeStateUnavailableError,
        "audit": AuditDependencyUnavailableError,
    }[dependency_name]

    app = _create_problem_test_app()

    @app.get("/api/v1/systems")
    async def list_systems() -> None:
        raise exc_factory(f"dependency missing at {SENSITIVE_LEAK_MARKERS[2]}")

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.get(path)

    payload = _assert_problem(
        response,
        expected_status=503,
        expected_code=expected_code,
        expected_instance=path,
        retryable=retryable,
    )
    _assert_no_sensitive_leaks(payload)


class _CreateSystemBody(BaseModel):
    display_name: str = Field(min_length=1)


_IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{16,128}$",
    ),
]


def test_missing_idempotency_key_header_returns_400_at_route_level() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/systems")
    async def create_system(
        body: _CreateSystemBody,
        idempotency_key: _IdempotencyKeyHeader,
    ) -> None:
        return None

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.post(
        path,
        json={"display_name": "Example System"},
    )

    payload = _assert_problem(
        response,
        expected_status=400,
        expected_code="idempotency_key_required",
        expected_instance=path,
        retryable=False,
        field_errors=[
            {
                "path": "header.Idempotency-Key",
                "code": "required",
                "message": "Idempotency-Key header is required.",
            }
        ],
    )
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_no_sensitive_leaks(payload)


def test_invalid_body_with_idempotency_key_returns_request_schema_invalid_422() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/systems")
    async def create_system(
        body: _CreateSystemBody,
        idempotency_key: _IdempotencyKeyHeader,
    ) -> None:
        return None

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.post(
        path,
        json={},
        headers={"Idempotency-Key": "idempotency-key-0001"},
    )

    assert response.status_code == 422
    payload = _problem_payload(response)
    assert payload["error_code"] == "request_schema_invalid"
    assert payload["status"] == 422
    assert payload["title"] == ERROR_TITLES["request_schema_invalid"]
    assert payload["detail"] == DEFAULT_DETAILS["request_schema_invalid"]
    assert payload["instance"] == path
    assert payload["retryable"] is False
    assert len(payload["field_errors"]) >= 1
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)
    _assert_no_sensitive_leaks(payload)


def test_request_validation_error_returns_problem_with_request_id() -> None:
    app = _create_problem_test_app()

    @app.post("/api/v1/systems")
    async def create_system(body: _CreateSystemBody) -> None:
        return None

    client = TestClient(app, raise_server_exceptions=False)
    path = "/api/v1/systems"
    response = client.post(path, json={})

    assert response.status_code == 422
    payload = _problem_payload(response)
    assert payload["error_code"] == "request_schema_invalid"
    assert payload["status"] == 422
    assert payload["title"] == ERROR_TITLES["request_schema_invalid"]
    assert payload["detail"] == DEFAULT_DETAILS["request_schema_invalid"]
    assert payload["type"] == f"{PROBLEM_TYPE_BASE}request_schema_invalid"
    assert payload["instance"] == path
    assert payload["retryable"] is False
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert len(payload["field_errors"]) >= 1
    assert all(
        set(item) == {"path", "code", "message"} for item in payload["field_errors"]
    )
    assert UUID_V4_PATTERN.match(payload["request_id"])
    _assert_problem_matches_contract(payload)
    _assert_no_sensitive_leaks(payload)
