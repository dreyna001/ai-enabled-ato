"""Tests for dev-only local env file loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from ato_service.local_env import (
    TEXT_MODEL_API_KEY_ENV_VAR,
    load_local_env_file,
)
from ato_service.text_llm import build_text_model_client
from ato_service.runtime_config import load_runtime_config_from_dict


def test_load_local_env_file_sets_text_model_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "config.local.env"
    env_file.write_text("ATO_TEXT_MODEL_API_KEY=test-env-key\n", encoding="utf-8")
    monkeypatch.delenv(TEXT_MODEL_API_KEY_ENV_VAR, raising=False)

    loaded = load_local_env_file(env_file)

    assert loaded is True
    import os

    assert os.environ[TEXT_MODEL_API_KEY_ENV_VAR] == "test-env-key"


def test_load_local_env_file_does_not_override_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "config.local.env"
    env_file.write_text("ATO_TEXT_MODEL_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv(TEXT_MODEL_API_KEY_ENV_VAR, "existing-key")

    load_local_env_file(env_file)

    import os

    assert os.environ[TEXT_MODEL_API_KEY_ENV_VAR] == "existing-key"


def test_build_text_model_client_uses_local_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "config.local.env"
    env_file.write_text("ATO_TEXT_MODEL_API_KEY=test-env-key\n", encoding="utf-8")
    monkeypatch.delenv(TEXT_MODEL_API_KEY_ENV_VAR, raising=False)

    config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data/ato-storage",
            "TEXT_MODEL_PROVIDER": "openai_compatible",
            "TEXT_MODEL_ENDPOINT_URL": "https://api.openai.com/v1",
            "TEXT_MODEL_NAME": "gpt-4.1",
            "TEXT_MODEL_ENDPOINT_PROFILE": "external_openai",
        },
        base_dir=tmp_path,
    )

    load_local_env_file(env_file)

    client = build_text_model_client(config)

    assert client.api_key == "test-env-key"
