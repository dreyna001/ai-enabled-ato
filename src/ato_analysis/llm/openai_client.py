"""OpenAI Chat Completions client for Block 1 dev_local profile."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ato_analysis.config import Settings
from ato_analysis.llm.structured_output import extract_json_from_text

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class OpenAILLMClient:
    """OpenAI-compatible chat client returning parsed JSON objects."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(settings.openai_timeout)
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._http_client.close()

    def __enter__(self) -> OpenAILLMClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]:
        user_message = user if not schema_hint else f"{user}\n\n{schema_hint}"
        payload = {
            "model": self._settings.openai_model,
            "max_tokens": self._settings.openai_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        max_retries = self._settings.openai_max_retries

        for attempt in range(max_retries + 1):
            try:
                response = self._http_client.post(
                    self._settings.openai_api_url,
                    headers=headers,
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise RuntimeError(
                        "OpenAI request failed after retries"
                    ) from exc
                self._sleep_before_retry(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(
                    f"OpenAI returned retryable status {response.status_code}"
                )
                logger.warning(
                    "OpenAI retryable response status=%s attempt=%s model=%s",
                    response.status_code,
                    attempt + 1,
                    self._settings.openai_model,
                )
                if attempt >= max_retries:
                    response.raise_for_status()
                self._sleep_before_retry(attempt)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"OpenAI request failed with status {response.status_code}"
                ) from exc

            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError("OpenAI response was not valid JSON") from exc

            try:
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    "OpenAI response missing message content"
                ) from exc

            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("OpenAI returned empty message content")

            return extract_json_from_text(content)

        raise RuntimeError("OpenAI request failed after retries") from last_error

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        time.sleep(min(2**attempt, 8))
