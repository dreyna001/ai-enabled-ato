"""RFC 9457-style Problem responses aligned to the published domain contract."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://ato.local/problems/"
MAX_DETAIL_LENGTH = 4000
ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
RETRYABLE_HTTP_STATUSES = frozenset({429, 503})
# Default delay for retryable 503 responses when no upstream Retry-After is known.
DEFAULT_RETRY_AFTER_SECONDS = 30

# Closed set of error codes used by the current API surface. Extend deliberately.
KNOWN_ERROR_CODES = frozenset(
    {
        "classified_data_unsupported",
        "database_unavailable",
        "illegal_state_transition",
        "matrix_coverage_invalid",
        "model_call_limit_exceeded",
        "model_policy_not_approved",
        "model_routing_denied",
        "prohibited_model_action",
        "reconciliation_required",
        "storage_unavailable",
    }
)

ERROR_TITLES: dict[str, str] = {
    "classified_data_unsupported": "Classified data unsupported",
    "database_unavailable": "Database unavailable",
    "illegal_state_transition": "Illegal state transition",
    "matrix_coverage_invalid": "Matrix coverage invalid",
    "model_call_limit_exceeded": "Model call limit exceeded",
    "model_policy_not_approved": "Model policy not approved",
    "model_routing_denied": "Model routing denied",
    "prohibited_model_action": "Prohibited model action",
    "reconciliation_required": "Reconciliation required",
    "storage_unavailable": "Storage unavailable",
}

DEFAULT_DETAILS: dict[str, str] = {
    "classified_data_unsupported": (
        "Classified data cannot be sent to the configured model route."
    ),
    "database_unavailable": "The database is unavailable.",
    "illegal_state_transition": (
        "The requested transition is not legal from the current state."
    ),
    "matrix_coverage_invalid": (
        "Matrix row identifiers do not exactly match the expected assessment items."
    ),
    "model_call_limit_exceeded": "The per-run model call limit has been reached.",
    "model_policy_not_approved": (
        "The configured model route is not approved by policy."
    ),
    "model_routing_denied": (
        "The requested model capability is denied by routing policy."
    ),
    "prohibited_model_action": (
        "The requested model action is prohibited by product policy."
    ),
    "reconciliation_required": (
        "One or more readiness checks require reconciliation."
    ),
    "storage_unavailable": "The storage subsystem is unavailable.",
}

_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*")
_UNIX_PATH = re.compile(r"/(?:[^/\s]+/)*[^/\s]+")


class FieldError(BaseModel):
    """Single request field validation failure."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=1000)
    code: str
    message: str = Field(max_length=1000)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _validate_error_code(value)


