"""Unversioned health endpoints for process liveness and dependency readiness."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ato_service.problems import (
    build_problem,
    get_request_id,
    problem_json_response,
)

CheckStatus = Literal["ok", "degraded", "unavailable"]

READINESS_CHECK_NAMES = (
    "database",
    "storage",
    "authority_manifest",
    "jobs",
    "configuration",
)

ReadinessChecks = dict[str, CheckStatus]
ReadinessProbe = Callable[[], Awaitable[ReadinessChecks]]

HEALTH_PATH_SERVER_OVERRIDE = [
    {
        "url": "/",
        "description": "Application root outside the versioned API base path",
    }
]

_HEALTH_OK_RESPONSE = {
    "description": "Health state",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "checks"],
                "properties": {
                    "status": {
                        "enum": ["ok", "degraded", "unavailable"],
                    },
                    "checks": {
                        "type": "object",
                        "additionalProperties": {
                            "enum": ["ok", "degraded", "unavailable"],
                        },
                    },
                },
            }
        }
    },
}

_PROBLEM_RESPONSE = {
    "description": "Request failed",
    "content": {
        "application/problem+json": {
            "schema": {
                "$ref": "domain.schema.json#/$defs/Problem",
            }
        }
    },
}


class HealthResponse(BaseModel):
    """Published health response shape."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, CheckStatus] = Field(default_factory=dict)


def _aggregate_status(checks: Mapping[str, CheckStatus]) -> CheckStatus:
    values = list(checks.values())
    if any(value == "unavailable" for value in values):
        return "unavailable"
    if any(value == "degraded" for value in values):
        return "degraded"
    return "ok"


def _select_readiness_error_code(checks: Mapping[str, CheckStatus]) -> str:
    if checks.get("database") == "unavailable":
        return "database_unavailable"
    if checks.get("storage") == "unavailable":
        return "storage_unavailable"
    return "reconciliation_required"


def _normalize_readiness_checks(raw_checks: Any) -> ReadinessChecks | None:
    """Return validated readiness checks or None when output is malformed."""
    if not isinstance(raw_checks, Mapping):
        return None

    normalized: ReadinessChecks = {}
    for name in READINESS_CHECK_NAMES:
        if name not in raw_checks:
            return None
        value = raw_checks[name]
        if value not in ("ok", "degraded", "unavailable"):
            return None
        normalized[name] = value
    return normalized


def _readiness_problem_response(request: Request, *, error_code: str):
    problem = build_problem(
        error_code=error_code,
        status=503,
        instance=str(request.url.path),
        request_id=get_request_id(request),
        retryable=True,
    )
    return problem_json_response(problem)


def create_health_router(readiness_probe: ReadinessProbe) -> APIRouter:
    """Build health routes with an injected async readiness probe."""
    router = APIRouter(tags=["Health"])

    @router.get(
        "/health/live",
        operation_id="getLiveness",
        response_model=HealthResponse,
        responses={
            200: _HEALTH_OK_RESPONSE,
            "default": _PROBLEM_RESPONSE,
        },
    )
    async def get_liveness() -> HealthResponse:
        return HealthResponse(status="ok", checks={"process": "ok"})

    @router.get(
        "/health/ready",
        operation_id="getReadiness",
        responses={
            200: _HEALTH_OK_RESPONSE,
            503: _PROBLEM_RESPONSE,
            "default": _PROBLEM_RESPONSE,
        },
    )
    async def get_readiness(request: Request):
        try:
            raw_checks = await readiness_probe()
        except Exception:
            return _readiness_problem_response(
                request,
                error_code="reconciliation_required",
            )

        checks = _normalize_readiness_checks(raw_checks)
        if checks is None:
            return _readiness_problem_response(
                request,
                error_code="reconciliation_required",
            )

        status = _aggregate_status(checks)
        if status == "ok":
            return HealthResponse(status="ok", checks=dict(checks))

        error_code = _select_readiness_error_code(checks)
        return _readiness_problem_response(request, error_code=error_code)

    return router
