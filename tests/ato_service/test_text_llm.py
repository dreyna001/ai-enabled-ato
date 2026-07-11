"""Tests for configurable text LLM clients."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ato_service.runtime_config import RuntimeConfig, load_runtime_config_from_dict
from ato_service.text_llm import (
    TEXT_MODEL_API_KEY_FILE_ENV_VAR,
    BedrockTextClient,
    ChatMessage,
    OpenAICompatibleTextClient,
    TextModelCallError,
    TextModelConfigurationError,
    build_text_model_client,
    resolve_text_model_settings,
    text_model_is_configured,
)

ROOT = Path(__file__).resolve().parents[2]
OPENAI_EXAMPLE = ROOT / "deployment" / "config" / "runtime-config.dev_local.openai.example.json"
BEDROCK_EXAMPLE = ROOT / "deployment" / "config" / "runtime-config.dev_local.bedrock.example.json"


def _dev_config(**overrides: Any) -> RuntimeConfig:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data/ato-storage",
        **overrides,
    }
    return load_runtime_config_from_dict(document, base_dir=ROOT)


def test_example_openai_config_is_recognized_as_configured() -> None:
    document = json.loads(OPENAI_EXAMPLE.read_text(encoding="utf-8"))
    assert text_model_is_configured(document) is True


def test_example_bedrock_config_is_recognized_as_configured() -> None:
    document = json.loads(BEDROCK_EXAMPLE.read_text(encoding="utf-8"))
    assert text_model_is_configured(document) is True


def test_openai_example_loads_and_builds_client_with_api_key_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_file = tmp_path / "openai-key.txt"
    api_key_file.write_text("test-openai-key", encoding="utf-8")
    monkeypatch.setenv(TEXT_MODEL_API_KEY_FILE_ENV_VAR, str(api_key_file))

    config = load_runtime_config_from_dict(
        json.loads(OPENAI_EXAMPLE.read_text(encoding="utf-8")),
        base_dir=tmp_path,
    )
    client = build_text_model_client(config)

    assert isinstance(client, OpenAICompatibleTextClient)
    assert client.provider == "openai_compatible"
    assert client.model_name == "gpt-4o-mini"


def test_bedrock_example_resolves_settings_without_endpoint_url(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        json.loads(BEDROCK_EXAMPLE.read_text(encoding="utf-8")),
        base_dir=tmp_path,
    )
    settings = resolve_text_model_settings(config)

    assert settings.provider == "aws_bedrock"
    assert settings.aws_region == "us-east-1"
    assert settings.endpoint_url is None


def test_build_text_model_client_requires_openai_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TEXT_MODEL_API_KEY_FILE_ENV_VAR, raising=False)
    config = _dev_config(
        TEXT_MODEL_PROVIDER="openai_compatible",
        TEXT_MODEL_ENDPOINT_URL="https://api.openai.com/v1",
        TEXT_MODEL_NAME="gpt-4o-mini",
    )

    with pytest.raises(TextModelConfigurationError, match="TEXT_MODEL_CREDENTIAL_REFERENCE"):
        build_text_model_client(config)


def test_openai_client_posts_chat_completion(tmp_path: Path) -> None:
    client = OpenAICompatibleTextClient(
        endpoint_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        api_key="secret",
        max_output_tokens=128,
        timeout_seconds=5,
        max_retries=0,
    )
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "ok"}}]},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    with patch("ato_service.text_llm.httpx.Client") as client_cls:
        http_client = MagicMock()
        http_client.__enter__.return_value = http_client
        http_client.post.return_value = response
        client_cls.return_value = http_client

        text = client.complete([ChatMessage(role="user", content="hello")])

    assert text == "ok"
    http_client.post.assert_called_once()
    posted = http_client.post.call_args.kwargs["json"]
    assert posted["model"] == "gpt-4o-mini"
    assert posted["messages"][-1]["content"] == "hello"


def test_openai_client_raises_on_invalid_response_shape() -> None:
    client = OpenAICompatibleTextClient(
        endpoint_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        api_key="secret",
        max_output_tokens=128,
        timeout_seconds=5,
        max_retries=0,
    )
    response = httpx.Response(
        200,
        json={"choices": []},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    with patch("ato_service.text_llm.httpx.Client") as client_cls:
        http_client = MagicMock()
        http_client.__enter__.return_value = http_client
        http_client.post.return_value = response
        client_cls.return_value = http_client

        with pytest.raises(TextModelCallError, match="missing choices"):
            client.complete([ChatMessage(role="user", content="hello")])


def test_bedrock_client_uses_converse_api() -> None:
    client = BedrockTextClient(
        region="us-east-1",
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        max_output_tokens=64,
        timeout_seconds=5,
        max_retries=0,
    )
    bedrock_client = MagicMock()
    bedrock_client.converse.return_value = {
        "output": {"message": {"content": [{"text": "bedrock-ok"}]}}
    }

    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = bedrock_client
    fake_config = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "boto3": fake_boto3,
            "botocore.config": MagicMock(Config=fake_config),
            "botocore.exceptions": MagicMock(
                BotoCoreError=Exception,
                ClientError=Exception,
            ),
        },
    ):
        text = client.complete(
            [ChatMessage(role="user", content="hello")],
            system="be concise",
        )

    assert text == "bedrock-ok"
    bedrock_client.converse.assert_called_once()
    request = bedrock_client.converse.call_args.kwargs
    assert request["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert request["system"] == [{"text": "be concise"}]


def test_bedrock_client_requires_boto3() -> None:
    client = BedrockTextClient(
        region="us-east-1",
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        max_output_tokens=64,
        timeout_seconds=5,
        max_retries=0,
    )
    original_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {"boto3", "botocore.config", "botocore.exceptions"}:
            raise ImportError("no boto3")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(TextModelConfigurationError, match="requires boto3"):
            client.complete([ChatMessage(role="user", content="hello")])
