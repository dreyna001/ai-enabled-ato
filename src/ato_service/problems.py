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
        "artifact_digest_mismatch",
        "analysis_not_eligible",
        "authentication_required",
        "authorization_denied",
        "capability_disabled",
        "chat_limit_exceeded",
        "classified_data_unsupported",
        "concurrent_run_limit_exceeded",
        "csrf_validation_failed",
        "customer_enterprise_mismatch",
        "database_unavailable",
        "duplicate_canonical_id",
        "etag_mismatch",
        "idempotency_key_conflict",
        "idempotency_key_required",
        "if_match_required",
        "illegal_state_transition",
        "malformed_request",
        "matrix_coverage_invalid",
        "model_call_limit_exceeded",
        "model_policy_not_approved",
        "model_routing_denied",
        "package_limit_exceeded",
        "prohibited_model_action",
        "reconciliation_required",
        "request_rate_limit_exceeded",
        "request_schema_invalid",
        "resource_not_found",
        "source_size_limit_exceeded",
        "source_type_mismatch",
        "state_artifact_inconsistent",
        "status_ceiling_violated",
        "storage_unavailable",
        "unconfirmed_fact_proposals",
        "unsupported_authorization_path",
        "unsupported_media_type",
    }
)

ERROR_TITLES: dict[str, str] = {
    "artifact_digest_mismatch": "Artifact digest mismatch",
    "analysis_not_eligible": "Analysis not eligible",
    "authentication_required": "Authentication required",
    "authorization_denied": "Authorization denied",
    "capability_disabled": "Capability disabled",
    "chat_limit_exceeded": "Chat limit exceeded",
    "classified_data_unsupported": "Classified data unsupported",
    "concurrent_run_limit_exceeded": "Concurrent run limit exceeded",
    "csrf_validation_failed": "CSRF validation failed",
    "customer_enterprise_mismatch": "Customer enterprise mismatch",
    "database_unavailable": "Database unavailable",
    "duplicate_canonical_id": "Duplicate canonical id",
    "etag_mismatch": "ETag mismatch",
    "idempotency_key_conflict": "Idempotency key conflict",
    "idempotency_key_required": "Idempotency key required",
    "if_match_required": "If-Match required",
    "illegal_state_transition": "Illegal state transition",
    "malformed_request": "Malformed request",
    "matrix_coverage_invalid": "Matrix coverage invalid",
    "model_call_limit_exceeded": "Model call limit exceeded",
    "model_policy_not_approved": "Model policy not approved",
    "model_routing_denied": "Model routing denied",
    "package_limit_exceeded": "Package limit exceeded",
    "prohibited_model_action": "Prohibited model action",
    "reconciliation_required": "Reconciliation required",
    "request_rate_limit_exceeded": "Request rate limit exceeded",
    "request_schema_invalid": "Request schema invalid",
    "resource_not_found": "Resource not found",
    "source_size_limit_exceeded": "Source size limit exceeded",
    "source_type_mismatch": "Source type mismatch",
    "state_artifact_inconsistent": "State artifact inconsistent",
    "status_ceiling_violated": "Status ceiling violated",
    "storage_unavailable": "Storage unavailable",
    "unconfirmed_fact_proposals": "Unconfirmed fact proposals",
    "unsupported_authorization_path": "Unsupported authorization path",
    "unsupported_media_type": "Unsupported media type",
}

