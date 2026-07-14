"""Single-customer-per-install enforcement through explicit runtime configuration."""

from __future__ import annotations

import re
from typing import Any

_CUSTOMER_ENTERPRISE_MISMATCH = "customer_enterprise_mismatch"
_DEFAULT_DEV_LOCAL_ENTERPRISE_ID = "dev-local-enterprise"
_ENTERPRISE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InstallationBoundaryError(ValueError):
    """Raised when a mutation violates the installation customer boundary."""

    def __init__(self, message: str, *, error_code: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class CustomerEnterpriseMismatchError(InstallationBoundaryError):
    """Raised when a system belongs to a different customer enterprise."""

    def __init__(self) -> None:
        super().__init__(
            "system customer enterprise does not match installation configuration",
            error_code=_CUSTOMER_ENTERPRISE_MISMATCH,
        )


def _validate_enterprise_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or _ENTERPRISE_ID_PATTERN.fullmatch(normalized) is None:
        raise InstallationBoundaryError(
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID is invalid",
            error_code="request_schema_invalid",
        )
    return normalized


def resolve_installation_customer_enterprise_id(config_document: dict[str, Any]) -> str:
    """Return the configured installation customer enterprise identifier."""
    runtime_profile = config_document.get("runtime_profile", "dev_local")
    configured = config_document.get("INSTALLATION_CUSTOMER_ENTERPRISE_ID")
    if configured is None:
        if runtime_profile == "dev_local":
            return _DEFAULT_DEV_LOCAL_ENTERPRISE_ID
        raise InstallationBoundaryError(
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID is required",
            error_code="request_schema_invalid",
        )
    if not isinstance(configured, str):
        raise InstallationBoundaryError(
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID must be a string",
            error_code="request_schema_invalid",
        )
    return _validate_enterprise_id(configured)


def require_matching_customer_enterprise(
    *,
    configured_enterprise_id: str,
    observed_enterprise_id: str,
) -> None:
    """Ensure persisted domain objects remain within the installation boundary."""
    if observed_enterprise_id != configured_enterprise_id:
        raise CustomerEnterpriseMismatchError()
