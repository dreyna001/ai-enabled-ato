"""Deterministic rejection of unsupported authorization paths and classified inputs."""

from __future__ import annotations

import re

_UNSUPPORTED_AUTHORIZATION_PATH = "unsupported_authorization_path"
_CLASSIFIED_DATA_UNSUPPORTED = "classified_data_unsupported"

_UNSUPPORTED_AUTHORIZATION_PATHS = frozenset(
    {
        "classified",
        "ccri",
        "dod",
        "dod_rmf",
        "emass",
        "fedramp_agency_certification",
        "ic",
        "intelligence_community",
        "intelligence",
        "privacy",
    }
)

_AUTHORIZATION_PATH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_./-]{0,499}$")


class UnsupportedAuthorizationPathError(ValueError):
    """Raised when an authorization path is outside product scope."""

    error_code = _UNSUPPORTED_AUTHORIZATION_PATH

    def __init__(self, *, authorization_path: str) -> None:
        self.authorization_path = authorization_path
        super().__init__("authorization path is outside product scope")


class ClassifiedAuthorizationInputError(ValueError):
    """Raised when classified authorization inputs are supplied."""

    error_code = _CLASSIFIED_DATA_UNSUPPORTED

    def __init__(self, *, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(f"{field_name} is outside product scope")


def normalize_authorization_path(value: str) -> str:
    """Normalize authorization-path text for deterministic boundary checks."""
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def is_supported_authorization_path(value: str) -> bool:
    """Return whether the authorization path is within the supported product scope."""
    normalized = normalize_authorization_path(value)
    if not normalized:
        return True
    if normalized in _UNSUPPORTED_AUTHORIZATION_PATHS:
        return False
    for blocked in _UNSUPPORTED_AUTHORIZATION_PATHS:
        if normalized.startswith(f"{blocked}_") or normalized.endswith(f"_{blocked}"):
            return False
        if f"_{blocked}_" in normalized:
            return False
    return _AUTHORIZATION_PATH_PATTERN.fullmatch(normalized) is not None


def require_supported_authorization_path(value: str) -> str:
    """Validate an authorization path or raise UnsupportedAuthorizationPathError."""
    if not isinstance(value, str):
        raise UnsupportedAuthorizationPathError(authorization_path=str(value))
    normalized = normalize_authorization_path(value)
    if normalized and not is_supported_authorization_path(value):
        raise UnsupportedAuthorizationPathError(authorization_path=value)
    return value


def require_unclassified_sensitivity(value: str, *, field_name: str = "sensitivity") -> str:
    """Reject classified package sensitivity at API and intake boundaries."""
    if value == "classified":
        raise ClassifiedAuthorizationInputError(field_name=field_name)
    return value


def validate_system_context_authorization_path(document: dict) -> None:
    """Validate authorization_path inside a system-context or draft document."""
    system_section = document.get("system")
    if not isinstance(system_section, dict):
        return
    authorization_path = system_section.get("authorization_path")
    if isinstance(authorization_path, str) and authorization_path.strip():
        require_supported_authorization_path(authorization_path)