DEFAULT_DETAILS: dict[str, str] = {
    "artifact_digest_mismatch": (
        "Stored artifact bytes do not match the recorded digest."
    ),
    "analysis_not_eligible": "The package revision is not eligible for analysis.",
    "authentication_required": "An authenticated principal is required.",
    "authorization_denied": "The principal is not authorized for this object.",
    "capability_disabled": "The requested process capability is disabled for this deployment.",
    "chat_limit_exceeded": "The configured package chat limit has been reached.",
    "classified_data_unsupported": (
        "Classified data cannot be sent to the configured model route."
    ),
    "concurrent_run_limit_exceeded": (
        "The configured concurrent analysis run limit has been reached."
    ),
    "csrf_validation_failed": (
        "The CSRF token or Origin validation failed for this mutation."
    ),
    "customer_enterprise_mismatch": (
        "The requested system does not belong to the configured customer enterprise."
    ),
    "database_unavailable": "The database is unavailable.",
    "duplicate_canonical_id": (
        "The package revision already contains an artifact with this digest."
    ),
    "etag_mismatch": "The supplied If-Match value is stale.",
    "idempotency_key_conflict": (
        "The Idempotency-Key was reused with a different request digest."
    ),
    "idempotency_key_required": (
        "Idempotency-Key is required for this replay-safe operation."
    ),
    "if_match_required": "A current If-Match header is required.",
    "illegal_state_transition": (
        "The requested transition is not legal from the current state."
    ),
    "malformed_request": "The request could not be parsed or is malformed.",
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
    "package_limit_exceeded": (
        "The package revision exceeds configured file or byte limits."
    ),
    "prohibited_model_action": (
        "The requested model action is prohibited by product policy."
    ),
    "reconciliation_required": (
        "One or more readiness checks require reconciliation."
    ),
    "request_schema_invalid": "One or more request fields failed validation.",
    "resource_not_found": "The requested resource was not found.",
    "source_size_limit_exceeded": (
        "The source artifact exceeds the configured byte limit."
    ),
    "source_type_mismatch": (
        "The declared media type does not match the detected content."
    ),
    "state_artifact_inconsistent": (
        "Domain state and stored artifacts are inconsistent."
    ),
    "status_ceiling_violated": (
        "Model-proposed status exceeds the deterministic evidence ceiling."
    ),
    "storage_unavailable": "The storage subsystem is unavailable.",
    "unconfirmed_fact_proposals": (
        "All fact proposals must be accepted or rejected before confirmation."
    ),
    "unsupported_authorization_path": (
        "The supplied authorization path is outside supported product scope."
    ),
    "unsupported_media_type": (
        "The declared media type is not supported for this upload."
    ),
}

ERROR_HTTP_METADATA: dict[str, tuple[int, bool]] = {
    "artifact_digest_mismatch": (500, False),
    "analysis_not_eligible": (422, False),
    "authentication_required": (401, False),
    "authorization_denied": (403, False),
    "capability_disabled": (403, False),
    "chat_limit_exceeded": (422, False),
    "classified_data_unsupported": (403, False),
    "concurrent_run_limit_exceeded": (429, True),
    "csrf_validation_failed": (403, False),
    "customer_enterprise_mismatch": (422, False),
    "database_unavailable": (503, True),
    "duplicate_canonical_id": (422, False),
    "etag_mismatch": (412, True),
    "idempotency_key_conflict": (409, False),
    "idempotency_key_required": (400, False),
    "if_match_required": (428, False),
    "illegal_state_transition": (409, False),
    "malformed_request": (400, False),
    "matrix_coverage_invalid": (422, False),
    "model_call_limit_exceeded": (422, False),
    "model_policy_not_approved": (403, False),
    "model_routing_denied": (403, False),
    "package_limit_exceeded": (413, False),
    "prohibited_model_action": (403, False),
    "reconciliation_required": (503, True),
    "request_rate_limit_exceeded": (429, True),
    "request_schema_invalid": (422, False),
    "resource_not_found": (404, False),
    "source_size_limit_exceeded": (413, False),
    "source_type_mismatch": (422, False),
    "state_artifact_inconsistent": (500, False),
    "status_ceiling_violated": (422, False),
    "storage_unavailable": (503, True),
    "unconfirmed_fact_proposals": (422, False),
    "unsupported_authorization_path": (422, False),
    "unsupported_media_type": (415, False),
}

_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*")
_UNIX_PATH = re.compile(r"/(?:[^/\s]+/)*[^/\s]+")
_IDEMPOTENCY_KEY_HEADER_NAMES = frozenset(
    {
        "idempotency-key",
        "idempotency_key",
        "Idempotency-Key",
    }
)


