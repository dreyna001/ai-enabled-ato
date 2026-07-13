"""Model-call wrapper for normalize_proposal using model_gateway and text_llm."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

from ato_service.model_gateway import (
    ClassifiedDataUnsupportedError,
    ModelCallLimitExceededError,
    ModelCallRequest,
    ModelCallResult,
    ModelCapability,
    ModelPolicyNotApprovedError,
    ModelPolicyOrderingError,
    ModelRoutingDeniedError,
    ProhibitedModelActionError,
    invoke_model_call,
)
from ato_service.normalize_proposal.constants import MAX_LLM_CALLS, sha256_text
from ato_service.normalize_proposal.prompt import (
    build_repair_prompt,
    build_system_prompt,
    build_user_prompt,
)
from ato_service.normalize_proposal.types import FactBundle, ModelCallMetadata
from ato_service.text_llm import (
    ChatMessage,
    TextModelCallError,
    TextModelClient,
    TextModelConfigurationError,
)

BeforeCallHook = Callable[[int], Awaitable[None]]
TextClientFactory = Callable[[], TextModelClient]


class NormalizeModelRoutingError(Exception):
    """Routing denied a normalize_proposal model call."""

    def __init__(self, *, error_code: str, llm_call_count: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.llm_call_count = llm_call_count


class NormalizeModelCallError(Exception):
    """Text model transport or configuration failed after callback invocation."""

    def __init__(
        self,
        *,
        error_code: str,
        llm_call_count: int,
        detail: str | None = None,
    ) -> None:
        super().__init__(detail or error_code)
        self.error_code = error_code
        self.llm_call_count = llm_call_count
        self.detail = detail


async def invoke_normalize_model(
    *,
    request: ModelCallRequest,
    bundle: FactBundle,
    max_output_tokens: int,
    repair_errors: Sequence[str] | None = None,
    prior_response: str | None = None,
    text_client: TextModelClient | None = None,
    client_factory: TextClientFactory | None = None,
    before_call: BeforeCallHook | None = None,
) -> tuple[str, ModelCallMetadata, int]:
    """Invoke one routed normalize_proposal text model call."""
    if request.capability is not ModelCapability.NORMALIZE_PROPOSAL:
        raise ValueError("capability must be NORMALIZE_PROPOSAL")
    if request.max_llm_calls > MAX_LLM_CALLS:
        raise ValueError(f"max_llm_calls must not exceed {MAX_LLM_CALLS}")
    if repair_errors is not None and prior_response is None:
        raise ValueError("prior_response is required for repair calls")
    if text_client is None and client_factory is None:
        raise ValueError("text_client or client_factory is required")
    if text_client is not None and client_factory is not None:
        raise ValueError("provide only one of text_client or client_factory")

    attempt = request.current_llm_call_count + 1
    system = build_system_prompt()
    if repair_errors is not None:
        user_content = build_repair_prompt(
            bundle=bundle,
            validation_errors=tuple(repair_errors),
            prior_response=prior_response or "",
            max_output_tokens=max_output_tokens,
        )
    else:
        user_content = build_user_prompt(bundle=bundle)

    elapsed_ms: int | None = None

    async def _timed_callback() -> str:
        nonlocal elapsed_ms
        if before_call is not None:
            await before_call(attempt)
        try:
            client = client_factory() if client_factory is not None else text_client
        except TextModelConfigurationError as exc:
            raise NormalizeModelCallError(
                error_code="model_not_configured",
                llm_call_count=request.current_llm_call_count + 1,
                detail=str(exc),
            ) from exc
        assert client is not None
        started = time.monotonic()
        try:
            return await asyncio.to_thread(
                client.complete,
                [ChatMessage(role="user", content=user_content)],
                system=system,
            )
        except TextModelConfigurationError as exc:
            raise NormalizeModelCallError(
                error_code="model_not_configured",
                llm_call_count=request.current_llm_call_count + 1,
                detail=str(exc),
            ) from exc
        except TextModelCallError as exc:
            raise NormalizeModelCallError(
                error_code="model_call_failed",
                llm_call_count=request.current_llm_call_count + 1,
                detail=str(exc),
            ) from exc
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)

    try:
        result: ModelCallResult[str] = await invoke_model_call(request, _timed_callback)
    except NormalizeModelCallError:
        raise
    except (
        ModelRoutingDeniedError,
        ClassifiedDataUnsupportedError,
        ModelPolicyNotApprovedError,
        ProhibitedModelActionError,
    ) as exc:
        raise NormalizeModelRoutingError(
            error_code=exc.error_code,
            llm_call_count=exc.llm_call_count,
        ) from exc
    except ModelPolicyOrderingError as exc:
        raise NormalizeModelRoutingError(
            error_code="model_policy_ordering",
            llm_call_count=exc.llm_call_count,
        ) from exc
    except ModelCallLimitExceededError as exc:
        raise NormalizeModelRoutingError(
            error_code=exc.error_code,
            llm_call_count=exc.llm_call_count,
        ) from exc

    raw = result.value
    metadata = ModelCallMetadata(
        attempt=attempt,
        raw_response=raw,
        response_sha256=sha256_text(raw),
        latency_ms=elapsed_ms,
    )
    return raw, metadata, result.llm_call_count


def normalize_model_request(base: ModelCallRequest) -> ModelCallRequest:
    """Return a normalize_proposal request capped to two calls."""
    return replace(
        base,
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        max_llm_calls=min(base.max_llm_calls, MAX_LLM_CALLS),
    )
