"""Configurable text LLM clients for OpenAI-compatible and AWS Bedrock APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import time
from typing import Any, Literal, Protocol
from urllib.parse import urljoin

import httpx

from ato_service.db.dsn import CREDENTIALS_DIRECTORY_ENV_VAR
from ato_service.local_env import (
    TEXT_MODEL_API_KEY_ENV_VAR,
    load_local_env_file,
)
from ato_service.runtime_config import RuntimeConfig, RuntimeConfigError

logger = logging.getLogger(__name__)

TEXT_MODEL_API_KEY_FILE_ENV_VAR = "ATO_TEXT_MODEL_API_KEY_FILE"
ChatRole = Literal["user", "assistant", "system"]


class TextModelClient(Protocol):
    """Minimal synchronous text completion client."""

    provider: str

    def complete(
        self,
        messages: Sequence["ChatMessage"],
        *,
        system: str | None = None,
    ) -> str:
        """Return assistant text for the supplied chat messages."""


class TextModelConfigurationError(RuntimeConfigError):
    """Raised when text-model settings or credentials are incomplete."""


class TextModelCallError(RuntimeConfigError):
    """Raised when a configured text-model request fails."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class TextModelSettings:
    provider: str
    model_name: str
    max_output_tokens: int
    timeout_seconds: int
    max_retries: int
    temperature: float
    endpoint_url: str | None = None
    aws_region: str | None = None


def text_model_is_configured(document: Mapping[str, Any]) -> bool:
    """Return whether runtime JSON contains enough text-model settings to call."""
    provider = document.get("TEXT_MODEL_PROVIDER", "openai_compatible")
    if "TEXT_MODEL_NAME" not in document:
        return False
    if provider == "aws_bedrock":
        return isinstance(document.get("AWS_REGION"), str) and bool(
            document["AWS_REGION"].strip()
        )
    return isinstance(document.get("TEXT_MODEL_ENDPOINT_URL"), str) and bool(
        document["TEXT_MODEL_ENDPOINT_URL"].strip()
    )


def resolve_text_model_settings(config: RuntimeConfig) -> TextModelSettings:
    """Resolve validated text-model settings from runtime configuration."""
    document = config.document
    if not text_model_is_configured(document):
        raise TextModelConfigurationError(
            "text model is not configured; set TEXT_MODEL_NAME and either "
            "TEXT_MODEL_ENDPOINT_URL for openai_compatible or AWS_REGION for aws_bedrock"
        )

    provider = config.text_model_provider
    model_name = _required_string(document, "TEXT_MODEL_NAME")
    max_output_tokens = _positive_int(document, "TEXT_MODEL_MAX_OUTPUT_TOKENS", default=1024)
    timeout_seconds = _positive_int(document, "TEXT_MODEL_TIMEOUT_SECONDS", default=30)
    max_retries = _non_negative_int(document, "TEXT_MODEL_MAX_RETRIES", default=2)
    temperature = _temperature(document)

    if provider == "aws_bedrock":
        return TextModelSettings(
            provider=provider,
            model_name=model_name,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            temperature=temperature,
            aws_region=_required_string(document, "AWS_REGION"),
        )

    return TextModelSettings(
        provider=provider,
        model_name=model_name,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        temperature=temperature,
        endpoint_url=_required_string(document, "TEXT_MODEL_ENDPOINT_URL"),
    )


def build_text_model_client(config: RuntimeConfig) -> TextModelClient:
    """Build the configured text-model client."""
    settings = resolve_text_model_settings(config)
    if settings.provider == "aws_bedrock":
        return BedrockTextClient(
            region=settings.aws_region or "",
            model_id=settings.model_name,
            max_output_tokens=settings.max_output_tokens,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            temperature=settings.temperature,
        )
    return OpenAICompatibleTextClient(
        endpoint_url=settings.endpoint_url or "",
        model_name=settings.model_name,
        api_key=_resolve_openai_api_key(config),
        max_output_tokens=settings.max_output_tokens,
        timeout_seconds=settings.timeout_seconds,
        max_retries=settings.max_retries,
        temperature=settings.temperature,
    )


