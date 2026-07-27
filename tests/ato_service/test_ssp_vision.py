from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from ato_service.extraction.limits import resolve_extraction_limits
from ato_service.runtime_config import RuntimeConfig
from ato_service.ssp_workspace.vision import (
    VisionConfigurationError,
    VisionExtractionError,
    VisionExtractionRequest,
    VisionPrompt,
    extract_screenshot_facts,
    resolve_vision_model_settings,
)


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 10), color="white").save(output, format="PNG")
    return output.getvalue()


def _request() -> VisionExtractionRequest:
    return VisionExtractionRequest(
        source_id="screenshot-1",
        content=_png_bytes(),
        declared_media_type="image/png",
        filename="settings.png",
    )


def _limits():
    return resolve_extraction_limits({})


def _valid_response() -> str:
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "observations": [
                {
                    "text": "Multi-factor authentication is enabled.",
                    "excerpt": "MFA status: Enabled",
                    "locator": {
                        "x": 0.1,
                        "y": 0.2,
                        "width": 0.4,
                        "height": 0.1,
                    },
                }
            ],
        }
    )


def test_extracts_locator_backed_fact_with_deterministic_id() -> None:
    prompts: list[VisionPrompt] = []

    async def model(prompt: VisionPrompt) -> str:
        prompts.append(prompt)
        return _valid_response()

    first = _run(
        extract_screenshot_facts(_request(), model=model, limits=_limits())
    )
    second = _run(
        extract_screenshot_facts(_request(), model=model, limits=_limits())
    )

    assert first.facts[0].fact_id == second.facts[0].fact_id
    assert first.facts[0].source_id == "screenshot-1"
    assert first.facts[0].excerpt == "MFA status: Enabled"
    assert first.detected_media_type == "image/png"
    assert prompts[0].image_bytes.startswith(b"\x89PNG")
    assert "untrusted data" in prompts[0].system


def test_repairs_one_schema_error_and_reuses_the_image() -> None:
    prompts: list[VisionPrompt] = []
    responses = iter(("not-json", _valid_response()))

    def model(prompt: VisionPrompt) -> str:
        prompts.append(prompt)
        return next(responses)

    result = _run(
        extract_screenshot_facts(_request(), model=model, limits=_limits())
    )

    assert result.attempts == 2
    assert result.repair_attempted is True
    assert prompts[0].image_bytes == prompts[1].image_bytes
    assert json.loads(prompts[1].user)["invalid_response"] == "not-json"


def test_invalid_locator_fails_after_one_repair() -> None:
    response = json.loads(_valid_response())
    response["observations"][0]["locator"]["width"] = 1.0

    with pytest.raises(VisionExtractionError) as caught:
        _run(
            extract_screenshot_facts(
                _request(),
                model=lambda _: json.dumps(response),
                limits=_limits(),
            )
        )

    assert caught.value.attempts == 2
    assert caught.value.repair_attempted is True
    assert caught.value.detail == "locator exceeds image width"


def test_duplicate_json_keys_fail_after_bounded_repair() -> None:
    duplicate = (
        '{"schema_version":"1.0.0","schema_version":"1.0.0",'
        '"observations":[]}'
    )

    with pytest.raises(VisionExtractionError) as caught:
        _run(
            extract_screenshot_facts(
                _request(),
                model=lambda _: duplicate,
                limits=_limits(),
            )
        )

    assert caught.value.failure_kind == "parse"
    assert caught.value.attempts == 2


def test_rejects_non_image_before_model_call() -> None:
    called = False

    def model(_: VisionPrompt) -> str:
        nonlocal called
        called = True
        return _valid_response()

    with pytest.raises(ValueError, match="screenshots must"):
        _run(
            extract_screenshot_facts(
                VisionExtractionRequest(
                    source_id="source-1",
                    content=b"plain text screenshot",
                    declared_media_type="text/plain",
                    filename="notes.txt",
                ),
                model=model,
                limits=_limits(),
            )
        )

    assert called is False


def test_model_call_failure_is_not_retried() -> None:
    call_count = 0

    def model(_: VisionPrompt) -> str:
        nonlocal call_count
        call_count += 1
        raise OSError("endpoint unavailable")

    with pytest.raises(VisionExtractionError) as caught:
        _run(
            extract_screenshot_facts(
                _request(),
                model=model,
                limits=_limits(),
            )
        )

    assert caught.value.failure_kind == "model_call"
    assert caught.value.attempts == 1
    assert call_count == 1


def test_resolves_existing_vision_runtime_fields() -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=Path("/data/ato-storage"),
        document={
            "VISION_MODEL_ENABLED": True,
            "VISION_MODEL_ENDPOINT_URL": "https://vision.example.test/v1",
            "VISION_MODEL_NAME": "vision-model",
            "VISION_MODEL_CONTEXT_TOKENS": 4096,
            "VISION_MODEL_ENDPOINT_PROFILE": "mock",
        },
    )

    settings = resolve_vision_model_settings(config)

    assert settings.endpoint_url == "https://vision.example.test/v1"
    assert settings.model_name == "vision-model"
    assert settings.context_tokens == 4096


def test_disabled_vision_configuration_fails_closed() -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=Path("/data/ato-storage"),
        document={},
    )

    with pytest.raises(VisionConfigurationError, match="must be true"):
        resolve_vision_model_settings(config)
