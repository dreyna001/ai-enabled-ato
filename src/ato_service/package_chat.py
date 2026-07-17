"""Bounded grounded package chat over authorized search results."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import cache
from typing import Any, Sequence

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.blobs import BlobStore
from ato_service.citation_validation import (
    CitableSource,
    build_sealed_citable_sources,
    validate_citations,
)
from ato_service.db.models import (
    AnalysisRun,
    PackageRevision,
    PackageRevisionChatUsage,
    ReviewRevision,
    SealedPackageContent,
)
from ato_service.model_gateway import (
    ModelCallRequest,
    ModelCallResult,
    ModelCapability,
    invoke_model_call,
)
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.package_search_index import SearchChunkHit, search_revision_chunks
from ato_service.runtime_config import ChatLimits, RuntimeConfig

_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior)\s+instructions\b"),
    re.compile(r"(?i)\bsystem\s+prompt\b"),
    re.compile(r"(?i)\bgrant\s+(me\s+)?ato\b"),
    re.compile(r"(?i)\bapprove\s+(this\s+)?package\b"),
    re.compile(r"(?i)\brun\s+(shell|sql|command)\b"),
    re.compile(r"(?i)\b(browse|search)\s+the\s+web\b"),
    re.compile(r"(?i)\b(exfiltrate|leak|dump)\b"),
)
_AUTHORIZATION_REFUSAL = "authorization_decision"
_RISK_ACCEPTANCE_REFUSAL = "risk_acceptance"
_OFFICIAL_COMPLIANCE_REFUSAL = "official_compliance"
_OUT_OF_PACKAGE_REFUSAL = "out_of_package"
_UNSAFE_REFUSAL = "unsafe_instruction"
_RESPONSE_SCHEMA_VERSION = "1.0.0"


class ChatValidationError(Exception):
    error_code = "request_schema_invalid"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ChatLimitExceededError(Exception):
    error_code = "chat_limit_exceeded"


class ChatRateLimitExceededError(Exception):
    error_code = "request_rate_limit_exceeded"


class ChatContextValidationError(Exception):
    error_code = "request_schema_invalid"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ChatContext:
    revision: PackageRevision
    sealed: SealedPackageContent
    run: AnalysisRun
    review_revision: ReviewRevision | None


@dataclass(frozen=True, slots=True)
class ParsedChatResponse:
    answer: str
    citations: list[dict[str, Any]]


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "answer", "citations"],
        "properties": {
            "schema_version": {"const": _RESPONSE_SCHEMA_VERSION},
            "answer": {"type": "string", "minLength": 1, "maxLength": 12000},
            "citations": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "object"},
            },
        },
    }


@cache
def _response_validator() -> Draft202012Validator:
    schema = _response_schema()
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


def evaluate_refusal(*, question: str) -> str | None:
    """Return a refusal code when the question must be blocked before retrieval."""
    normalized = question.strip()
    if not normalized:
        return _OUT_OF_PACKAGE_REFUSAL
    lowered = normalized.lower()
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(normalized):
            if _looks_like_authorization_request(lowered):
                return _AUTHORIZATION_REFUSAL
            if _looks_like_risk_acceptance(lowered):
                return _RISK_ACCEPTANCE_REFUSAL
            if _looks_like_official_compliance(lowered):
                return _OFFICIAL_COMPLIANCE_REFUSAL
            return _UNSAFE_REFUSAL
    if _looks_like_authorization_request(lowered):
        return _AUTHORIZATION_REFUSAL
    if _looks_like_risk_acceptance(lowered):
        return _RISK_ACCEPTANCE_REFUSAL
    if _looks_like_official_compliance(lowered):
        return _OFFICIAL_COMPLIANCE_REFUSAL
    return None


def validate_question_length(*, question: str, limits: ChatLimits) -> None:
    measured = len(question)
    if limits.input_limit_unit == "characters" and measured > limits.input_limit_value:
        raise ChatValidationError("question exceeds configured input limit")
    if limits.input_limit_unit == "tokens":
        estimated_tokens = max(1, measured // 4)
        if estimated_tokens > limits.input_limit_value:
            raise ChatValidationError("question exceeds configured input limit")


async def load_chat_context(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    run_id: uuid.UUID,
    review_revision_id: uuid.UUID | None,
) -> ChatContext:
    revision = (
        await session.execute(
            select(PackageRevision).where(
                PackageRevision.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    if revision is None or revision.status != "ready":
        raise ChatContextValidationError("package revision is not available for chat")

    sealed = (
        await session.execute(
            select(SealedPackageContent).where(
                SealedPackageContent.package_revision_id == package_revision_id
            )
        )
    ).scalar_one_or_none()
    if sealed is None:
        raise ChatContextValidationError("sealed package content is required")

    run = (
        await session.execute(
            select(AnalysisRun).where(AnalysisRun.run_id == run_id)
        )
    ).scalar_one_or_none()
    if run is None or run.package_revision_id != package_revision_id:
        raise ChatContextValidationError("run_id is not valid for this package revision")

    review_revision = None
    if review_revision_id is not None:
        review_revision = (
            await session.execute(
                select(ReviewRevision).where(
                    ReviewRevision.review_revision_id == review_revision_id
                )
            )
        ).scalar_one_or_none()
        if review_revision is None or review_revision.run_id != run_id:
            raise ChatContextValidationError(
                "review_revision_id is not valid for the supplied run"
            )

    return ChatContext(
        revision=revision,
        sealed=sealed,
        run=run,
        review_revision=review_revision,
    )


async def reserve_chat_usage(
    session: AsyncSession,
    *,
    package_revision_id: uuid.UUID,
    principal: AuthenticatedPrincipal,
    limits: ChatLimits,
    now: datetime,
    estimated_tokens: int,
) -> None:
    usage = (
        await session.execute(
            select(PackageRevisionChatUsage)
            .where(
                PackageRevisionChatUsage.package_revision_id == package_revision_id,
                PackageRevisionChatUsage.actor_id == principal.actor_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    usage_date = now.astimezone(timezone.utc).date()
    if usage is None:
        if limits.turn_limit < 1:
            raise ChatLimitExceededError()
        if estimated_tokens > limits.daily_token_limit_per_user:
            raise ChatLimitExceededError()
        if limits.rate_limit_max_requests < 1:
            raise ChatRateLimitExceededError()
        session.add(
            PackageRevisionChatUsage(
                package_revision_id=package_revision_id,
                actor_id=principal.actor_id,
                rate_window_started_at=now,
                rate_window_count=1,
                turn_count=1,
                daily_token_count=estimated_tokens,
                usage_date=usage_date,
                updated_at=now,
            )
        )
        return

    if usage.usage_date != usage_date:
        usage.usage_date = usage_date
        usage.daily_token_count = 0

    if usage.turn_count >= limits.turn_limit:
        raise ChatLimitExceededError()

    window_elapsed = (now - usage.rate_window_started_at).total_seconds()
    if window_elapsed >= limits.rate_limit_window_seconds:
        usage.rate_window_started_at = now
        usage.rate_window_count = 0
    if usage.rate_window_count >= limits.rate_limit_max_requests:
        raise ChatRateLimitExceededError()

    if usage.daily_token_count + estimated_tokens > limits.daily_token_limit_per_user:
        raise ChatLimitExceededError()

    usage.rate_window_count += 1
    usage.turn_count += 1
    usage.daily_token_count += estimated_tokens
    usage.updated_at = now


async def chat_with_package(
    session: AsyncSession,
    *,
    config: RuntimeConfig,
    blob_store: BlobStore,
    principal: AuthenticatedPrincipal,
    context: ChatContext,
    question: str,
    limits: ChatLimits,
    now: datetime,
) -> dict[str, Any]:
    """Answer bounded questions using authorized chunks and optional model assistance."""
    validate_question_length(question=question, limits=limits)
    refusal_code = evaluate_refusal(question=question)
    if refusal_code is not None:
        return _refusal_response(refusal_code)

    estimated_tokens = max(1, len(question) // 4) + limits.max_retrieved_chunks * 750
    await reserve_chat_usage(
        session,
        package_revision_id=context.revision.package_revision_id,
        principal=principal,
        limits=limits,
        now=now,
        estimated_tokens=estimated_tokens,
    )

    search_page = await search_revision_chunks(
        session,
        package_revision_id=context.revision.package_revision_id,
        query=question,
        limit=limits.max_retrieved_chunks,
    )
    if not search_page.items:
        return _refusal_response(_OUT_OF_PACKAGE_REFUSAL)

    sources = build_sealed_citable_sources(
        sealed_document=context.sealed.document,
        field_provenance=context.sealed.field_provenance,
    )
    sources = _sources_for_hits(hits=search_page.items, base_sources=sources)
    if await _model_chat_allowed(config=config, revision=context.revision):
        try:
            answer = await _model_grounded_answer(
                config=config,
                revision=context.revision,
                question=question,
                hits=search_page.items,
                sources=sources,
            )
            return answer
        except Exception:
            pass

    return _deterministic_grounded_answer(
        question=question,
        hits=search_page.items,
        sealed_document=context.sealed.document,
        sources=sources,
    )


def _sources_for_hits(
    *,
    hits: Sequence[SearchChunkHit],
    base_sources: dict[str, CitableSource],
) -> dict[str, CitableSource]:
    sources = dict(base_sources)
    for hit in hits:
        source_id = str(hit.artifact_id)
        if source_id not in sources:
            sources[source_id] = CitableSource(
                source_id=source_id,
                source_sha256=hit.citation["source_sha256"],
                text=hit.text,
            )
    return sources


def _deterministic_grounded_answer(
    *,
    question: str,
    hits: Sequence[SearchChunkHit],
    sealed_document: dict[str, Any],
    sources: dict[str, CitableSource],
) -> dict[str, Any]:
    citations = [_hit_citation(hit) for hit in hits[: min(3, len(hits))]]

    system_name = ""
    system = sealed_document.get("system")
    if isinstance(system, dict):
        system_name = str(system.get("display_name") or "")

    excerpts = []
    for hit in hits[:3]:
        excerpt = _trusted_excerpt(hit)
        if excerpt:
            excerpts.append(excerpt)
    joined = " | ".join(excerpts)
    prefix = (
        f"Based on the authorized package for {system_name}, "
        if system_name
        else "Based on the authorized package, "
    )
    answer = (
        f"{prefix}the closest matching content for your question is: {joined}"
    )[:12000]
    return {
        "answer": answer,
        "citations": citations,
        "refused": False,
        "refusal_code": None,
    }


def _chat_model_request(*, config: RuntimeConfig, revision: PackageRevision) -> ModelCallRequest:
    endpoint_profile = _endpoint_profile(config)
    return ModelCallRequest(
        capability=ModelCapability.PACKAGE_CHAT,
        data_origin=DataOrigin(revision.data_origin),
        sensitivity=Sensitivity(revision.sensitivity),
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=config.document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is True,
        cui_boundary_approved=config.document.get("CUI_MODEL_BOUNDARY_APPROVED") is True,
        vision_model_enabled=bool(config.document.get("VISION_MODEL_ENABLED")),
        current_llm_call_count=0,
        max_llm_calls=2,
    )


async def _invoke_chat_completion(
    *,
    request: ModelCallRequest,
    config: RuntimeConfig,
    messages: Sequence[Any],
    system: str,
) -> tuple[str, int]:
    from ato_service.text_llm import build_text_model_client

    client = build_text_model_client(config)

    async def _callback() -> str:
        return client.complete(list(messages), system=system)

    result: ModelCallResult[str] = await invoke_model_call(request, _callback)
    return result.value, result.llm_call_count


def _format_model_answer(
    *,
    raw: str,
    hits: Sequence[SearchChunkHit],
    sources: dict[str, CitableSource],
) -> dict[str, Any]:
    parsed = _parse_chat_response(raw)
    validate_citations(citations=parsed.citations, sources=sources)
    rendered = []
    for citation in parsed.citations:
        rendered.append(
            {
                **citation,
                "excerpt": _render_excerpt(citation=citation, hits=hits, sources=sources),
            }
        )
    return {
        "answer": parsed.answer[:12000],
        "citations": [
            {key: value for key, value in item.items() if key != "excerpt"} for item in rendered
        ],
        "refused": False,
        "refusal_code": None,
    }


async def _model_grounded_answer(
    *,
    config: RuntimeConfig,
    revision: PackageRevision,
    question: str,
    hits: Sequence[SearchChunkHit],
    sources: dict[str, CitableSource],
) -> dict[str, Any]:
    from ato_service.text_llm import ChatMessage

    base_request = _chat_model_request(config=config, revision=revision)
    prompt = _build_chat_prompt(question=question, hits=hits)
    raw, llm_call_count = await _invoke_chat_completion(
        request=base_request,
        config=config,
        messages=[ChatMessage(role="user", content=question)],
        system=prompt,
    )

    try:
        return _format_model_answer(raw=raw, hits=hits, sources=sources)
    except ChatValidationError:
        if llm_call_count >= base_request.max_llm_calls:
            raise
        repair_prompt = (
            "Repair the previous response into strict JSON with keys "
            "schema_version, answer, citations. citations must only reference "
            "supplied chunk ids and offsets."
        )
        raw, _ = await _invoke_chat_completion(
            request=replace(base_request, current_llm_call_count=llm_call_count),
            config=config,
            messages=[
                ChatMessage(role="user", content=question),
                ChatMessage(role="assistant", content=raw),
                ChatMessage(role="user", content=repair_prompt),
            ],
            system=prompt,
        )
        return _format_model_answer(raw=raw, hits=hits, sources=sources)


async def _model_chat_allowed(*, config: RuntimeConfig, revision: PackageRevision) -> bool:
    from ato_service.process_capabilities import resolve_process_capabilities
    from ato_service.text_llm import text_model_is_configured

    capabilities = resolve_process_capabilities(config.document)
    if capabilities is not None and not capabilities.text_model_calls:
        return False
    if not text_model_is_configured(config.document):
        return False
    endpoint_profile = _endpoint_profile(config)
    if revision.sensitivity == "classified":
        return False
    if endpoint_profile is EndpointProfile.EXTERNAL_OPENAI and revision.data_origin == "customer_production":
        return False
    if config.document.get("runtime_profile") == "onprem_production":
        if config.document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is not True:
            return False
    return True


def _endpoint_profile(config: RuntimeConfig) -> EndpointProfile:
    profile = config.document.get("TEXT_MODEL_ENDPOINT_PROFILE", "mock")
    if profile == "external_openai":
        return EndpointProfile.EXTERNAL_OPENAI
    if profile == "internal_openai_compatible":
        return EndpointProfile.INTERNAL_OPENAI_COMPATIBLE
    return EndpointProfile.MOCK


def _build_chat_prompt(*, question: str, hits: Sequence[SearchChunkHit]) -> str:
    chunks = []
    for hit in hits:
        chunks.append(
            json.dumps(
                {
                    "chunk_id": hit.chunk_id,
                    "artifact_id": str(hit.artifact_id),
                    "citation": hit.citation,
                    "text": _trusted_excerpt(hit),
                },
                sort_keys=True,
            )
        )
    return (
        "You are a bounded package assistant. Answer only from supplied chunks. "
        "Do not authorize, certify, browse the web, run tools, or write data. "
        "Return strict JSON with schema_version, answer, and citations. "
        "Every factual claim must include citations referencing supplied chunk ids "
        "and offsets only.\n\n"
        f"Question: {question}\n\n"
        f"Authorized chunks:\n" + "\n".join(chunks)
    )


def _parse_chat_response(raw_text: str) -> ParsedChatResponse:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ChatValidationError("model response was not valid JSON") from exc
    errors = sorted(_response_validator().iter_errors(payload), key=lambda item: item.path)
    if errors:
        raise ChatValidationError(errors[0].message)
    citations = payload.get("citations")
    if not isinstance(citations, list):
        raise ChatValidationError("citations must be an array")
    normalized_citations = [entry for entry in citations if isinstance(entry, dict)]
    answer = payload.get("answer")
    if not isinstance(answer, str):
        raise ChatValidationError("answer must be a string")
    return ParsedChatResponse(answer=answer, citations=normalized_citations)


def _hit_citation(hit: SearchChunkHit) -> dict[str, Any]:
    return dict(hit.citation)


def _trusted_excerpt(hit: SearchChunkHit) -> str:
    return hit.text[:500]


def _render_excerpt(
    *,
    citation: dict[str, Any],
    hits: Sequence[SearchChunkHit],
    sources: dict[str, CitableSource],
) -> str:
    chunk_id = citation.get("chunk_id")
    for hit in hits:
        if hit.chunk_id != chunk_id:
            continue
        source = sources.get(str(hit.artifact_id)) or sources.get(
            str(citation.get("source_id", ""))
        )
        start = citation.get("start_offset")
        end = citation.get("end_offset")
        if source is not None and isinstance(start, int) and isinstance(end, int):
            if 0 <= start < end <= len(source.text):
                return source.text[start:end]
        return hit.text[:500]
    return ""


def _refusal_response(code: str) -> dict[str, Any]:
    messages = {
        _AUTHORIZATION_REFUSAL: "This assistant cannot perform authorization, approval, or unsafe actions.",
        _RISK_ACCEPTANCE_REFUSAL: "This assistant cannot perform risk acceptance or authorization decisions.",
        _OFFICIAL_COMPLIANCE_REFUSAL: "This assistant cannot make official compliance determinations.",
        _OUT_OF_PACKAGE_REFUSAL: "No authorized package content matched the question.",
        _UNSAFE_REFUSAL: "This assistant cannot perform authorization, approval, or unsafe actions.",
    }
    return {
        "answer": messages.get(code, messages[_UNSAFE_REFUSAL]),
        "citations": [],
        "refused": True,
        "refusal_code": code,
    }


def _looks_like_authorization_request(text: str) -> bool:
    return any(
        token in text
        for token in (
            "grant ato",
            "approve this package",
            "authorization decision",
            "official compliance",
        )
    )


def _looks_like_risk_acceptance(text: str) -> bool:
    return "risk acceptance" in text or "accept this risk" in text


def _looks_like_official_compliance(text: str) -> bool:
    return "official compliance" in text or "certify compliance" in text


__all__ = [
    "ChatContext",
    "ChatContextValidationError",
    "ChatLimitExceededError",
    "ChatRateLimitExceededError",
    "ChatValidationError",
    "chat_with_package",
    "evaluate_refusal",
    "load_chat_context",
    "reserve_chat_usage",
]