class FieldError(BaseModel):
    """Single request field validation failure."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=1000)
    code: str
    message: str = Field(max_length=1000)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _validate_field_error_code(value)


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


def _validate_field_error_code(code: str) -> str:
    if not ERROR_CODE_PATTERN.fullmatch(code):
        raise ValueError(f"invalid field error code format: {code!r}")
    return code


def _http_metadata_for_error_code(error_code: str) -> tuple[int, bool]:
    try:
        return ERROR_HTTP_METADATA[error_code]
    except KeyError as exc:
        raise ValueError(f"unsupported error_code: {error_code!r}") from exc


def _field_errors_from_system_validation(
    field_errors: list[Any],
) -> list[FieldError]:
    return [
        FieldError(
            path=item.path,
            code=item.code,
            message=sanitize_detail(item.message),
        )
        for item in field_errors[:100]
    ]


def _is_missing_idempotency_key_error(error: dict[str, Any]) -> bool:
    if error.get("type") != "missing":
        return False
    location = error.get("loc", ())
    if len(location) < 2 or location[0] != "header":
        return False
    header_name = str(location[1])
    if header_name in _IDEMPOTENCY_KEY_HEADER_NAMES:
        return True
    normalized = header_name.lower().replace("_", "-")
    return normalized == "idempotency-key"


def _idempotency_key_field_path(error: dict[str, Any]) -> str:
    location = error.get("loc", ())
    if len(location) >= 2 and location[0] == "header":
        return f"header.{location[1]}"
    return "header.Idempotency-Key"


def _field_errors_for_missing_idempotency_key(
    errors: list[dict[str, Any]],
) -> list[FieldError]:
    return [
        FieldError(
            path=_idempotency_key_field_path(error),
            code="required",
            message="Idempotency-Key header is required.",
        )
        for error in errors
        if _is_missing_idempotency_key_error(error)
    ][:100]


def _request_validation_problem(exc: Any) -> tuple[str, int, list[FieldError]]:
    errors = exc.errors()
    if any(_is_missing_idempotency_key_error(error) for error in errors):
        return (
            "idempotency_key_required",
            400,
            _field_errors_for_missing_idempotency_key(errors),
        )
    return (
        "request_schema_invalid",
        422,
        _field_errors_from_request_validation(exc),
    )


def _field_errors_from_request_validation(exc: Any) -> list[FieldError]:
    field_errors: list[FieldError] = []
    for error in exc.errors()[:100]:
        location = error.get("loc", ())
        path = _validation_error_path(location)
        code = _validation_error_type_code(str(error.get("type", "invalid_value")))
        message = sanitize_detail(str(error.get("msg", "invalid value")))
        field_errors.append(FieldError(path=path, code=code, message=message))
    return field_errors


def _validation_error_path(location: tuple[Any, ...] | list[Any]) -> str:
    parts: list[str] = []
    for item in location:
        if item in ("body", "query", "path", "header", "cookie"):
            continue
        parts.append(str(item))
    return ".".join(parts) if parts else "request"


def _validation_error_type_code(error_type: str) -> str:
    candidate = error_type.replace(".", "_").lower()
    if ERROR_CODE_PATTERN.fullmatch(candidate):
        return candidate
    return "invalid_value"


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


def _register_domain_problem_handler(
    app: FastAPI,
    exc_type: type[Exception],
    *,
    error_code: str | None = None,
    field_errors_for: Any | None = None,
    retryable_for: Any | None = None,
) -> None:
    """Map a domain exception family to a contract Problem response."""

    @app.exception_handler(exc_type)
    async def handle_domain_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        resolved_code = error_code or exc.error_code  # type: ignore[attr-defined]
        status, default_retryable = _http_metadata_for_error_code(resolved_code)
        if retryable_for is None:
            retryable = default_retryable
        else:
            retryable = retryable_for(exc)
        field_errors: list[FieldError] = []
        if field_errors_for is not None:
            field_errors = field_errors_for(exc)
        problem = build_problem(
            error_code=resolved_code,
            status=status,
            instance=request.url.path,
            request_id=get_request_id(request),
            retryable=retryable,
            field_errors=field_errors,
        )
        return problem_json_response(problem)


def _register_p11_problem_handlers(app: FastAPI) -> None:
    """Attach P1.1 package route domain exception handlers."""
    from fastapi.exceptions import RequestValidationError

    from ato_service.api_dependencies import (
        AuditDependencyUnavailableError,
        DatabaseSessionUnavailableError,
        RuntimeStateUnavailableError,
    )
    from ato_service.audit import AuditUnavailableError, AuditValidationError
    from ato_service.auth_context import (
        AuthenticationRequiredError,
        AuthorizationDeniedError,
        CsrfValidationError,
    )
    from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
    from ato_service.analysis_runs import (
        AnalysisRunNotFoundError,
        AnalysisRunPolicyError,
        AnalysisRunValidationError,
        ConcurrentRunLimitExceededError,
    )
    from ato_service.fact_proposals import (
        FactProposalNotFoundError,
    )
    from ato_service.oidc_auth import OidcAuthenticationError
    from ato_service.session_auth import SessionConfigurationError, SessionExpiredError
    from ato_service.idempotency import (
        IdempotencyConflictError,
        IdempotencyValidationError,
    )
    from ato_service.package_revision_drafts import PackageRevisionDraftNotFoundError
    from ato_service.package_revisions import (
        PackageRevisionNotFoundError,
        PackageRevisionStorageError,
        PackageRevisionValidationError,
        ParentRevisionNotFoundError,
        SystemNotFoundError,
        UnconfirmedFactProposalsError,
    )
    from ato_service.pagination import (
        InvalidPageLimitError,
        InvalidPaginationCursorError,
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
        RequestSchemaInvalidError as SystemRequestSchemaInvalidError,
        ResourceNotFoundError as SystemResourceNotFoundError,
    )

    for auth_error_type in (
        AuthenticationRequiredError,
        AuthorizationDeniedError,
        CsrfValidationError,
        OidcAuthenticationError,
        SessionExpiredError,
    ):
        _register_domain_problem_handler(app, auth_error_type)

    for not_found_type in (
        SystemResourceNotFoundError,
        SourceResourceNotFoundError,
        PackageRevisionNotFoundError,
        PackageRevisionDraftNotFoundError,
        SystemNotFoundError,
        ParentRevisionNotFoundError,
        FactProposalNotFoundError,
        AnalysisRunNotFoundError,
    ):
        _register_domain_problem_handler(app, not_found_type)

    _register_domain_problem_handler(
        app,
        SessionConfigurationError,
        error_code="reconciliation_required",
    )

    _register_domain_problem_handler(
        app,
        SystemRequestSchemaInvalidError,
        field_errors_for=lambda exc: _field_errors_from_system_validation(
            exc.field_errors
        ),
    )
    _register_domain_problem_handler(app, SourceRequestSchemaInvalidError)
    _register_domain_problem_handler(app, PackageRevisionValidationError)
    _register_domain_problem_handler(app, AnalysisRunValidationError)
    _register_domain_problem_handler(app, AnalysisRunPolicyError)
    _register_domain_problem_handler(app, ConcurrentRunLimitExceededError)
    _register_domain_problem_handler(
        app,
        PackageRevisionStorageError,
        retryable_for=lambda exc: exc.retryable,
    )
    _register_domain_problem_handler(app, UnconfirmedFactProposalsError)

    for malformed_error_type in (
        InvalidPaginationCursorError,
        InvalidPageLimitError,
        IdempotencyValidationError,
    ):
        _register_domain_problem_handler(
            app,
            malformed_error_type,
            error_code="malformed_request",
        )

    _register_domain_problem_handler(
        app,
        IdempotencyConflictError,
        error_code="idempotency_key_conflict",
    )

    for concurrency_error_type in (
        IfMatchRequiredError,
        EtagMismatchError,
    ):
        _register_domain_problem_handler(app, concurrency_error_type)

    for upload_error_type in (
        UnsupportedMediaTypeError,
        SourceSizeLimitExceededError,
        PackageLimitExceededError,
        SourceTypeMismatchError,
        DuplicateSourceArtifactError,
    ):
        _register_domain_problem_handler(app, upload_error_type)

    _register_domain_problem_handler(app, SourceArtifactStorageError)

    _register_domain_problem_handler(
        app,
        DatabaseSessionUnavailableError,
        error_code="database_unavailable",
    )
    _register_domain_problem_handler(
        app,
        RuntimeStateUnavailableError,
        error_code="reconciliation_required",
    )
    _register_domain_problem_handler(
        app,
        AuditDependencyUnavailableError,
        error_code="reconciliation_required",
    )
    for audit_error_type in (AuditValidationError, AuditUnavailableError):
        _register_domain_problem_handler(
            app,
            audit_error_type,
            error_code="reconciliation_required",
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        error_code, status, field_errors = _request_validation_problem(exc)
        problem = build_problem(
            error_code=error_code,
            status=status,
            instance=request.url.path,
            request_id=get_request_id(request),
            field_errors=field_errors,
            retryable=False,
        )
        return problem_json_response(problem)


def register_problem_handlers(app: FastAPI) -> None:
    """Attach middleware and the ServiceProblem exception handler."""
    from ato_service.fact_proposals import FactProposalReviewConflictError
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
    _register_typed_error_handler(
        app,
        FactProposalReviewConflictError,
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

    _register_p11_problem_handlers(app)