class Problem(BaseModel):
    """Problem Details response body."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=255)
    status: int = Field(ge=400, le=599)
    detail: str = Field(max_length=4000)
    instance: str = Field(max_length=2048)
    error_code: str
    request_id: UUID
    field_errors: list[FieldError] = Field(default_factory=list, max_length=100)
    retryable: bool

    @field_validator("error_code")
    @classmethod
    def validate_error_code_field(cls, value: str) -> str:
        return _validate_error_code(value)


class ServiceProblem(Exception):
    """Typed service exception rendered as a Problem response."""

    def __init__(
        self,
        *,
        error_code: str,
        status: int,
        instance: str,
        detail: str | None = None,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        field_errors: list[FieldError] | None = None,
    ) -> None:
        _validate_error_code(error_code)
        if status < 400 or status > 599:
            raise ValueError("status must be an HTTP error status")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        self.error_code = error_code
        self.status = status
        self.instance = instance
        self.detail = detail
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.field_errors = field_errors or []
        super().__init__(error_code)


def _validate_error_code(error_code: str) -> str:
    if not ERROR_CODE_PATTERN.fullmatch(error_code):
        raise ValueError(f"invalid error_code format: {error_code!r}")
    if error_code not in KNOWN_ERROR_CODES:
        raise ValueError(f"unsupported error_code: {error_code!r}")
    return error_code


def sanitize_detail(detail: str) -> str:
    """Return bounded client-safe detail without paths or stack traces."""
    cleaned = detail.strip()
    if "Traceback" in cleaned:
        cleaned = cleaned.split("Traceback", maxsplit=1)[0].strip()
    cleaned = _WINDOWS_PATH.sub("[path]", cleaned)
    cleaned = _UNIX_PATH.sub("[path]", cleaned)
    return cleaned[:MAX_DETAIL_LENGTH]


def build_problem(
    *,
    error_code: str,
    status: int,
    instance: str,
    request_id: UUID,
    detail: str | None = None,
    retryable: bool = False,
    field_errors: list[FieldError] | None = None,
) -> Problem:
    """Build a validated Problem payload."""
    resolved_detail = sanitize_detail(detail or DEFAULT_DETAILS[error_code])
    return Problem(
        type=f"{PROBLEM_TYPE_BASE}{error_code}",
        title=ERROR_TITLES[error_code],
        status=status,
        detail=resolved_detail,
        instance=instance,
        error_code=error_code,
        request_id=request_id,
        field_errors=field_errors or [],
        retryable=retryable,
    )


def problem_json_response(
    problem: Problem,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    """Serialize a Problem as application/problem+json."""
    headers: dict[str, str] = {}
    if problem.retryable and problem.status in RETRYABLE_HTTP_STATUSES:
        delay = (
            retry_after_seconds
            if retry_after_seconds is not None
            else DEFAULT_RETRY_AFTER_SECONDS
        )
        headers["Retry-After"] = str(delay)
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


def get_request_id(request: Request) -> UUID:
    """Return the request-scoped correlation identifier."""
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        raise RuntimeError("request_id is not available on request.state")
    return request_id


def _register_typed_error_handler(
    app: FastAPI,
    exc_type: type[Exception],
    *,
    status: int,
) -> None:
    """Map a typed domain exception to a non-retryable Problem response."""

    @app.exception_handler(exc_type)
    async def handle_typed_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        error_code = exc.error_code  # type: ignore[attr-defined]
        problem = build_problem(
            error_code=error_code,
            status=status,
            instance=request.url.path,
            request_id=get_request_id(request),
            retryable=False,
        )
        return problem_json_response(problem)


def register_problem_handlers(app: FastAPI) -> None:
    """Attach middleware and the ServiceProblem exception handler."""
    from ato_service.lifecycle_transitions import IllegalStateTransitionError
    from ato_service.matrix_coverage import MatrixCoverageError
    from ato_service.model_gateway import (
        ClassifiedDataUnsupportedError,
        ModelCallLimitExceededError,
        ModelPolicyNotApprovedError,
        ModelRoutingDeniedError,
        ProhibitedModelActionError,
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: Any) -> Any:
        from uuid import uuid4

        request.state.request_id = uuid4()
        return await call_next(request)

    @app.exception_handler(ServiceProblem)
    async def handle_service_problem(
        request: Request,
        exc: ServiceProblem,
    ) -> JSONResponse:
        problem = build_problem(
            error_code=exc.error_code,
            status=exc.status,
            instance=exc.instance,
            request_id=get_request_id(request),
            detail=exc.detail,
            retryable=exc.retryable,
            field_errors=exc.field_errors,
        )
        return problem_json_response(
            problem,
            retry_after_seconds=exc.retry_after_seconds,
        )

    _register_typed_error_handler(
        app,
        IllegalStateTransitionError,
        status=409,
    )
    for policy_error_type in (
        ModelRoutingDeniedError,
        ClassifiedDataUnsupportedError,
        ModelPolicyNotApprovedError,
        ProhibitedModelActionError,
    ):
        _register_typed_error_handler(app, policy_error_type, status=403)
    for validation_error_type in (
        ModelCallLimitExceededError,
        MatrixCoverageError,
    ):
        _register_typed_error_handler(app, validation_error_type, status=422)
