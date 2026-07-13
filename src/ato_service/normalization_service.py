"""Bounded intake normalization orchestration for Component A Diff 4."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ato_service.db.session import session_scope
from ato_service.draft_builder import DOCUMENT_SCHEMA_VERSION, AggregatedIntakeDraft
from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment
from ato_service.intake_work import (
    IntakeLeaseLostError,
    assert_intake_claim_live,
    mark_intake_work_reconciliation_required,
)
from ato_service.model_gateway import ModelCallRequest, ModelCapability
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.normalization_artifacts import (
    NormalizationArtifactError,
    StoredNormalizationArtifact,
    write_normalization_protected_artifact,
)
from ato_service.normalize_proposal.constants import (
    PROMPT_VERSION,
    RESPONSE_SCHEMA_VERSION,
    sha256_text,
)
from ato_service.normalize_proposal.fact_bundle import ContextLimitExceededError, build_fact_bundle
from ato_service.normalize_proposal.json_utils import stable_json_dumps
from ato_service.normalize_proposal.prompt import (
    build_system_prompt,
    build_user_prompt,
    prompt_contract_metadata,
)
from ato_service.normalize_proposal.runner import run_normalize_proposal
from ato_service.normalize_proposal.target_catalog import list_empty_targets
from ato_service.normalize_proposal.types import ArtifactFacts, NormalizeProposalResult, SegmentFact
from ato_service.db.models import PackageNormalizationStep, PackageRevisionIntakeWork
from ato_service.runtime_config import RuntimeConfig
from ato_service.text_llm import TextModelClient, build_text_model_client, text_model_is_configured

if TYPE_CHECKING:
    from ato_service.intake import (
        ArtifactSnapshot,
        ClaimedIntakeOperation,
        IntakeRevisionSnapshot,
    )

NORMALIZATION_STEP_KEY = "normalize_proposal"
FACT_BUNDLE_SCHEMA_ID = "https://ato.local/schemas/normalize-proposal-fact-bundle.schema.json"
RESPONSE_SCHEMA_ID = "https://ato.local/schemas/normalize-proposal-response.schema.json"

MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_ARTIFACT_BYTES = 512 * 1024

PRE_TERMINAL_STEP_STATUSES = frozenset({"reserved", "running"})
REPLAY_RECONCILIATION_STEP_STATUSES = frozenset(
    {"running", "completed", "policy_blocked", "failed", "reconciliation_required"}
)

UtcNowFactory = Callable[[], datetime]


class NormalizationLeaseLostError(Exception):
    """Intake lease or normalization reservation is no longer live."""


class NormalizationInvariantError(Exception):
    """Normalization step state violates intake invariants."""

    def __init__(self, *, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class NormalizationDependencies:
    """Injectable normalization runtime dependencies."""

    config: RuntimeConfig
    storage_root: Path
    text_client_factory: Callable[[RuntimeConfig], TextModelClient]


@dataclass(frozen=True, slots=True)
class StoredNormalizationArtifacts:
    """Protected artifact metadata written outside DB transactions."""

    prompt: StoredNormalizationArtifact | None
    fact_bundle: StoredNormalizationArtifact | None
    response: StoredNormalizationArtifact | None


@dataclass(frozen=True, slots=True)
class PendingNormalizationOutcome:
    """Normalization result pending atomic intake commit."""

    skipped: bool
    reconciliation_required: bool
    step_id: uuid.UUID | None
    input_digest: str | None
    result: NormalizeProposalResult | None
    deterministic_draft: AggregatedIntakeDraft
    protected_artifacts: StoredNormalizationArtifacts | None
    runtime_metadata: dict[str, Any] | None


def default_text_client_factory(config: RuntimeConfig) -> TextModelClient:
    """Build the configured text-model client lazily at call time."""
    return build_text_model_client(config)


def build_artifact_facts(
    *,
    artifacts: Sequence[ArtifactSnapshot],
    artifact_outcomes: Sequence[tuple[ArtifactSnapshot, ExtractionOutcome]],
) -> tuple[ArtifactFacts, ...]:
    """Build verified ArtifactFacts from extraction outcomes."""
    _require_complete_artifact_outcomes(
        artifacts=artifacts,
        artifact_outcomes=artifact_outcomes,
    )
    outcome_by_id = {artifact.artifact_id: outcome for artifact, outcome in artifact_outcomes}
    facts: list[ArtifactFacts] = []
    for artifact in artifacts:
        outcome = outcome_by_id[artifact.artifact_id]
        segments = tuple(_segment_fact(segment) for segment in outcome.segments)
        facts.append(
            ArtifactFacts(
                artifact_id=artifact.artifact_id,
                sha256=artifact.sha256,
                filename=artifact.display_filename,
                detected_format=outcome.detected_format,
                segments=segments,
            )
        )
    return tuple(facts)


def normalization_needed(
    *,
    profile_id: str,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
    artifacts: Sequence[ArtifactFacts],
) -> bool:
    """Return whether bounded normalization should reserve a step."""
    empty_targets = list_empty_targets(
        profile_id=profile_id,
        document=document,
        field_provenance=field_provenance,
    )
    if not empty_targets:
        return False
    return any(artifact.segments for artifact in artifacts)


def compute_draft_digest(*, document: dict[str, Any], field_provenance: dict[str, Any]) -> str:
    """Return SHA-256 of the deterministic draft document and provenance."""
    payload = {
        "document_schema_version": DOCUMENT_SCHEMA_VERSION,
        "document": document,
        "field_provenance": field_provenance,
    }
    return sha256_text(stable_json_dumps(payload))


def compute_input_digest(
    *,
    package_revision_id: uuid.UUID,
    revision_version: int,
    profile_id: str,
    content_manifest_sha256: str,
    artifacts: Sequence[ArtifactSnapshot],
    draft_digest: str,
    fact_bundle_digest: str | None,
) -> str:
    """Return canonical reservation digest for one normalization step."""
    artifact_entries = [
        {
            "artifact_id": str(artifact.artifact_id).lower(),
            "sha256": artifact.sha256,
        }
        for artifact in sorted(artifacts, key=lambda item: str(item.artifact_id))
    ]
    payload = {
        "package_revision_id": str(package_revision_id).lower(),
        "revision_version": revision_version,
        "profile_id": profile_id,
        "content_manifest_sha256": content_manifest_sha256,
        "document_schema_version": DOCUMENT_SCHEMA_VERSION,
        "artifacts": artifact_entries,
        "draft_digest": draft_digest,
        "fact_bundle_digest": fact_bundle_digest,
    }
    return sha256_text(stable_json_dumps(payload))


def build_fact_bundle_envelope(
    *,
    package_revision_id: uuid.UUID,
    bundle_payload: dict[str, Any],
) -> dict[str, Any]:
    """Return canonical protected fact-bundle envelope with integration metadata."""
    contract = prompt_contract_metadata()
    return {
        "package_revision_id": str(package_revision_id).lower(),
        "prompt_version": contract["prompt_version"],
        "response_schema_version": contract["response_schema_version"],
        "schema_id": FACT_BUNDLE_SCHEMA_ID,
        "system_prompt_sha256": contract["prompt_sha256"],
        "fact_bundle": bundle_payload,
    }


def validate_fact_bundle_envelope(envelope: dict[str, Any]) -> None:
    """Validate the core fact bundle against the published schema."""
    bundle = envelope.get("fact_bundle")
    if not isinstance(bundle, dict):
        raise ValueError("fact bundle envelope requires a fact_bundle object")
    errors = sorted(_fact_bundle_validator().iter_errors(bundle))
    if errors:
        raise ValueError(errors[0].message)


def build_prompt_artifact_payload(
    *,
    bundle: Any,
) -> dict[str, Any]:
    """Return bounded prompt artifact JSON without secrets."""
    contract = prompt_contract_metadata()
    system = build_system_prompt()
    user = build_user_prompt(bundle=bundle)
    return {
        "prompt_version": contract["prompt_version"],
        "system_prompt_sha256": contract["prompt_sha256"],
        "response_schema_version": contract["response_schema_version"],
        "messages": {
            "system": system,
            "user": user,
        },
    }


def build_response_envelope(
    *,
    model_calls: Sequence[Any],
    final_validation_outcome: str,
    error_code: str | None,
) -> dict[str, Any]:
    """Return bounded protected response envelope for all attempts."""
    attempts: list[dict[str, Any]] = []
    for call in model_calls:
        attempts.append(
            {
                "attempt": call.attempt,
                "raw_response": call.raw_response,
                "response_sha256": call.response_sha256,
                "failure_kind": call.failure_kind,
                "failure_detail": call.failure_detail,
                "latency_ms": call.latency_ms,
            }
        )
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "validation_outcome": final_validation_outcome,
        "error_code": error_code,
        "attempts": attempts,
    }


def resolve_text_model_runtime_metadata(config: RuntimeConfig) -> dict[str, Any]:
    """Return non-secret text-model metadata without loading credentials."""
    document = config.document
    configured = text_model_is_configured(document)
    temperature = _optional_temperature(document, default=0.0)
    if not configured:
        return {
            "endpoint_profile": "mock",
            "endpoint_host": "unconfigured",
            "model_requested": "unconfigured",
            "temperature": temperature,
            "input_limit": _optional_positive_int(document, "TEXT_MODEL_CONTEXT_TOKENS", default=8192),
            "output_limit": _optional_positive_int(
                document,
                "TEXT_MODEL_MAX_OUTPUT_TOKENS",
                default=1024,
            ),
            "timeout_seconds": float(
                _optional_positive_int(document, "TEXT_MODEL_TIMEOUT_SECONDS", default=30)
            ),
            "schema_id": RESPONSE_SCHEMA_ID,
            "prompt_version": PROMPT_VERSION,
        }
    profile = document["TEXT_MODEL_ENDPOINT_PROFILE"]
    return {
        "endpoint_profile": profile,
        "endpoint_host": _resolve_endpoint_host(config),
        "model_requested": document["TEXT_MODEL_NAME"],
        "temperature": temperature,
        "input_limit": _optional_positive_int(document, "TEXT_MODEL_CONTEXT_TOKENS", default=8192),
        "output_limit": _optional_positive_int(
            document,
            "TEXT_MODEL_MAX_OUTPUT_TOKENS",
            default=1024,
        ),
        "timeout_seconds": float(
            _optional_positive_int(document, "TEXT_MODEL_TIMEOUT_SECONDS", default=30)
        ),
        "schema_id": RESPONSE_SCHEMA_ID,
        "prompt_version": PROMPT_VERSION,
    }


def resolve_text_model_endpoint_profile(config: RuntimeConfig) -> EndpointProfile:
    """Return the configured endpoint profile for routing evaluation."""
    document = config.document
    if not text_model_is_configured(document):
        return EndpointProfile.MOCK
    return EndpointProfile(document["TEXT_MODEL_ENDPOINT_PROFILE"])


def build_model_call_request(
    *,
    snapshot: IntakeRevisionSnapshot,
    config: RuntimeConfig,
) -> ModelCallRequest:
    """Construct authoritative model-call routing inputs from revision data."""
    document = config.document
    return ModelCallRequest(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        data_origin=DataOrigin(snapshot.data_origin),
        sensitivity=Sensitivity(snapshot.sensitivity),
        endpoint_profile=resolve_text_model_endpoint_profile(config),
        endpoint_policy_approved=document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is True,
        cui_boundary_approved=document.get("CUI_MODEL_BOUNDARY_APPROVED") is True,
        vision_model_enabled=config.vision_model_enabled,
        current_llm_call_count=0,
        max_llm_calls=2,
    )


async def run_intake_normalization(
    *,
    deps: NormalizationDependencies,
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: IntakeRevisionSnapshot,
    claimed: ClaimedIntakeOperation,
    deterministic_draft: AggregatedIntakeDraft,
    artifact_outcomes: Sequence[tuple[ArtifactSnapshot, ExtractionOutcome]],
    lease_owner: str,
    now_factory: UtcNowFactory,
) -> PendingNormalizationOutcome:
    """Optionally run bounded normalization outside DB transactions."""
    try:
        return await _run_intake_normalization_inner(
            deps=deps,
            session_factory=session_factory,
            snapshot=snapshot,
            claimed=claimed,
            deterministic_draft=deterministic_draft,
            artifact_outcomes=artifact_outcomes,
            lease_owner=lease_owner,
            now_factory=now_factory,
        )
    except (NormalizationInvariantError, NormalizationLeaseLostError):
        await mark_normalization_and_intake_reconciliation_required(
            session_factory,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
            normalization_step_id=None,
        )
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=True,
            step_id=None,
            input_digest=None,
            result=None,
            deterministic_draft=deterministic_draft,
            protected_artifacts=None,
            runtime_metadata=None,
        )


async def _run_intake_normalization_inner(
    *,
    deps: NormalizationDependencies,
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: IntakeRevisionSnapshot,
    claimed: ClaimedIntakeOperation,
    deterministic_draft: AggregatedIntakeDraft,
    artifact_outcomes: Sequence[tuple[ArtifactSnapshot, ExtractionOutcome]],
    lease_owner: str,
    now_factory: UtcNowFactory,
) -> PendingNormalizationOutcome:
    artifacts = build_artifact_facts(
        artifacts=snapshot.artifacts,
        artifact_outcomes=artifact_outcomes,
    )
    document = copy.deepcopy(deterministic_draft.document)
    provenance = copy.deepcopy(deterministic_draft.field_provenance)
    if not normalization_needed(
        profile_id=snapshot.profile_id,
        document=document,
        field_provenance=provenance,
        artifacts=artifacts,
    ):
        return PendingNormalizationOutcome(
            skipped=True,
            reconciliation_required=False,
            step_id=None,
            input_digest=None,
            result=None,
            deterministic_draft=deterministic_draft,
            protected_artifacts=None,
            runtime_metadata=None,
        )

    context_tokens = _positive_int(deps.config.document, "TEXT_MODEL_CONTEXT_TOKENS", default=8192)
    max_output_tokens = _positive_int(
        deps.config.document,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS",
        default=1024,
    )
    empty_targets = list_empty_targets(
        profile_id=snapshot.profile_id,
        document=document,
        field_provenance=provenance,
    )
    runtime_metadata = resolve_text_model_runtime_metadata(deps.config)
    try:
        bundle = build_fact_bundle(
            profile_id=snapshot.profile_id,
            empty_targets=empty_targets,
            artifacts=artifacts,
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
        )
    except ContextLimitExceededError as exc:
        draft_digest = compute_draft_digest(document=document, field_provenance=provenance)
        input_digest = compute_input_digest(
            package_revision_id=snapshot.package_revision_id,
            revision_version=snapshot.revision_version,
            profile_id=snapshot.profile_id,
            content_manifest_sha256=snapshot.content_manifest_sha256,
            artifacts=snapshot.artifacts,
            draft_digest=draft_digest,
            fact_bundle_digest=None,
        )
        step_id = await _reserve_normalization_step(
            session_factory=session_factory,
            snapshot=snapshot,
            claimed=claimed,
            lease_owner=lease_owner,
            now=now_factory(),
            input_digest=input_digest,
            runtime_metadata=runtime_metadata,
        )
        if step_id.reconciliation_required:
            return PendingNormalizationOutcome(
                skipped=False,
                reconciliation_required=True,
                step_id=step_id.step_id,
                input_digest=input_digest,
                result=None,
                deterministic_draft=deterministic_draft,
                protected_artifacts=None,
                runtime_metadata=runtime_metadata,
            )
        reserved_step_id = step_id.step_id
        assert reserved_step_id is not None
        failed_result = _failed_result(
            document=document,
            field_provenance=provenance,
            validation_outcome="rejected_context_limit",
            error_code=exc.error_code,
        )
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=False,
            step_id=reserved_step_id,
            input_digest=input_digest,
            result=failed_result,
            deterministic_draft=deterministic_draft,
            protected_artifacts=None,
            runtime_metadata=runtime_metadata,
        )

    draft_digest = compute_draft_digest(document=document, field_provenance=provenance)
    input_digest = compute_input_digest(
        package_revision_id=snapshot.package_revision_id,
        revision_version=snapshot.revision_version,
        profile_id=snapshot.profile_id,
        content_manifest_sha256=snapshot.content_manifest_sha256,
        artifacts=snapshot.artifacts,
        draft_digest=draft_digest,
        fact_bundle_digest=bundle.fact_bundle_sha256,
    )
    reservation = await _reserve_normalization_step(
        session_factory=session_factory,
        snapshot=snapshot,
        claimed=claimed,
        lease_owner=lease_owner,
        now=now_factory(),
        input_digest=input_digest,
        runtime_metadata=runtime_metadata,
    )
    if reservation.reconciliation_required:
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=True,
            step_id=reservation.step_id,
            input_digest=input_digest,
            result=None,
            deterministic_draft=deterministic_draft,
            protected_artifacts=None,
            runtime_metadata=runtime_metadata,
        )

    step_id = reservation.step_id
    assert step_id is not None
    fact_envelope = build_fact_bundle_envelope(
        package_revision_id=snapshot.package_revision_id,
        bundle_payload=bundle.prompt_payload,
    )
    validate_fact_bundle_envelope(fact_envelope)
    prompt_payload = build_prompt_artifact_payload(bundle=bundle)
    prompt_bytes = _bounded_json_bytes(prompt_payload, max_bytes=MAX_PROMPT_ARTIFACT_BYTES)
    fact_bundle_bytes = _bounded_json_bytes(
        fact_envelope,
        max_bytes=MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES,
    )

    try:
        prompt_artifact = await _write_protected_artifact(
            storage_root=deps.storage_root,
            package_revision_id=str(snapshot.package_revision_id).lower(),
            step_id=str(step_id).lower(),
            artifact_kind="prompt",
            payload=prompt_bytes,
            max_bytes=MAX_PROMPT_ARTIFACT_BYTES,
        )
        fact_bundle_artifact = await _write_protected_artifact(
            storage_root=deps.storage_root,
            package_revision_id=str(snapshot.package_revision_id).lower(),
            step_id=str(step_id).lower(),
            artifact_kind="fact_bundle",
            payload=fact_bundle_bytes,
            max_bytes=MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES,
        )
    except NormalizationArtifactError:
        failed_result = _failed_result(
            document=document,
            field_provenance=provenance,
            validation_outcome="model_call_failed",
            error_code="artifact_write_failed",
        )
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=False,
            step_id=step_id,
            input_digest=input_digest,
            result=failed_result,
            deterministic_draft=deterministic_draft,
            protected_artifacts=None,
            runtime_metadata=runtime_metadata,
        )

    model_call_started = False

    async def _before_call(attempt: int) -> None:
        nonlocal model_call_started
        await _transition_step_for_call(
            session_factory=session_factory,
            snapshot=snapshot,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
            step_id=step_id,
            input_digest=input_digest,
            attempt=attempt,
            runtime_metadata=runtime_metadata,
            prompt_artifact=prompt_artifact,
            fact_bundle_artifact=fact_bundle_artifact,
        )
        model_call_started = True

    def _client_factory() -> TextModelClient:
        return deps.text_client_factory(deps.config)

    model_request = build_model_call_request(snapshot=snapshot, config=deps.config)
    try:
        result = await run_normalize_proposal(
            profile_id=snapshot.profile_id,
            document=document,
            field_provenance=provenance,
            artifacts=artifacts,
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
            model_request=model_request,
            step_id=step_id,
            client_factory=_client_factory,
            before_call=_before_call,
        )
    except (NormalizationInvariantError, NormalizationLeaseLostError):
        await mark_normalization_and_intake_reconciliation_required(
            session_factory,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
            normalization_step_id=step_id,
        )
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=True,
            step_id=step_id,
            input_digest=input_digest,
            result=None,
            deterministic_draft=deterministic_draft,
            protected_artifacts=StoredNormalizationArtifacts(
                prompt=prompt_artifact,
                fact_bundle=fact_bundle_artifact,
                response=None,
            ),
            runtime_metadata=runtime_metadata,
        )

    response_artifact: StoredNormalizationArtifact | None = None
    if result.model_calls:
        response_envelope = build_response_envelope(
            model_calls=result.model_calls,
            final_validation_outcome=result.validation_outcome,
            error_code=result.error_code,
        )
        response_bytes = _bounded_json_bytes(
            response_envelope,
            max_bytes=MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES,
        )
        try:
            response_artifact = await _write_protected_artifact(
                storage_root=deps.storage_root,
                package_revision_id=str(snapshot.package_revision_id).lower(),
                step_id=str(step_id).lower(),
                artifact_kind="response",
                payload=response_bytes,
                max_bytes=MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES,
            )
        except NormalizationArtifactError:
            await mark_normalization_and_intake_reconciliation_required(
                session_factory,
                claimed=claimed,
                lease_owner=lease_owner,
                now_factory=now_factory,
                normalization_step_id=step_id,
            )
            return PendingNormalizationOutcome(
                skipped=False,
                reconciliation_required=True,
                step_id=step_id,
                input_digest=input_digest,
                result=None,
                deterministic_draft=deterministic_draft,
                protected_artifacts=StoredNormalizationArtifacts(
                    prompt=prompt_artifact,
                    fact_bundle=fact_bundle_artifact,
                    response=None,
                ),
                runtime_metadata=runtime_metadata,
            )
        result = _with_response_envelope_digest(result, response_bytes)
    elif _missing_response_requires_reconciliation(
        result=result,
        model_call_started=model_call_started,
    ):
        await mark_normalization_and_intake_reconciliation_required(
            session_factory,
            claimed=claimed,
            lease_owner=lease_owner,
            now_factory=now_factory,
            normalization_step_id=step_id,
        )
        return PendingNormalizationOutcome(
            skipped=False,
            reconciliation_required=True,
            step_id=step_id,
            input_digest=input_digest,
            result=None,
            deterministic_draft=deterministic_draft,
            protected_artifacts=StoredNormalizationArtifacts(
                prompt=prompt_artifact,
                fact_bundle=fact_bundle_artifact,
                response=None,
            ),
            runtime_metadata=runtime_metadata,
        )

    return PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=step_id,
        input_digest=input_digest,
        result=result,
        deterministic_draft=_draft_from_result(deterministic_draft, result),
        protected_artifacts=StoredNormalizationArtifacts(
            prompt=prompt_artifact,
            fact_bundle=fact_bundle_artifact,
            response=response_artifact,
        ),
        runtime_metadata=runtime_metadata,
    )


def _missing_response_requires_reconciliation(
    *,
    result: NormalizeProposalResult,
    model_call_started: bool,
) -> bool:
    if not model_call_started or result.model_calls:
        return False
    return result.validation_outcome not in {
        "model_not_configured",
        "model_call_failed",
    }


def verify_normalization_step_for_commit(
    *,
    step: PackageNormalizationStep,
    pending: PendingNormalizationOutcome,
    package_revision_id: uuid.UUID,
) -> None:
    """Fail closed when the locked step does not match the pending outcome."""
    if pending.step_id is None:
        raise NormalizationInvariantError(
            message="normalization commit requires a pending step identifier"
        )
    if step.step_id != pending.step_id:
        raise NormalizationInvariantError(
            message="normalization commit found a mismatched step identifier"
        )
    if step.package_revision_id != package_revision_id:
        raise NormalizationInvariantError(
            message="normalization commit found a mismatched package revision"
        )
    if step.step_key != NORMALIZATION_STEP_KEY:
        raise NormalizationInvariantError(
            message="normalization commit found an unexpected step key"
        )
    if pending.input_digest is not None and step.input_digest != pending.input_digest:
        raise NormalizationInvariantError(
            message="normalization commit found a mismatched input digest"
        )
    if pending.reconciliation_required:
        return
    if step.status not in PRE_TERMINAL_STEP_STATUSES:
        raise NormalizationInvariantError(
            message="normalization commit requires a reserved or running step"
        )


def terminalize_normalization_step(
    session: AsyncSession,
    *,
    step: PackageNormalizationStep,
    pending: PendingNormalizationOutcome,
    now: datetime,
) -> None:
    """Apply terminal normalization metadata during the final intake commit."""
    if pending.skipped or pending.step_id is None:
        return
    if pending.reconciliation_required:
        _apply_step_reconciliation_fields(step, now=now)
        return

    assert pending.result is not None
    result = pending.result
    status = resolve_terminal_step_status(result)
    step.status = status
    step.validation_outcome = result.validation_outcome
    step.llm_call_count = result.llm_call_count
    step.repair_attempted = result.llm_call_count == 2
    step.completed_at = now
    step.error_code = _terminal_error_code(result)

    if pending.runtime_metadata is not None:
        step.schema_id = pending.runtime_metadata["schema_id"]
        step.prompt_version = pending.runtime_metadata["prompt_version"]
        step.endpoint_profile = pending.runtime_metadata["endpoint_profile"]
        step.endpoint_host = pending.runtime_metadata["endpoint_host"]
        step.model_requested = pending.runtime_metadata["model_requested"]
        step.temperature = pending.runtime_metadata["temperature"]
        step.input_limit = pending.runtime_metadata["input_limit"]
        step.output_limit = pending.runtime_metadata["output_limit"]
        step.timeout_seconds = pending.runtime_metadata["timeout_seconds"]

    if pending.protected_artifacts is not None:
        if pending.protected_artifacts.prompt is not None:
            step.prompt_storage_key = pending.protected_artifacts.prompt.storage_key
            step.prompt_sha256 = pending.protected_artifacts.prompt.sha256
        if pending.protected_artifacts.fact_bundle is not None:
            step.fact_bundle_storage_key = pending.protected_artifacts.fact_bundle.storage_key
            step.fact_bundle_sha256 = pending.protected_artifacts.fact_bundle.sha256

    if status == "policy_blocked":
        step.error_retryable = False
        return
    if status == "failed":
        step.error_retryable = _terminal_error_retryable(result)
    else:
        step.error_retryable = None

    if status == "completed":
        step.response_sha256 = result.response_sha256
        if pending.protected_artifacts is not None and pending.protected_artifacts.response is not None:
            step.response_storage_key = pending.protected_artifacts.response.storage_key
        step.latency_ms = sum(call.latency_ms or 0 for call in result.model_calls)
        if step.started_at is None and result.llm_call_count > 0:
            step.started_at = now


def resolve_terminal_step_status(result: NormalizeProposalResult) -> str:
    """Map pure normalize outcome to terminal persistence status."""
    outcome = result.validation_outcome
    if outcome == "rejected_routing":
        return "policy_blocked"
    if outcome in {"rejected_context_limit", "model_not_configured", "model_call_failed"}:
        return "failed"
    return "completed"


def normalization_audit_metadata(pending: PendingNormalizationOutcome) -> dict[str, Any] | None:
    """Return bounded normalization metadata for intake audit events."""
    if pending.skipped or pending.step_id is None or pending.result is None:
        return None
    result = pending.result
    metadata: dict[str, Any] = {
        "normalization_step_id": str(pending.step_id).lower(),
        "normalization_status": resolve_terminal_step_status(result),
        "validation_outcome": result.validation_outcome,
        "llm_call_count": result.llm_call_count,
    }
    if pending.protected_artifacts is not None:
        if pending.protected_artifacts.prompt is not None:
            metadata["prompt_sha256"] = pending.protected_artifacts.prompt.sha256
        if pending.protected_artifacts.fact_bundle is not None:
            metadata["fact_bundle_sha256"] = pending.protected_artifacts.fact_bundle.sha256
        if pending.protected_artifacts.response is not None:
            metadata["response_sha256"] = pending.protected_artifacts.response.sha256
    return metadata


async def mark_normalization_and_intake_reconciliation_required(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now_factory: UtcNowFactory,
    normalization_step_id: uuid.UUID | None,
) -> bool:
    """Mark intake and an optional normalization step reconciliation_required."""
    async with session_scope(session_factory) as session:
        marked = await mark_intake_work_reconciliation_required(
            session,
            package_revision_id=claimed.package_revision_id,
            work_phase=claimed.work_phase,
            lease_owner=lease_owner,
            fence_token=claimed.fence_token,
            now=now_factory(),
        )
        if not marked or normalization_step_id is None:
            return marked
        step = (
            await session.execute(
                select(PackageNormalizationStep)
                .where(PackageNormalizationStep.step_id == normalization_step_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if step is not None:
            _apply_step_reconciliation_fields(step, now=now_factory())
        return marked


@dataclass(frozen=True, slots=True)
class _ReservationOutcome:
    step_id: uuid.UUID | None
    reconciliation_required: bool


async def _reserve_normalization_step(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: IntakeRevisionSnapshot,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now: datetime,
    input_digest: str,
    runtime_metadata: dict[str, Any],
) -> _ReservationOutcome:
    async with session_scope(session_factory) as session:
        _work, step = await _load_owned_normalization_context(
            session,
            snapshot=snapshot,
            claimed=claimed,
            lease_owner=lease_owner,
            now=now,
        )
        if step is None:
            step_id = uuid.uuid4()
            session.add(
                PackageNormalizationStep(
                    step_id=step_id,
                    package_revision_id=snapshot.package_revision_id,
                    step_key=NORMALIZATION_STEP_KEY,
                    status="reserved",
                    input_digest=input_digest,
                    schema_id=runtime_metadata["schema_id"],
                    prompt_version=runtime_metadata["prompt_version"],
                    endpoint_profile=runtime_metadata["endpoint_profile"],
                    endpoint_host=runtime_metadata["endpoint_host"],
                    model_requested=runtime_metadata["model_requested"],
                    temperature=runtime_metadata["temperature"],
                    input_limit=runtime_metadata["input_limit"],
                    output_limit=runtime_metadata["output_limit"],
                    timeout_seconds=runtime_metadata["timeout_seconds"],
                    created_at=now,
                )
            )
            return _ReservationOutcome(step_id=step_id, reconciliation_required=False)

        if step.status == "reserved" and step.input_digest == input_digest:
            return _ReservationOutcome(step_id=step.step_id, reconciliation_required=False)
        if step.status in REPLAY_RECONCILIATION_STEP_STATUSES:
            await _mark_reconciliation_in_session(
                session,
                step=step,
                claimed=claimed,
                lease_owner=lease_owner,
                now=now,
            )
            return _ReservationOutcome(step_id=step.step_id, reconciliation_required=True)
        if step.input_digest != input_digest:
            raise NormalizationInvariantError(
                message="existing normalization reservation digest does not match"
            )
        return _ReservationOutcome(step_id=step.step_id, reconciliation_required=False)


async def _transition_step_for_call(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: IntakeRevisionSnapshot,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now_factory: UtcNowFactory,
    step_id: uuid.UUID,
    input_digest: str,
    attempt: int,
    runtime_metadata: dict[str, Any],
    prompt_artifact: StoredNormalizationArtifact,
    fact_bundle_artifact: StoredNormalizationArtifact,
) -> None:
    now = now_factory()
    async with session_scope(session_factory) as session:
        _work, step = await _load_owned_normalization_context(
            session,
            snapshot=snapshot,
            claimed=claimed,
            lease_owner=lease_owner,
            now=now,
            step_id=step_id,
            lock_step=True,
        )
        if step is None:
            raise NormalizationLeaseLostError()
        if step.input_digest != input_digest:
            raise NormalizationInvariantError(
                message="normalization input digest changed during call reservation"
            )
        if attempt == 1:
            if step.status != "reserved":
                raise NormalizationInvariantError(
                    message="first call requires a reserved normalization step"
                )
            step.status = "running"
            step.started_at = now
            step.llm_call_count = 1
            step.prompt_storage_key = prompt_artifact.storage_key
            step.fact_bundle_storage_key = fact_bundle_artifact.storage_key
            step.prompt_sha256 = prompt_artifact.sha256
            step.fact_bundle_sha256 = fact_bundle_artifact.sha256
            step.schema_id = runtime_metadata["schema_id"]
            step.prompt_version = runtime_metadata["prompt_version"]
            step.endpoint_profile = runtime_metadata["endpoint_profile"]
            step.endpoint_host = runtime_metadata["endpoint_host"]
            step.model_requested = runtime_metadata["model_requested"]
            step.temperature = runtime_metadata["temperature"]
            step.input_limit = runtime_metadata["input_limit"]
            step.output_limit = runtime_metadata["output_limit"]
            step.timeout_seconds = runtime_metadata["timeout_seconds"]
            return
        if attempt == 2:
            if step.status != "running" or step.llm_call_count != 1:
                raise NormalizationInvariantError(
                    message="repair call requires a running normalization step with one prior call"
                )
            step.llm_call_count = 2
            step.repair_attempted = True
            return
        raise NormalizationInvariantError(message="normalize_proposal supports at most two calls")


async def _mark_reconciliation_in_session(
    session: AsyncSession,
    *,
    step: PackageNormalizationStep,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now: datetime,
) -> None:
    marked = await mark_intake_work_reconciliation_required(
        session,
        package_revision_id=claimed.package_revision_id,
        work_phase=claimed.work_phase,
        lease_owner=lease_owner,
        fence_token=claimed.fence_token,
        now=now,
    )
    if marked:
        _apply_step_reconciliation_fields(step, now=now)


def _apply_step_reconciliation_fields(
    step: PackageNormalizationStep,
    *,
    now: datetime,
) -> None:
    step.status = "reconciliation_required"
    step.completed_at = now
    step.error_code = "ambiguous_running_step"
    step.error_retryable = False
    step.validation_outcome = "reconciliation_required"


async def _load_owned_normalization_context(
    session: AsyncSession,
    *,
    snapshot: IntakeRevisionSnapshot,
    claimed: ClaimedIntakeOperation,
    lease_owner: str,
    now: datetime,
    step_id: uuid.UUID | None = None,
    lock_step: bool = False,
) -> tuple[PackageRevisionIntakeWork, PackageNormalizationStep | None]:
    work = (
        await session.execute(
            select(PackageRevisionIntakeWork)
            .where(
                PackageRevisionIntakeWork.package_revision_id
                == claimed.package_revision_id,
                PackageRevisionIntakeWork.work_phase == claimed.work_phase,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if work is None:
        raise NormalizationLeaseLostError()
    revision_version = snapshot.revision_version
    from ato_service.db.models import PackageRevision

    revision = (
        await session.execute(
            select(PackageRevision)
            .where(PackageRevision.package_revision_id == claimed.package_revision_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revision is None:
        raise NormalizationInvariantError(message="normalization requires owning revision")
    try:
        assert_intake_claim_live(
            work,
            revision,
            lease_owner=lease_owner,
            fence_token=claimed.fence_token,
            now=now,
        )
    except IntakeLeaseLostError as exc:
        raise NormalizationLeaseLostError() from exc
    if revision.revision_version != revision_version:
        raise NormalizationLeaseLostError()

    query = select(PackageNormalizationStep).where(
        PackageNormalizationStep.package_revision_id == claimed.package_revision_id,
        PackageNormalizationStep.step_key == NORMALIZATION_STEP_KEY,
    )
    if step_id is not None:
        query = query.where(PackageNormalizationStep.step_id == step_id)
    if lock_step:
        query = query.with_for_update()
    step = (await session.execute(query)).scalar_one_or_none()
    return work, step


def _require_complete_artifact_outcomes(
    *,
    artifacts: Sequence[ArtifactSnapshot],
    artifact_outcomes: Sequence[tuple[ArtifactSnapshot, ExtractionOutcome]],
) -> None:
    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    outcome_artifact_ids = [artifact.artifact_id for artifact, _ in artifact_outcomes]
    if len(set(artifact_ids)) != len(artifact_ids):
        raise NormalizationInvariantError(
            message="revision artifact list contains duplicate artifact IDs"
        )
    if len(set(outcome_artifact_ids)) != len(outcome_artifact_ids):
        raise NormalizationInvariantError(
            message="artifact extraction outcomes contain duplicate artifact IDs"
        )
    if set(outcome_artifact_ids) != set(artifact_ids):
        raise NormalizationInvariantError(
            message="artifact extraction outcomes do not match revision artifacts"
        )


def _segment_fact(segment: ExtractedSegment) -> SegmentFact:
    return SegmentFact(
        segment_index=segment.segment_index,
        text=segment.text,
        locator=copy.deepcopy(segment.locator),
        extraction_method=segment.extraction_method,
    )


async def _write_protected_artifact(
    *,
    storage_root: Path,
    package_revision_id: str,
    step_id: str,
    artifact_kind: str,
    payload: bytes,
    max_bytes: int,
) -> StoredNormalizationArtifact:
    return await asyncio.to_thread(
        write_normalization_protected_artifact,
        storage_root=storage_root,
        package_revision_id=package_revision_id,
        step_id=step_id,
        artifact_kind=artifact_kind,
        payload=payload,
        max_bytes=max_bytes,
    )


def _bounded_json_bytes(payload: dict[str, Any], *, max_bytes: int) -> bytes:
    encoded = stable_json_dumps(payload).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("normalization protected artifact exceeds configured maximum size")
    return encoded


def _positive_int(document: dict[str, Any], key: str, *, default: int) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"{key} must be a positive integer")
    return raw


def _optional_positive_int(document: dict[str, Any], key: str, *, default: int) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return default
    return raw


def _optional_temperature(document: dict[str, Any], *, default: float) -> float:
    raw = document.get("TEXT_MODEL_TEMPERATURE", default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = float(raw)
    if value < 0.0 or value > 2.0:
        return default
    return value


def _resolve_endpoint_host(config: RuntimeConfig) -> str:
    document = config.document
    if config.text_model_provider == "aws_bedrock":
        region = document.get("AWS_REGION")
        if isinstance(region, str) and region.strip():
            return region.strip()
        return "unconfigured"
    endpoint_url = document.get("TEXT_MODEL_ENDPOINT_URL")
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        return "unconfigured"
    parsed = urlsplit(endpoint_url.strip())
    return parsed.hostname or "unconfigured"


def _failed_result(
    *,
    document: dict[str, Any],
    field_provenance: dict[str, Any],
    validation_outcome: str,
    error_code: str,
) -> NormalizeProposalResult:
    contract = prompt_contract_metadata()
    return NormalizeProposalResult(
        document=document,
        field_provenance=field_provenance,
        validation_outcome=validation_outcome,  # type: ignore[arg-type]
        llm_call_count=0,
        merged_targets=(),
        rejected_proposals=(),
        omitted_segment_ids=(),
        context_complete=True,
        fact_bundle_sha256=None,
        prompt_version=contract["prompt_version"],
        prompt_sha256=contract["prompt_sha256"],
        response_sha256=None,
        model_calls=(),
        error_code=error_code,
    )


def _draft_from_result(
    base: AggregatedIntakeDraft,
    result: NormalizeProposalResult,
) -> AggregatedIntakeDraft:
    return AggregatedIntakeDraft(
        document=result.document,
        field_provenance=result.field_provenance,
        system_context_proposal=base.system_context_proposal,
        segment_count=base.segment_count,
    )


def _with_response_envelope_digest(
    result: NormalizeProposalResult,
    response_bytes: bytes,
) -> NormalizeProposalResult:
    from dataclasses import replace

    return replace(result, response_sha256=hashlib.sha256(response_bytes).hexdigest())


def _terminal_error_code(result: NormalizeProposalResult) -> str | None:
    if result.validation_outcome == "rejected_routing":
        return result.error_code or "policy_denied"
    if result.validation_outcome in {
        "rejected_context_limit",
        "model_not_configured",
        "model_call_failed",
    }:
        return result.error_code
    return None


def _terminal_error_retryable(result: NormalizeProposalResult) -> bool:
    if result.validation_outcome == "model_call_failed":
        return True
    return False


@cache
def _fact_bundle_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "contracts"
        / "normalize-proposal-fact-bundle.schema.json"
    )
    import json

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    return validator


__all__ = [
    "FACT_BUNDLE_SCHEMA_ID",
    "MAX_NORMALIZATION_PROTECTED_ARTIFACT_BYTES",
    "NORMALIZATION_STEP_KEY",
    "NormalizationDependencies",
    "NormalizationInvariantError",
    "NormalizationLeaseLostError",
    "PendingNormalizationOutcome",
    "StoredNormalizationArtifacts",
    "UtcNowFactory",
    "build_artifact_facts",
    "build_fact_bundle_envelope",
    "build_model_call_request",
    "build_prompt_artifact_payload",
    "build_response_envelope",
    "compute_draft_digest",
    "compute_input_digest",
    "default_text_client_factory",
    "mark_normalization_and_intake_reconciliation_required",
    "normalization_audit_metadata",
    "normalization_needed",
    "resolve_terminal_step_status",
    "resolve_text_model_endpoint_profile",
    "resolve_text_model_runtime_metadata",
    "run_intake_normalization",
    "terminalize_normalization_step",
    "validate_fact_bundle_envelope",
    "verify_normalization_step_for_commit",
]
