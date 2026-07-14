"""Explicit process capability flags from validated runtime JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ato_service.runtime_config import RuntimeConfigValidationError

_PROCESS_CAPABILITY_KEYS = (
    "api",
    "intake_worker",
    "analyzer_worker",
    "portal_static",
    "malware_scanning",
    "text_model_calls",
    "vision_model_calls",
    "oidc_authentication",
    "package_search",
    "package_chat",
)


@dataclass(frozen=True, slots=True)
class ProcessCapabilities:
    """Resolved capability flags for operator preflight and dependency validation."""

    api: bool
    intake_worker: bool
    analyzer_worker: bool
    portal_static: bool
    malware_scanning: bool
    text_model_calls: bool
    vision_model_calls: bool
    oidc_authentication: bool
    package_search: bool
    package_chat: bool

    def requires_database(self) -> bool:
        return self.api or self.intake_worker or self.analyzer_worker

    def requires_storage(self) -> bool:
        return self.api or self.intake_worker or self.analyzer_worker

    def requires_audit_credentials(self) -> bool:
        return self.api or self.intake_worker or self.analyzer_worker


def resolve_process_capabilities(document: dict[str, Any]) -> ProcessCapabilities | None:
    """Return capability flags when configured; None for minimal dev_local JSON."""
    raw = document.get("PROCESS_CAPABILITIES")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeConfigValidationError("PROCESS_CAPABILITIES must be an object")

    resolved: dict[str, bool] = {}
    for key in _PROCESS_CAPABILITY_KEYS:
        value = raw.get(key, False if key in {"package_search", "package_chat"} else None)
        if value is None:
            if document.get("runtime_profile") == "onprem_production":
                raise RuntimeConfigValidationError(
                    f"PROCESS_CAPABILITIES.{key} is required for onprem_production"
                )
            continue
        if not isinstance(value, bool):
            raise RuntimeConfigValidationError(
                f"PROCESS_CAPABILITIES.{key} must be a boolean"
            )
        resolved[key] = value

    if document.get("runtime_profile") == "onprem_production":
        for key in _PROCESS_CAPABILITY_KEYS[:8]:
            if key not in resolved:
                raise RuntimeConfigValidationError(
                    f"PROCESS_CAPABILITIES.{key} is required for onprem_production"
                )

    return ProcessCapabilities(
        api=resolved.get("api", False),
        intake_worker=resolved.get("intake_worker", False),
        analyzer_worker=resolved.get("analyzer_worker", False),
        portal_static=resolved.get("portal_static", False),
        malware_scanning=resolved.get("malware_scanning", False),
        text_model_calls=resolved.get("text_model_calls", False),
        vision_model_calls=resolved.get("vision_model_calls", False),
        oidc_authentication=resolved.get("oidc_authentication", False),
        package_search=resolved.get("package_search", False),
        package_chat=resolved.get("package_chat", False),
    )


__all__ = ["ProcessCapabilities", "resolve_process_capabilities"]
