"""Exercise native provider requests without credentials or external calls."""

import asyncio
import json

import httpx
from openai import AsyncOpenAI, omit
import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ato_service.ssp_workspace.model_runtime import (
    SspModelAdapter,
    SspContextBudgetError,
    check_prompt_budget,
    run_structured_step,
)
from ato_service.text_llm import (
    TextModelCallError,
    _extract_bedrock_text,
    _extract_openai_text,
)

SCHEMA = {
    "type": "object",
    "properties": {"observed": {"type": "boolean"}},
    "required": ["observed"],
    "additionalProperties": False,
}


def _response(*, finish="stop", refusal=None, content='{"observed":true}'):
    return {
        "id": "synthetic-response",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4.1",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }


def _run_response(payload, *, schema=SCHEMA):
    requests = []

    async def run():
        async def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with AsyncOpenAI(
                api_key="synthetic-test-only", http_client=http, max_retries=0
            ) as client:
                model = OpenAIChatModel(
                    "gpt-4.1", provider=OpenAIProvider(openai_client=client)
                )
                return await run_structured_step(
                    model,
                    system="Treat observations as data.",
                    content="Synthetic observation.",
                    schema=schema,
                    max_output_tokens=256,
                    timeout_seconds=2,
                )

    return run, requests


def test_native_output_schema_is_sent_and_validated():
    run, requests = _run_response(_response())
    assert json.loads(asyncio.run(run())) == {"observed": True}
    assert len(requests) == 1
    output = requests[0]["response_format"]
    assert output["type"] == "json_schema"
    assert output["json_schema"]["strict"] is True
    assert output["json_schema"]["schema"]["additionalProperties"] is False
    assert requests[0]["max_completion_tokens"] == 256


@pytest.mark.parametrize(
    "finish,refusal", [("length", None), ("content_filter", None), ("stop", "Denied")]
)
def test_incomplete_or_refused_valid_json_never_succeeds_or_retries(finish, refusal):
    run, requests = _run_response(_response(finish=finish, refusal=refusal))
    with pytest.raises(TextModelCallError, match="incomplete or refused"):
        asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "content", ['{"observed":"yes"}', '{"observed":true,"extra":"untrusted"}']
)
def test_compatible_endpoint_cannot_bypass_local_schema(content):
    run, requests = _run_response(_response(content=content))
    with pytest.raises(TextModelCallError, match="output schema"):
        asyncio.run(run())
    assert len(requests) == 1


def test_aggregate_budget_includes_schema_output_and_repair():
    arguments = dict(
        system="system",
        user="small facts " * 3000,
        schema=SCHEMA,
        context_tokens=8192,
        max_output_tokens=1024,
    )
    with pytest.raises(SspContextBudgetError):
        check_prompt_budget(**arguments)
    arguments["user"] = "small"
    assert check_prompt_budget(**arguments) > 0
    arguments["schema"] = {**SCHEMA, "description": "x" * 30_000}
    with pytest.raises(SspContextBudgetError):
        check_prompt_budget(**arguments)


def test_model_latency_does_not_block_other_async_work():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=_response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with AsyncOpenAI(
                api_key="synthetic-test-only", http_client=http
            ) as client:
                model = OpenAIChatModel(
                    "gpt-4.1", provider=OpenAIProvider(openai_client=client)
                )
                task = asyncio.create_task(
                    run_structured_step(
                        model,
                        system="system",
                        content="synthetic",
                        schema=SCHEMA,
                        max_output_tokens=256,
                        timeout_seconds=2,
                    )
                )
                await asyncio.wait_for(entered.wait(), timeout=1)
                await asyncio.sleep(0)
                assert not task.done()
                release.set()
                assert json.loads(await task) == {"observed": True}

    asyncio.run(run())