def _required_string(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TextModelConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_int(document: Mapping[str, Any], key: str, *, default: int) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise TextModelConfigurationError(f"{key} must be a positive integer")
    return raw


def _non_negative_int(document: Mapping[str, Any], key: str, *, default: int) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise TextModelConfigurationError(f"{key} must be a non-negative integer")
    return raw


def _temperature(document: Mapping[str, Any]) -> float:
    raw = document.get("TEXT_MODEL_TEMPERATURE", 0.0)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TextModelConfigurationError("TEXT_MODEL_TEMPERATURE must be a number")
    value = float(raw)
    if value < 0.0 or value > 2.0:
        raise TextModelConfigurationError(
            "TEXT_MODEL_TEMPERATURE must be between 0 and 2 inclusive"
        )
    return value


def _resolve_openai_api_key(config: RuntimeConfig) -> str:
    reference = config.document.get("TEXT_MODEL_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        return _read_secret_from_credential_reference(reference)

    load_local_env_file()

    api_key = os.environ.get(TEXT_MODEL_API_KEY_ENV_VAR)
    if api_key and api_key.strip():
        return api_key.strip()

    env_path = os.environ.get(TEXT_MODEL_API_KEY_FILE_ENV_VAR)
    if env_path and env_path.strip():
        return _read_secret_file(Path(env_path.strip()))

    raise TextModelConfigurationError(
        "OpenAI-compatible text model requires TEXT_MODEL_CREDENTIAL_REFERENCE, "
        f"{TEXT_MODEL_API_KEY_ENV_VAR} in config.local.env, or "
        f"{TEXT_MODEL_API_KEY_FILE_ENV_VAR}"
    )


def _read_secret_from_credential_reference(reference: dict[str, Any]) -> str:
    source = reference.get("source")
    if source == "root_owned_file":
        path_raw = reference.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise TextModelConfigurationError(
                "TEXT_MODEL_CREDENTIAL_REFERENCE root_owned_file requires a path"
            )
        return _read_secret_file(Path(path_raw.strip()))

    if source == "systemd_credential":
        identifier = reference.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise TextModelConfigurationError(
                "TEXT_MODEL_CREDENTIAL_REFERENCE systemd_credential requires an identifier"
            )
        cred_dir_raw = os.environ.get(CREDENTIALS_DIRECTORY_ENV_VAR)
        if not cred_dir_raw or not cred_dir_raw.strip():
            raise TextModelConfigurationError(
                f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be set to resolve systemd credentials"
            )
        cred_dir = Path(cred_dir_raw.strip())
        if not cred_dir.is_absolute():
            raise TextModelConfigurationError(
                f"{CREDENTIALS_DIRECTORY_ENV_VAR} must be an absolute path"
            )
        return _read_secret_file(cred_dir / identifier.strip())

    raise TextModelConfigurationError(
        "TEXT_MODEL_CREDENTIAL_REFERENCE has an unsupported or malformed source"
    )


def _read_secret_file(path: Path) -> str:
    if not path.is_absolute():
        raise TextModelConfigurationError(
            f"secret file must be an absolute path; got {path!s}"
        )
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TextModelConfigurationError(
            f"secret file must be readable; got {path!s}"
        ) from exc
    if not secret:
        raise TextModelConfigurationError(
            f"secret file must be non-empty; got {path!s}"
        )
    return secret


@dataclass(frozen=True, slots=True)
class OpenAICompatibleTextClient:
    endpoint_url: str
    model_name: str
    api_key: str
    max_output_tokens: int
    timeout_seconds: int
    max_retries: int
    temperature: float = 0.0
    provider: str = "openai_compatible"

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> str:
        payload_messages = _build_openai_messages(messages, system=system)
        request_url = urljoin(self.endpoint_url.rstrip("/") + "/", "chat/completions")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model_name,
            "messages": payload_messages,
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
        }

        last_error: Exception | None = None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.post(request_url, headers=headers, json=body)
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError(
                            "retryable upstream response",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    return _extract_openai_text(response.json())
                except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries or not _is_retryable_http_error(exc):
                        break
                    time.sleep(min(2**attempt, 8))

        raise TextModelCallError("OpenAI-compatible text model request failed") from last_error


@dataclass(frozen=True, slots=True)
class BedrockTextClient:
    region: str
    model_id: str
    max_output_tokens: int
    timeout_seconds: int
    max_retries: int
    temperature: float = 0.0
    provider: str = "aws_bedrock"

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
    ) -> str:
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:
            raise TextModelConfigurationError(
                "aws_bedrock text model requires boto3; install with "
                'pip install -e ".[bedrock]"'
            ) from exc

        client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(
                connect_timeout=self.timeout_seconds,
                read_timeout=self.timeout_seconds,
                retries={"max_attempts": self.max_retries + 1, "mode": "standard"},
            ),
        )
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [
                {
                    "role": message.role,
                    "content": [{"text": message.content}],
                }
                for message in messages
                if message.role in {"user", "assistant"}
            ],
            "inferenceConfig": {
                "maxTokens": self.max_output_tokens,
                "temperature": self.temperature,
            },
        }
        if system:
            request["system"] = [{"text": system}]

        try:
            response = client.converse(**request)
        except (ClientError, BotoCoreError) as exc:
            raise TextModelCallError("AWS Bedrock text model request failed") from exc

        return _extract_bedrock_text(response)


def _build_openai_messages(
    messages: Sequence[ChatMessage],
    *,
    system: str | None,
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    if system:
        payload.append({"role": "system", "content": system})
    for message in messages:
        payload.append({"role": message.role, "content": message.content})
    return payload


def _extract_openai_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise TextModelCallError("OpenAI-compatible response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TextModelCallError("OpenAI-compatible response is missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise TextModelCallError("OpenAI-compatible response choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise TextModelCallError("OpenAI-compatible response is missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise TextModelCallError("OpenAI-compatible response is missing message content")
    return content


def _extract_bedrock_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise TextModelCallError("Bedrock response must be an object")
    output = payload.get("output")
    if not isinstance(output, dict):
        raise TextModelCallError("Bedrock response is missing output")
    message = output.get("message")
    if not isinstance(message, dict):
        raise TextModelCallError("Bedrock response is missing output.message")
    content_blocks = message.get("content")
    if not isinstance(content_blocks, list) or not content_blocks:
        raise TextModelCallError("Bedrock response is missing output.message.content")
    first = content_blocks[0]
    if not isinstance(first, dict):
        raise TextModelCallError("Bedrock response content block must be an object")
    text = first.get("text")
    if not isinstance(text, str) or not text.strip():
        raise TextModelCallError("Bedrock response is missing text content")
    return text


def _is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, httpx.TransportError)
