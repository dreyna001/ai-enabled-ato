"""Async, schema-enforced model boundary for bounded SSP steps.

Provider clients are lazy and application-scoped. Domain grounding and the single
repair remain owned by the calling workflow; this adapter never adds agent loops.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
from io import BytesIO
from math import ceil
from typing import TYPE_CHECKING

import httpx
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI, omit
from opentelemetry import trace
from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent, NativeOutput, StructuredDict
from PIL import Image
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserContent
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from ato_service.context_budget import estimate_tokens_from_text, resolve_context_budget
from ato_service.runtime_config import RuntimeConfig
from ato_service.text_llm import (
    TextModelCallError,
    TextModelConfigurationError,
    _resolve_openai_api_key,
    resolve_text_model_settings,
)

if TYPE_CHECKING:
    from ato_service.ssp_workspace.generation import ModelPrompt
    from ato_service.ssp_workspace.vision import VisionPrompt

MAX_CONCURRENT_MODEL_CALLS = 4
MAX_RESPONSE_CHARACTERS = 2_000_000
REPAIR_INSTRUCTION_RESERVE_TOKENS = 2048


async def _no_api_key() -> str:
    """Explicit local no-auth mode; never inherit an ambient OpenAI credential."""
    return ""


class SspContextBudgetError(TextModelCallError):
    """The complete request cannot fit the configured model's input budget."""


def check_prompt_budget(
    *,
    system: str,
    user: str,
    schema: dict[str, object],
    context_tokens: int,
    max_output_tokens: int,
    utilization_target: float = 0.70,
    image_tokens: int = 0,
) -> int:
    """Preflight aggregate context, schema, output and one repair reserve.

    This uses the application's deterministic estimator, not a claim of exact
    provider tokenization. Non-ASCII bytes and a 25% margin are counted additionally.
    Every repaired prompt is checked again; facts are never silently discarded.
    """
    # PydanticAI inlines schema definitions for StructuredDict. Budget that wire
    # representation, not the smaller source schema with reusable references.
    wire_schema = TypeAdapter(StructuredDict(schema)).json_schema()
    schema_text = json.dumps(wire_schema, ensure_ascii=False, separators=(",", ":"))
    text = system + "\n" + user + "\n" + schema_text
    estimated = (
        (estimate_tokens_from_text(text) * 5 + 3) // 4
        + len(text.encode("utf-8"))
        - len(text)
        + image_tokens
    )
    budget = resolve_context_budget(
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        instruction_overhead_tokens=max_output_tokens
        + REPAIR_INSTRUCTION_RESERVE_TOKENS,
        utilization_target=utilization_target,
    )
    if estimated > budget.input_budget_tokens:
        raise SspContextBudgetError(
            "SSP request exceeds the configured aggregate context budget; "
            "reduce the selected evidence or use an approved larger-context profile"
        )
    return estimated