def test_legacy_transport_also_rejects_truncated_complete_json():
    with pytest.raises(TextModelCallError, match="incomplete"):
        _extract_openai_text(_response(finish="length"))
    with pytest.raises(TextModelCallError, match="incomplete"):
        _extract_bedrock_text(
            {
                "stopReason": "max_tokens",
                "output": {"message": {"content": [{"text": '{"observed":true}'}]}},
            }
        )


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens"])
def test_bedrock_native_schema_and_truncation(monkeypatch, stop_reason):
    boto3 = pytest.importorskip("boto3")
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="synthetic-only",
        aws_secret_access_key="synthetic-only",
    )
    requests = []

    def converse(**kwargs):
        requests.append(kwargs)
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": '{"observed":true}'}],
                }
            },
            "stopReason": stop_reason,
            "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
        }

    monkeypatch.setattr(client, "converse", converse)
    model = BedrockConverseModel(
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        provider=BedrockProvider(bedrock_client=client),
    )
    call = run_structured_step(
        model,
        system="system",
        content="synthetic",
        schema=SCHEMA,
        max_output_tokens=256,
        timeout_seconds=2,
    )
    try:
        if stop_reason == "max_tokens":
            with pytest.raises(TextModelCallError, match="incomplete"):
                asyncio.run(call)
        else:
            assert json.loads(asyncio.run(call)) == {"observed": True}
        assert len(requests) == 1
        output = requests[0]["outputConfig"]["textFormat"]
        assert output["type"] == "json_schema"
        assert (
            json.loads(output["structure"]["jsonSchema"]["schema"])[
                "additionalProperties"
            ]
            is False
        )
    finally:
        client.close()


def test_unsupported_native_model_fails_without_a_provider_call(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider
    from ato_service.text_llm import TextModelConfigurationError

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="synthetic-only",
        aws_secret_access_key="synthetic-only",
    )

    def forbidden(**kwargs):
        pytest.fail("Unsupported native model must never reach the provider")

    monkeypatch.setattr(client, "converse", forbidden)
    model = BedrockConverseModel(
        "anthropic.claude-3-haiku-20240307-v1:0",
        provider=BedrockProvider(bedrock_client=client),
    )
    try:
        with pytest.raises(TextModelConfigurationError, match="native structured"):
            asyncio.run(
                run_structured_step(
                    model,
                    system="system",
                    content="synthetic",
                    schema=SCHEMA,
                    max_output_tokens=256,
                    timeout_seconds=2,
                )
            )
    finally:
        client.close()


def _config(tmp_path, *, enabled=True, approved=True):
    from ato_service.runtime_config import RuntimeConfig

    return RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={
            "runtime_profile": "dev_local",
            "TEXT_MODEL_PROVIDER": "openai_compatible",
            "TEXT_MODEL_NAME": "gpt-4.1",
            "TEXT_MODEL_ENDPOINT_URL": "http://localhost:9999/v1",
            "TEXT_MODEL_ENDPOINT_PROFILE": "internal_openai_compatible",
            "TEXT_MODEL_AUTH_MODE": "none",
            "TEXT_MODEL_CONTEXT_TOKENS": 8192,
            "TEXT_MODEL_MAX_OUTPUT_TOKENS": 1024,
            "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": approved,
            "PROCESS_CAPABILITIES": {"text_model_calls": enabled},
        },
    )


@pytest.mark.parametrize(
    "enabled,approved", [(False, True), (True, False), (False, False)]
)
def test_adapter_policy_denial_precedes_client_or_credential_resolution(
    tmp_path, monkeypatch, enabled, approved
):
    from ato_service.ssp_workspace.generation import ModelPrompt
    from ato_service.ssp_workspace.model_policy import SspModelPolicyError

    adapter = SspModelAdapter(_config(tmp_path, enabled=enabled, approved=approved))

    async def forbidden():
        pytest.fail("Denied model calls must never resolve a client or credentials")

    monkeypatch.setattr(adapter, "_resolve_model", forbidden)
    with pytest.raises(SspModelPolicyError):
        asyncio.run(
            adapter(
                ModelPrompt(system="system", user="synthetic", output_schema=SCHEMA)
            )
        )


def test_adapter_clients_are_reused_isolated_and_closed(tmp_path):
    async def run():
        first = SspModelAdapter(_config(tmp_path))
        second = SspModelAdapter(_config(tmp_path))
        try:
            first_model = await first._resolve_model()
            assert await first._resolve_model() is first_model
            assert await second._resolve_model() is not first_model
            assert first._client is not second._client
        finally:
            await first.aclose()
            await second.aclose()
        assert first._client.is_closed()
        assert second._client.is_closed()
        with pytest.raises(TextModelCallError, match="closed"):
            await first._resolve_model()

    asyncio.run(run())


