"""Tests for provider-neutral text-model capability profiles."""

from __future__ import annotations

import pytest

from ato_service.runtime_config import (
    RuntimeConfigValidationError,
    load_runtime_config_from_dict,
)
from ato_service.text_model_catalog import (
    TextModelCatalogError,
    load_text_model_catalog,
    resolve_text_model_capability_profile,
)


def _profile_document(profile_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data/ato-storage",
        "TEXT_MODEL_PROVIDER": "openai_compatible",
        "TEXT_MODEL_ENDPOINT_URL": "https://api.openai.com/v1",
        "TEXT_MODEL_NAME": "transport-specific-model-id",
        "TEXT_MODEL_PROFILE_ID": profile_id,
        "TEXT_MODEL_ENDPOINT_PROFILE": "external_openai",
    }


def test_catalog_profiles_have_valid_bounded_limits() -> None:
    profiles = load_text_model_catalog()

    assert "openai-gpt-4.1" in profiles
    assert "anthropic-claude-sonnet-4" in profiles
    assert "local-openai-compatible-8k" in profiles
    for profile in profiles.values():
        assert profile.max_output_tokens <= profile.application_context_tokens
        assert profile.application_context_tokens <= profile.context_window_tokens


def test_runtime_profile_materializes_catalog_owned_limits(tmp_path) -> None:
    config = load_runtime_config_from_dict(
        _profile_document("openai-gpt-4.1"),
        base_dir=tmp_path,
    )

    assert config.document["TEXT_MODEL_CONTEXT_TOKENS"] == 131072
    assert config.document["TEXT_MODEL_MAX_OUTPUT_TOKENS"] == 32768
    assert config.document["TEXT_MODEL_TIMEOUT_SECONDS"] == 180
    reloaded = load_runtime_config_from_dict(config.document, base_dir=tmp_path)
    assert reloaded.document == config.document


def test_runtime_profile_rejects_direct_limit_override(tmp_path) -> None:
    document = _profile_document("openai-gpt-4.1")
    document["TEXT_MODEL_MAX_OUTPUT_TOKENS"] = 1024

    with pytest.raises(
        RuntimeConfigValidationError,
        match="catalog-selected model profiles own their limits",
    ):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def test_runtime_profile_rejects_unknown_profile(tmp_path) -> None:
    with pytest.raises(RuntimeConfigValidationError, match="not present"):
        load_runtime_config_from_dict(
            _profile_document("unknown-model"),
            base_dir=tmp_path,
        )


def test_catalog_resolver_rejects_unknown_profile() -> None:
    with pytest.raises(TextModelCatalogError, match="not present"):
        resolve_text_model_capability_profile("unknown-model")