class _CompleteResponseModel(WrapperModel):
    """Reject truncated/refused output before the agent attempts to parse it."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )
        if response.finish_reason in {"length", "content_filter", "error"}:
            raise TextModelCallError("SSP model response was incomplete or refused")
        if (
            sum(len(p.content) for p in response.parts if isinstance(p, TextPart))
            > MAX_RESPONSE_CHARACTERS
        ):
            raise TextModelCallError("SSP model response exceeds the size limit")
        return response


async def run_structured_step(
    model: Model,
    *,
    system: str,
    content: str | Sequence[UserContent],
    schema: dict[str, object],
    max_output_tokens: int,
    timeout_seconds: float,
    temperature: float = 0,
) -> str:
    """One native structured response, with no implicit schema or tool retries."""
    if not model.profile.get("supports_json_schema_output", False):
        raise TextModelConfigurationError(
            "Selected model does not support native structured output; "
            "select a qualified model (prompt-only fallback is disabled)"
        )
    output = NativeOutput(StructuredDict(schema), strict=True)
    agent = Agent(
        _CompleteResponseModel(model),
        output_type=output,
        system_prompt=system,
        retries=0,
        name="ssp_structured_step",
        model_settings={"max_tokens": max_output_tokens, "temperature": temperature},
    )
    # Never inherit process-wide model-content tracing; emit only our safe span.
    agent.instrument = False
    # Do not capture exceptions, prompts, schema content, images or identifiers in
    # telemetry. Deployment may supply an OpenTelemetry provider/exporter.
    with trace.get_tracer(__name__).start_as_current_span(
        "ssp.model.structured_step",
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.set_attribute("ssp.output.mode", "native")
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await agent.run(
                    content, usage_limits=UsageLimits(request_limit=1)
                )
            # StructuredDict attaches a schema but deliberately does not validate
            # its contents. Validate locally even when an endpoint claims strictness.
            if not Draft202012Validator(schema).is_valid(result.output):
                raise TextModelCallError("SSP model response failed its output schema")
            from ato_service.ssp_workspace.model_schemas import normalize_native_output

            normalized = normalize_native_output(schema, result.output)
            span.set_attribute("ssp.output.valid", True)
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            span.set_attribute("ssp.output.valid", False)
            raise


class SspModelAdapter:
    """Application-owned client, bounded concurrency and per-invocation policy."""

    def __init__(self, config: RuntimeConfig, *, model: Model | None = None) -> None:
        self.config = config
        self._model = model
        self._client: AsyncOpenAI | None = None
        self._bedrock_client = None
        self._initialization = asyncio.Lock()
        self._shutdown = asyncio.Lock()
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_MODEL_CALLS)
        self._closed = False

    async def _resolve_model(self) -> Model:
        async with self._initialization:
            if self._closed:
                raise TextModelCallError("SSP model runtime is closed")
            if self._model is not None:
                return self._model
            settings = resolve_text_model_settings(self.config)
            if settings.provider == "aws_bedrock":
                import boto3
                from botocore.config import Config
                from pydantic_ai.models.bedrock import BedrockConverseModel
                from pydantic_ai.providers.bedrock import BedrockProvider

                self._bedrock_client = await asyncio.to_thread(
                    boto3.client,
                    "bedrock-runtime",
                    region_name=settings.aws_region,
                    config=Config(
                        connect_timeout=settings.timeout_seconds,
                        read_timeout=settings.timeout_seconds,
                        retries={
                            "max_attempts": settings.max_retries,
                            "mode": "standard",
                        },
                    ),
                )
                self._model = BedrockConverseModel(
                    settings.model_name,
                    provider=BedrockProvider(bedrock_client=self._bedrock_client),
                )
            else:
                key = (
                    await asyncio.to_thread(_resolve_openai_api_key, self.config)
                    if settings.auth_mode == "api_key"
                    else _no_api_key
                )
                self._client = AsyncOpenAI(
                    base_url=settings.endpoint_url,
                    api_key=key,
                    max_retries=settings.max_retries,
                    timeout=settings.timeout_seconds,
                    http_client=httpx.AsyncClient(
                        timeout=settings.timeout_seconds,
                        limits=httpx.Limits(max_connections=MAX_CONCURRENT_MODEL_CALLS),
                        follow_redirects=False,
                    ),
                )
                self._model = OpenAIChatModel(
                    settings.model_name,
                    provider=OpenAIProvider(openai_client=self._client),
                    settings={"extra_headers": {"Authorization": omit}}
                    if settings.auth_mode != "api_key"
                    else None,
                )
            return self._model

    async def __call__(self, prompt: ModelPrompt) -> str:
        from ato_service.ssp_workspace.model_policy import require_ssp_model_allowed

        require_ssp_model_allowed(self.config)
        if prompt.output_schema is None:
            raise TextModelConfigurationError("SSP model output schema is required")
        settings = resolve_text_model_settings(self.config)
        check_prompt_budget(
            system=prompt.system,
            user=prompt.user,
            schema=prompt.output_schema,
            context_tokens=int(
                self.config.document.get("TEXT_MODEL_CONTEXT_TOKENS", 8192)
            ),
            max_output_tokens=settings.max_output_tokens,
            utilization_target=self.config.context_budget_settings.utilization_target,
        )
        async with asyncio.timeout(settings.timeout_seconds):
            async with self._slots:
                model = await self._resolve_model()
                return await run_structured_step(
                    model,
                    system=prompt.system,
                    content=prompt.user,
                    schema=prompt.output_schema,
                    max_output_tokens=settings.max_output_tokens,
                    timeout_seconds=settings.timeout_seconds,
                    temperature=settings.temperature,
                )

    async def aclose(self) -> None:
        self._closed = True
        async with self._shutdown:
            acquired = 0
            try:
                # Drain admitted requests before closing their shared transport.
                # Do not hold initialization while waiting: admitted calls may
                # still need that lock to observe the closed state and exit.
                for _ in range(MAX_CONCURRENT_MODEL_CALLS):
                    await self._slots.acquire()
                    acquired += 1
                async with self._initialization:
                    if self._client is not None:
                        await self._client.close()
                    if self._bedrock_client is not None:
                        await asyncio.to_thread(self._bedrock_client.close)
            finally:
                for _ in range(acquired):
                    self._slots.release()


def build_ssp_model_adapter(config: RuntimeConfig) -> SspModelAdapter:
    """Build without accessing credentials or making network requests."""
    return SspModelAdapter(config)


class SspVisionAdapter(SspModelAdapter):
    """The same bounded native-output boundary for approved image observations."""

    async def _resolve_model(self) -> Model:
        from ato_service.ssp_workspace.vision import (
            resolve_vision_api_key,
            resolve_vision_model_settings,
        )

        async with self._initialization:
            if self._closed:
                raise TextModelCallError("SSP model runtime is closed")
            if self._model is not None:
                return self._model
            settings = resolve_vision_model_settings(self.config)
            key = await asyncio.to_thread(resolve_vision_api_key, self.config)
            self._client = AsyncOpenAI(
                base_url=settings.endpoint_url,
                api_key=key if key is not None else _no_api_key,
                max_retries=0,
                timeout=settings.timeout_seconds,
                http_client=httpx.AsyncClient(
                    timeout=settings.timeout_seconds,
                    limits=httpx.Limits(max_connections=MAX_CONCURRENT_MODEL_CALLS),
                    follow_redirects=False,
                ),
            )
            self._model = OpenAIChatModel(
                settings.model_name,
                provider=OpenAIProvider(openai_client=self._client),
                settings={"extra_headers": {"Authorization": omit}}
                if key is None
                else None,
            )
            return self._model

    async def __call__(self, prompt: VisionPrompt) -> str:
        from ato_service.ssp_workspace.model_policy import require_ssp_model_allowed
        from ato_service.ssp_workspace.vision import resolve_vision_model_settings

        require_ssp_model_allowed(self.config, vision=True)
        if prompt.output_schema is None:
            raise TextModelConfigurationError("SSP vision output schema is required")
        settings = resolve_vision_model_settings(self.config)
        # Images have already passed extraction limits. Read dimensions without
        # decoding pixels and reserve a conservative tile budget, not base64 size.
        with Image.open(BytesIO(prompt.image_bytes)) as image:
            image_tokens = (
                4096 + ceil(image.width / 512) * ceil(image.height / 512) * 256
            )
        check_prompt_budget(
            system=prompt.system,
            user=prompt.user,
            schema=prompt.output_schema,
            context_tokens=settings.context_tokens,
            max_output_tokens=settings.max_output_tokens,
            utilization_target=self.config.context_budget_settings.utilization_target,
            image_tokens=image_tokens,
        )
        async with asyncio.timeout(settings.timeout_seconds):
            async with self._slots:
                model = await self._resolve_model()
                return await run_structured_step(
                    model,
                    system=prompt.system,
                    content=[
                        prompt.user,
                        BinaryContent(
                            data=prompt.image_bytes, media_type=prompt.media_type
                        ),
                    ],
                    schema=prompt.output_schema,
                    max_output_tokens=settings.max_output_tokens,
                    timeout_seconds=settings.timeout_seconds,
                )