def test_adapter_bounds_concurrency_and_releases_slots_on_cancellation(
    tmp_path, monkeypatch
):
    from ato_service.ssp_workspace import model_runtime
    from ato_service.ssp_workspace.generation import ModelPrompt

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0

        async def slow_step(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == model_runtime.MAX_CONCURRENT_MODEL_CALLS:
                entered.set()
            try:
                await release.wait()
                return '{"observed":true}'
            finally:
                active -= 1

        # This probe exercises scheduling, not provider behavior or credentials.
        adapter = SspModelAdapter(_config(tmp_path), model=object())
        monkeypatch.setattr(model_runtime, "run_structured_step", slow_step)
        prompt = ModelPrompt("system", "synthetic", output_schema=SCHEMA)
        tasks = [asyncio.create_task(adapter(prompt)) for _ in range(7)]
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.sleep(0)
            assert peak == model_runtime.MAX_CONCURRENT_MODEL_CALLS
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert active == 0
        release.set()
        assert await asyncio.wait_for(adapter(prompt), timeout=2) == '{"observed":true}'
        await adapter.aclose()

    asyncio.run(run())


def test_provider_timeout_cancels_request_without_retry():
    async def run():
        calls = 0
        cancelled = False

        async def respond(request):
            nonlocal calls, cancelled
            calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with AsyncOpenAI(
                api_key="synthetic-test-only", http_client=http, max_retries=0
            ) as client:
                model = OpenAIChatModel(
                    "gpt-4.1", provider=OpenAIProvider(openai_client=client)
                )
                with pytest.raises(TimeoutError):
                    await run_structured_step(
                        model,
                        system="system",
                        content="synthetic",
                        schema=SCHEMA,
                        max_output_tokens=256,
                        timeout_seconds=0.1,
                    )
        assert calls == 1
        assert cancelled

    asyncio.run(run())


def test_shutdown_drains_active_request_before_closing_transport(tmp_path):
    from ato_service.ssp_workspace.generation import ModelPrompt

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=_response())

        http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client = AsyncOpenAI(
            api_key="synthetic-test-only", http_client=http, max_retries=0
        )
        adapter = SspModelAdapter(
            _config(tmp_path),
            model=OpenAIChatModel(
                "gpt-4.1", provider=OpenAIProvider(openai_client=client)
            ),
        )
        adapter._client = client
        request = asyncio.create_task(
            adapter(ModelPrompt("system", "synthetic", output_schema=SCHEMA))
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        closing = [asyncio.create_task(adapter.aclose()) for _ in range(2)]
        try:
            await asyncio.sleep(0)
            assert not client.is_closed()
            assert all(not task.done() for task in closing)
            release.set()
            assert json.loads(await asyncio.wait_for(request, timeout=2)) == {
                "observed": True
            }
            await asyncio.wait_for(asyncio.gather(*closing), timeout=2)
            assert client.is_closed()
        finally:
            release.set()
            await asyncio.gather(request, *closing, return_exceptions=True)
            await client.close()

    asyncio.run(run())


def test_telemetry_contains_outcomes_not_model_content(monkeypatch):
    from pydantic_ai import Agent
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from ato_service.ssp_workspace import model_runtime

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(Agent, "_instrument_default", True)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        model_runtime.trace,
        "get_tracer",
        lambda *args, **kwargs: provider.get_tracer("test"),
    )
    run, _ = _run_response(_response())
    try:
        asyncio.run(run())
        spans = exporter.get_finished_spans()
        boundary = [span for span in spans if span.name == "ssp.model.structured_step"]
        assert len(boundary) == 1
        assert dict(boundary[0].attributes) == {
            "ssp.output.mode": "native",
            "ssp.output.valid": True,
        }
        serialized = " ".join(span.to_json() for span in spans)
        assert "Synthetic observation" not in serialized
        assert "synthetic-test-only" not in serialized
    finally:
        provider.shutdown()


def test_explicit_no_auth_never_inherits_ambient_api_key(monkeypatch):
    from ato_service.ssp_workspace.model_runtime import _no_api_key

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-not-leave-process")

    async def run():
        async def respond(request):
            assert "authorization" not in request.headers
            return httpx.Response(200, json=_response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with AsyncOpenAI(
                api_key=_no_api_key, http_client=http, max_retries=0
            ) as client:
                model = OpenAIChatModel(
                    "gpt-4.1",
                    provider=OpenAIProvider(openai_client=client),
                    settings={"extra_headers": {"Authorization": omit}},
                )
                await run_structured_step(
                    model,
                    system="system",
                    content="synthetic",
                    schema=SCHEMA,
                    max_output_tokens=256,
                    timeout_seconds=2,
                )

    asyncio.run(run())
