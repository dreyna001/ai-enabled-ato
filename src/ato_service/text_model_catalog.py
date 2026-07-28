"""Provider-neutral text-model capability profiles."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files
import json
from typing import Any, Mapping


class TextModelCatalogError(ValueError):
    """Raised when a selected model profile is absent or malformed."""


@dataclass(frozen=True, slots=True)
class TextModelCapabilityProfile:
    """Limits that follow a model family regardless of transport."""

    profile_id: str
    model_family: str
    context_window_tokens: int
    application_context_tokens: int
    max_output_tokens: int
    timeout_seconds: int
    qualification_status: str
    evidence_url: str | None


def _positive_int(document: Mapping[str, Any], key: str, profile_id: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TextModelCatalogError(
            f"text model profile {profile_id!r} field {key} must be a positive integer"
        )
    return value


@cache
def load_text_model_catalog() -> dict[str, TextModelCapabilityProfile]:
    """Load and validate the bundled operator-editable model catalog."""
    catalog_path = files("ato_service").joinpath("text_model_catalog.json")
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TextModelCatalogError("text model catalog is unreadable or invalid JSON") from exc

    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0.0":
        raise TextModelCatalogError("text model catalog schema_version must be 1.0.0")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise TextModelCatalogError("text model catalog profiles must be a non-empty object")

    resolved: dict[str, TextModelCapabilityProfile] = {}
    for profile_id, raw in profiles.items():
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise TextModelCatalogError("text model profile IDs must be non-empty strings")
        if not isinstance(raw, dict):
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} must be an object"
            )
        model_family = raw.get("model_family")
        qualification_status = raw.get("qualification_status")
        evidence_url = raw.get("evidence_url")
        if not isinstance(model_family, str) or not model_family.strip():
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} model_family must be non-empty"
            )
        if not isinstance(qualification_status, str) or not qualification_status.strip():
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} qualification_status must be non-empty"
            )
        if evidence_url is not None and (
            not isinstance(evidence_url, str) or not evidence_url.startswith("https://")
        ):
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} evidence_url must be HTTPS or null"
            )

        context_window = _positive_int(raw, "context_window_tokens", profile_id)
        application_context = _positive_int(raw, "application_context_tokens", profile_id)
        max_output = _positive_int(raw, "max_output_tokens", profile_id)
        timeout = _positive_int(raw, "timeout_seconds", profile_id)
        if application_context > context_window:
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} application context exceeds model context"
            )
        if max_output > application_context:
            raise TextModelCatalogError(
                f"text model profile {profile_id!r} max output exceeds application context"
            )

        resolved[profile_id] = TextModelCapabilityProfile(
            profile_id=profile_id,
            model_family=model_family.strip(),
            context_window_tokens=context_window,
            application_context_tokens=application_context,
            max_output_tokens=max_output,
            timeout_seconds=timeout,
            qualification_status=qualification_status.strip(),
            evidence_url=evidence_url,
        )
    return resolved


def resolve_text_model_capability_profile(
    profile_id: str,
) -> TextModelCapabilityProfile:
    """Resolve one catalog profile or fail closed."""
    profile = load_text_model_catalog().get(profile_id)
    if profile is None:
        raise TextModelCatalogError(
            f"TEXT_MODEL_PROFILE_ID {profile_id!r} is not present in the text model catalog"
        )
    return profile
