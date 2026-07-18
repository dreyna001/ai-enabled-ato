"""Contract tests for bounded package chat model routing and call budgets."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ato_service.citation_validation import (
    build_evidence_citation,
    build_sealed_citable_sources,
    derive_chunk_id,
)
from ato_service.model_gateway import ModelPolicyNotApprovedError
from ato_service.package_chat import (
    ChatValidationError,
    _build_chat_prompt,
    _chat_model_request,
    _model_grounded_answer,
    _select_hits_for_context_budget,
)
from ato_service.package_search_index import SearchChunkHit
from ato_service.runtime_config import RuntimeConfig, load_runtime_config_from_dict
from ato_service.text_llm import ChatMessage, TextModelCallError

ROOT = Path(__file__).resolve().parents[2]
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ARTIFACT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _chat_config(tmp_path: Path, **overrides: Any):
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        "TEXT_MODEL_ENDPOINT_URL": "https://mock.local/v1",
        "TEXT_MODEL_NAME": "mock-chat",
        "TEXT_MODEL_CONTEXT_TOKENS": 8192,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS": 1024,
        "TEXT_MODEL_TIMEOUT_SECONDS": 30,
        "TEXT_MODEL_ENDPOINT_PROFILE": "internal_openai_compatible",
        "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
        "CUI_MODEL_BOUNDARY_APPROVED": False,
    }
    document.update(overrides)
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _runtime_config_document(tmp_path: Path, **overrides: Any) -> RuntimeConfig:
    document = {
        "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
        "CUI_MODEL_BOUNDARY_APPROVED": False,
        **overrides,
    }
    return RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=tmp_path,
        document=document,
    )


def _package_revision(*, sensitivity: str = "internal_unclassified"):
    from datetime import datetime, timezone

    from ato_service.db.models import PackageRevision

    now = datetime.now(timezone.utc)
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=uuid.uuid4(),
        parent_revision_id=None,
        profile_id="fedramp_20x_program",
        certification_class="C",
        impact_level=None,
        data_origin="synthetic",
        sensitivity=sensitivity,
        effective_data_labels=[sensitivity, "synthetic"],
        authority_manifest_id="authority.v2",
        content_manifest_sha256="a" * 64,
        package_content_sha256="b" * 64,
        revision_version=1,
        status="ready",
        created_by="tester",
        created_at=now,
    )


def _document() -> dict[str, Any]:
    return {
        "security_controls": {
            "AC-2": {
                "implementation_statement": "Access control policy is implemented for all users.",
            }
        },
        "evidence": {},
    }


def _sources() -> dict[str, Any]:
    provenance = {
        "/security_controls/AC-2/implementation_statement": {
            "source_artifact_id": str(ARTIFACT_ID),
            "source_sha256": "b" * 64,
        }
    }
    return build_sealed_citable_sources(
        sealed_document=_document(),
        field_provenance=provenance,
    )


def _search_hit(*, chunk_id: str | None = None, text: str | None = None) -> SearchChunkHit:
    sources = _sources()
    source = sources[str(ARTIFACT_ID)]
    excerpt = source.text[0:20]
    citation = build_evidence_citation(source=source, start_offset=0, end_offset=20)
    resolved_chunk_id = chunk_id or derive_chunk_id(
        source_sha256=source.source_sha256,
        start_offset=0,
        end_offset=20,
        text=excerpt,
    )
    citation["chunk_id"] = resolved_chunk_id
    return SearchChunkHit(
        chunk_id=resolved_chunk_id,
        artifact_id=ARTIFACT_ID,
        score=1.0,
        citation=citation,
        text=text if text is not None else source.text[:500],
    )


def _valid_chat_response(*, chunk_id: str | None = None) -> str:
    sources = _sources()
    source = sources[str(ARTIFACT_ID)]
    citation = build_evidence_citation(source=source, start_offset=0, end_offset=20)
    excerpt = source.text[0:20]
    citation["chunk_id"] = chunk_id or derive_chunk_id(
        source_sha256=source.source_sha256,
        start_offset=0,
        end_offset=20,
        text=excerpt,
    )
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "answer": "Access control policy is implemented for all users.",
            "citations": [citation],
        }
    )


@dataclass
class FakeTextClient:
    responses: list[str] = field(default_factory=list)
    calls: int = 0

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
    ) -> str:
        self.calls += 1
        if not self.responses:
            raise TextModelCallError("no fake response configured")
        return self.responses.pop(0)


def test_chat_model_request_uses_canonical_cui_key(tmp_path: Path) -> None:
    config = _runtime_config_document(
        tmp_path,
        CUI_BOUNDARY_APPROVED=True,
        CUI_MODEL_BOUNDARY_APPROVED=False,
    )
    revision = _package_revision(sensitivity="cui")
    request = _chat_model_request(config=config, revision=revision)
    assert request.cui_boundary_approved is False


def test_model_grounded_answer_denies_cui_without_canonical_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _chat_config(tmp_path, CUI_MODEL_BOUNDARY_APPROVED=False)
    revision = _package_revision(sensitivity="cui")
    client = FakeTextClient(responses=[_valid_chat_response()])
    monkeypatch.setattr(
        "ato_service.text_llm.build_text_model_client",
        lambda _config: client,
    )

    with pytest.raises(ModelPolicyNotApprovedError):
        _run(
            _model_grounded_answer(
                config=config,
                revision=revision,
                question="What access controls are implemented?",
                hits=[_search_hit()],
                sources=_sources(),
            )
        )

    assert client.calls == 0


def test_model_grounded_answer_allows_cui_with_canonical_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _chat_config(tmp_path, CUI_MODEL_BOUNDARY_APPROVED=True)
    revision = _package_revision(sensitivity="cui")
    client = FakeTextClient(responses=[_valid_chat_response()])
    monkeypatch.setattr(
        "ato_service.text_llm.build_text_model_client",
        lambda _config: client,
    )

    result = _run(
        _model_grounded_answer(
            config=config,
            revision=revision,
            question="What access controls are implemented?",
            hits=[_search_hit()],
            sources=_sources(),
        )
    )

    assert result["refused"] is False
    assert client.calls == 1


def test_model_grounded_answer_repair_uses_two_metered_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _chat_config(tmp_path)
    revision = _package_revision()
    client = FakeTextClient(responses=["not-json", _valid_chat_response()])
    invoke_counts: list[int] = []
    real_invoke = __import__(
        "ato_service.package_chat", fromlist=["invoke_model_call"]
    ).invoke_model_call

    async def counting_invoke(request, callback):
        invoke_counts.append(request.current_llm_call_count)
        return await real_invoke(request, callback)

    monkeypatch.setattr("ato_service.package_chat.invoke_model_call", counting_invoke)
    monkeypatch.setattr(
        "ato_service.text_llm.build_text_model_client",
        lambda _config: client,
    )

    result = _run(
        _model_grounded_answer(
            config=config,
            revision=revision,
            question="What access controls are implemented?",
            hits=[_search_hit()],
            sources=_sources(),
        )
    )

    assert result["refused"] is False
    assert invoke_counts == [0, 1]
    assert client.calls == 2


def test_model_grounded_answer_repair_stops_after_two_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _chat_config(tmp_path)
    revision = _package_revision()
    client = FakeTextClient(responses=["not-json", "still-not-json"])
    invoke_counts: list[int] = []
    real_invoke = __import__(
        "ato_service.package_chat", fromlist=["invoke_model_call"]
    ).invoke_model_call

    async def counting_invoke(request, callback):
        invoke_counts.append(request.current_llm_call_count)
        return await real_invoke(request, callback)

    monkeypatch.setattr("ato_service.package_chat.invoke_model_call", counting_invoke)
    monkeypatch.setattr(
        "ato_service.text_llm.build_text_model_client",
        lambda _config: client,
    )

    with pytest.raises(ChatValidationError):
        _run(
            _model_grounded_answer(
                config=config,
                revision=revision,
                question="What access controls are implemented?",
                hits=[_search_hit()],
                sources=_sources(),
            )
        )

    assert invoke_counts == [0, 1]
    assert client.calls == 2


def test_chat_prompt_omits_lower_ranked_hits_when_budget_tight() -> None:
    hits = [
        _search_hit(chunk_id=f"chunk-{index}", text=f"chunk-{index} " + ("word " * 700))
        for index in range(3)
    ]

    included = _select_hits_for_context_budget(
        question="What access controls are implemented?",
        hits=hits,
        input_budget_tokens=400,
    )

    assert len(included) < len(hits)
    assert included == tuple(hits[: len(included)])
    prompt = _build_chat_prompt(
        question="What access controls are implemented?",
        hits=included,
    )
    assert hits[-1].chunk_id not in prompt


def test_chat_prompt_rejects_when_no_authorized_chunk_fits() -> None:
    hit = _search_hit(text="word " * 700)

    with pytest.raises(
        ChatValidationError,
        match="cannot fit an authorized chat chunk",
    ):
        _select_hits_for_context_budget(
            question="What access controls are implemented?",
            hits=[hit],
            input_budget_tokens=100,
        )


def test_chat_prompt_rejects_fixed_payload_over_budget_without_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _chat_config(
        tmp_path,
        TEXT_MODEL_CONTEXT_TOKENS=64,
        TEXT_MODEL_MAX_OUTPUT_TOKENS=16,
        CONTEXT_UTILIZATION_TARGET=1.0,
    )
    revision = _package_revision()
    client = FakeTextClient(responses=[_valid_chat_response()])
    monkeypatch.setattr(
        "ato_service.text_llm.build_text_model_client",
        lambda _config: client,
    )

    with pytest.raises(ChatValidationError, match="cannot fit chat prompt"):
        _run(
            _model_grounded_answer(
                config=config,
                revision=revision,
                question="What access controls are implemented?" + (" extra" * 200),
                hits=[_search_hit()],
                sources=_sources(),
            )
        )

    assert client.calls == 0
