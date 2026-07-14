"""Deterministic runtime input digests for analysis runs."""

from __future__ import annotations

import hashlib
from typing import Any

from ato_service.idempotency import canonical_json_bytes
from ato_service.runtime_config import RuntimeConfig

_EMPTY_PROMPT_BUNDLE_BYTES = canonical_json_bytes({})
_TARGETED_PROMPT_BUNDLE_BYTES = canonical_json_bytes({"bundle": "targeted-routed-1"})
_FULL_PROMPT_BUNDLE_BYTES = canonical_json_bytes({"bundle": "full-routed-1"})
DETERMINISTIC_MODEL_PROFILE = "deterministic"
ROUTED_MODEL_PROFILE = "openai_compatible"
DETERMINISTIC_PROMPT_BUNDLE_SHA256 = hashlib.sha256(_EMPTY_PROMPT_BUNDLE_BYTES).hexdigest()
TARGETED_PROMPT_BUNDLE_SHA256 = hashlib.sha256(_TARGETED_PROMPT_BUNDLE_BYTES).hexdigest()
FULL_PROMPT_BUNDLE_SHA256 = hashlib.sha256(_FULL_PROMPT_BUNDLE_BYTES).hexdigest()


def compute_config_fingerprint(config: RuntimeConfig) -> str:
    """Return a SHA-256 digest of the non-secret runtime configuration document."""
    document = config.document
    if not isinstance(document, dict):
        raise ValueError("runtime configuration document must be an object")
    safe_document = _strip_credential_references(document)
    return hashlib.sha256(canonical_json_bytes(safe_document)).hexdigest()


def prompt_bundle_sha256_for_run_type(run_type: str) -> str:
    """Return the prompt bundle digest for a run type."""
    if run_type == "deterministic_only":
        return DETERMINISTIC_PROMPT_BUNDLE_SHA256
    if run_type == "targeted":
        return TARGETED_PROMPT_BUNDLE_SHA256
    if run_type == "full":
        return FULL_PROMPT_BUNDLE_SHA256
    raise ValueError("unsupported analysis run type")


def model_profile_for_run_type(run_type: str) -> str:
    """Return the model profile label stored on an analysis run."""
    if run_type == "deterministic_only":
        return DETERMINISTIC_MODEL_PROFILE
    if run_type in {"targeted", "full"}:
        return ROUTED_MODEL_PROFILE
    raise ValueError("unsupported analysis run type")


def _strip_credential_references(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if not key.endswith("_CREDENTIAL_REFERENCE")
    }
