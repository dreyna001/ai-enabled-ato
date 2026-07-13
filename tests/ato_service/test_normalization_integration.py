"""Diff 4 integration tests for intake normalization orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.draft_builder import AggregatedIntakeDraft
from ato_service.extraction.types import ExtractionOutcome, ExtractedSegment
from ato_service.intake import (
    ArtifactSnapshot,
    ClaimedIntakeOperation,
    IntakeRevisionSnapshot,
)
from ato_service.intake_work import IntakeWorkPhase
from ato_service.model_gateway import ModelCallRequest, ModelCapability
from ato_service.model_routing import DataOrigin, EndpointProfile, Sensitivity
from ato_service.normalization_service import (
    NormalizationInvariantError,
    PendingNormalizationOutcome,
    build_artifact_facts,
    build_model_call_request,
    build_response_envelope,
    normalization_audit_metadata,
    normalization_needed,
    resolve_text_model_runtime_metadata,
    terminalize_normalization_step,
    verify_normalization_step_for_commit,
)
from ato_service.normalize_proposal.runner import run_normalize_proposal
from ato_service.normalize_proposal.types import ArtifactFacts, SegmentFact
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.text_llm import (
    ChatMessage,
    OpenAICompatibleTextClient,
    TextModelClient,
    TextModelConfigurationError,
    resolve_text_model_settings,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
HMAC_KEY = b"x" * MIN_AUDIT_HMAC_KEY_BYTES
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SYSTEM_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ARTIFACT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
FENCE_TOKEN = uuid.UUID("55555555-5555-4555-8555-555555555555")
SHA256 = "a" * 64
ROOT = Path(__file__).resolve().parents[2]
VALID_RESPONSE = (
    ROOT / "tests" / "ato_service" / "normalize_proposal" / "fixtures" / "valid_proposal_response.json"
).read_text(encoding="utf-8")


def _run(awaitable: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(awaitable)


def _config(tmp_path: Path, **overrides: Any):
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data",
        "TEXT_MODEL_PROVIDER": "openai_compatible",
        "TEXT_MODEL_ENDPOINT_URL": "https://api.openai.com/v1",
        "TEXT_MODEL_NAME": "gpt-4o-mini",
        "TEXT_MODEL_CONTEXT_TOKENS": 8192,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS": 1024,
        "TEXT_MODEL_TIMEOUT_SECONDS": 30,
        "TEXT_MODEL_MAX_RETRIES": 0,
        "TEXT_MODEL_ENDPOINT_PROFILE": "external_openai",
        "TEXT_MODEL_TEMPERATURE": 0.0,
        "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
        "CUI_MODEL_BOUNDARY_APPROVED": False,
        **overrides,
    }
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _text_locator() -> dict[str, Any]:
    return {
        "kind": "text_offsets",
        "start_byte": 0,
        "end_byte": 120,
        "normalized_start": 0,
        "normalized_end": 120,
    }


def _empty_fisma_document() -> dict[str, Any]:
    return {
        "package": {
            "profile_id": "fisma_agency_security",
            "title": "",
            "prepared_for": "",
            "reporting_period": None,
        },
        "system": {
            "display_name": "Demo System",
            "authorization_boundary": "",
            "mission_summary": "",
            "impact_level": None,
            "authorization_path": "agency",
        },
        "contacts": {
            "system_owner": [],
            "isso": [],
            "issm": [],
            "control_owners": [],
            "assessors": [],
            "approvers": [],
        },
        "control_set": {
            "source": {},
            "tailoring": [],
            "organization_defined_parameters": {},
            "inheritance": [],
        },
        "security_controls": {},
        "evidence": {},
        "findings": {},
        "poam_candidates": {},
        "assessor_inputs": {},
        "privacy": {
            "artifacts_present": False,
            "scope_notice": "Privacy review is external to this product.",
        },
        "fedramp_20x": None,
        "fedramp_rev5_transition": None,
        "fisma_agency_security": {"security_plan_sections": {}},
        "extensions": {},
    }


def _artifact_snapshot() -> ArtifactSnapshot:
    return ArtifactSnapshot(
        artifact_id=ARTIFACT_ID,
        package_revision_id=REVISION_ID,
        display_filename="narrative.txt",
        storage_key="ab/" + "b" * 64,
        sha256=SHA256,
        size_bytes=120,
        declared_media_type="text/plain",
        detected_media_type="text/plain",
        artifact_kind="evidence_document",
        malware_scan_status="clean",
        extraction_status="pending",
    )


def _extraction_outcome(*, text: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=len(text),
        vision_status="not_needed",
        segments=(
            ExtractedSegment(
                segment_index=1,
                text=text,
                locator=_text_locator(),
                extraction_method="text",
            ),
        ),
    )


def _revision_snapshot() -> IntakeRevisionSnapshot:
    return IntakeRevisionSnapshot(
        package_revision_id=REVISION_ID,
        revision_version=3,
        status="extracting",
        profile_id="fisma_agency_security",
        impact_level="moderate",
        content_manifest_sha256="c" * 64,
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        system_id=SYSTEM_ID,
        system_display_name="Normalization Test System",
        artifacts=(_artifact_snapshot(),),
    )


def _claimed() -> ClaimedIntakeOperation:
    return ClaimedIntakeOperation(
        package_revision_id=REVISION_ID,
        work_phase=IntakeWorkPhase.DETERMINISTIC_EXTRACT.value,
        lease_owner="intake-worker",
        fence_token=FENCE_TOKEN,
        expected_revision_version=3,
    )


def _deterministic_draft() -> AggregatedIntakeDraft:
    document = _empty_fisma_document()
    document["extensions"]["unmapped_segments"] = [
        {
            "artifact_id": str(ARTIFACT_ID).lower(),
            "segment_index": 1,
            "text": "Mission: Internal web application for agency case management.",
            "source_locator": _text_locator(),
            "extraction_method": "text",
        }
    ]
    return AggregatedIntakeDraft(
        document=document,
        field_provenance={},
        system_context_proposal=None,
        segment_count=1,
    )


@dataclass
class FakeTextClient:
    responses: list[str] = field(default_factory=list)
    calls: list[tuple[str | None, str]] = field(default_factory=list)
    builds: int = 0
    provider: str = "fake"
    error: Exception | None = None

    def complete(
        self,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        *,
        system: str | None = None,
    ) -> str:
        self.builds += 1
        user = messages[-1].content if messages else ""
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise RuntimeError("fake client has no queued responses")
        return self.responses.pop(0)


def _model_request(*, current: int = 0, approved: bool = True) -> ModelCallRequest:
    sensitivity = Sensitivity.CLASSIFIED if not approved else Sensitivity.PUBLIC
    return ModelCallRequest(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.MOCK,
        endpoint_policy_approved=approved,
        cui_boundary_approved=False,
        vision_model_enabled=False,
        current_llm_call_count=current,
        max_llm_calls=2,
    )


def test_no_targets_skips_normalization_row(tmp_path: Path) -> None:
    draft = _deterministic_draft()
    empty_outcome = ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=0,
        vision_status="not_needed",
        segments=(),
    )
    artifacts = build_artifact_facts(
        artifacts=(_artifact_snapshot(),),
        artifact_outcomes=[(_artifact_snapshot(), empty_outcome)],
    )
    assert normalization_needed(
        profile_id="fisma_agency_security",
        document=draft.document,
        field_provenance=draft.field_provenance,
        artifacts=artifacts,
    ) is False


def test_temperature_defaults_and_openai_payload(tmp_path: Path) -> None:
    config = _config(tmp_path, TEXT_MODEL_TEMPERATURE=0.5)
    settings = resolve_text_model_settings(config)
    assert settings.temperature == 0.5

    client = OpenAICompatibleTextClient(
        endpoint_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        api_key="secret",
        max_output_tokens=128,
        timeout_seconds=5,
        max_retries=0,
        temperature=settings.temperature,
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    response.request = MagicMock()

    with patch("ato_service.text_llm.httpx.Client") as client_cls:
        http_client = MagicMock()
        http_client.__enter__.return_value = http_client
        http_client.post.return_value = response
        client_cls.return_value = http_client
        client.complete([ChatMessage(role="user", content="hello")])

    posted = http_client.post.call_args.kwargs["json"]
    assert posted["temperature"] == 0.5


def test_routing_blocked_builds_zero_clients(tmp_path: Path) -> None:
    fake = FakeTextClient(responses=[VALID_RESPONSE])
    builds = 0

    def factory(_config: Any) -> TextModelClient:
        nonlocal builds
        builds += 1
        return fake

    result = _run(
        run_normalize_proposal(
            profile_id="fisma_agency_security",
            document=_empty_fisma_document(),
            field_provenance={},
            artifacts=(
                ArtifactFacts(
                    artifact_id=ARTIFACT_ID,
                    sha256=SHA256,
                    filename="narrative.txt",
                    detected_format="text",
                    segments=(
                        SegmentFact(
                            segment_index=1,
                            text="Mission: Internal web application for agency case management.",
                            locator=_text_locator(),
                            extraction_method="text",
                        ),
                    ),
                ),
            ),
            context_tokens=8192,
            max_output_tokens=1024,
            model_request=_model_request(approved=False),
            step_id=uuid.uuid4(),
            client_factory=lambda: factory(_config(tmp_path)),
        )
    )
    assert result.validation_outcome == "rejected_routing"
    assert result.llm_call_count == 0
    assert builds == 0
    assert fake.builds == 0


def test_customer_production_external_openai_zero_calls(tmp_path: Path) -> None:
    request = ModelCallRequest(
        capability=ModelCapability.NORMALIZE_PROPOSAL,
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        sensitivity=Sensitivity.INTERNAL_UNCLASSIFIED,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=False,
        vision_model_enabled=False,
        current_llm_call_count=0,
        max_llm_calls=2,
    )
    fake = FakeTextClient(responses=[VALID_RESPONSE])
    result = _run(
        run_normalize_proposal(
            profile_id="fisma_agency_security",
            document=_empty_fisma_document(),
            field_provenance={},
            artifacts=(
                ArtifactFacts(
                    artifact_id=ARTIFACT_ID,
                    sha256=SHA256,
                    filename="narrative.txt",
                    detected_format="text",
                    segments=(
                        SegmentFact(
                            segment_index=1,
                            text="Mission: Internal web application for agency case management.",
                            locator=_text_locator(),
                            extraction_method="text",
                        ),
                    ),
                ),
            ),
            context_tokens=8192,
            max_output_tokens=1024,
            model_request=request,
            step_id=uuid.uuid4(),
            client_factory=lambda: fake,
        )
    )
    assert result.llm_call_count == 0
    assert fake.builds == 0


def test_response_envelope_includes_raw_attempts_audit_excludes_raw() -> None:
    from ato_service.normalize_proposal.types import ModelCallMetadata

    calls = (
        ModelCallMetadata(
            attempt=1,
            raw_response='{"bad": true}',
            response_sha256="1" * 64,
            failure_kind="parse",
            failure_detail="invalid json",
            latency_ms=12,
        ),
        ModelCallMetadata(
            attempt=2,
            raw_response='{"schema_version":"1.0.0","proposals":[]}',
            response_sha256="2" * 64,
            latency_ms=18,
        ),
    )
    envelope = build_response_envelope(
        model_calls=calls,
        final_validation_outcome="repair_succeeded",
        error_code=None,
    )
    assert len(envelope["attempts"]) == 2
    assert envelope["attempts"][0]["raw_response"] == '{"bad": true}'
    assert envelope["attempts"][1]["raw_response"] == (
        '{"schema_version":"1.0.0","proposals":[]}'
    )
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=uuid.uuid4(),
        input_digest="d" * 64,
        result=MagicMock(
            validation_outcome="repair_succeeded",
            llm_call_count=2,
            model_calls=calls,
        ),
        deterministic_draft=_deterministic_draft(),
        protected_artifacts=MagicMock(
            prompt=MagicMock(sha256="p" * 64),
            fact_bundle=MagicMock(sha256="f" * 64),
            response=MagicMock(sha256="r" * 64),
        ),
        runtime_metadata=None,
    )
    audit = normalization_audit_metadata(pending)
    assert audit is not None
    audit_json = json.dumps(audit)
    assert "raw_response" not in audit_json


def test_malformed_response_repair_uses_two_call_reservations(tmp_path: Path) -> None:
    fake = FakeTextClient(
        responses=[
            "not-json",
            VALID_RESPONSE,
        ]
    )
    reservations: list[int] = []

    async def before_call(attempt: int) -> None:
        reservations.append(attempt)

    result = _run(
        run_normalize_proposal(
            profile_id="fisma_agency_security",
            document=_empty_fisma_document(),
            field_provenance={},
            artifacts=(
                ArtifactFacts(
                    artifact_id=ARTIFACT_ID,
                    sha256=SHA256,
                    filename="narrative.txt",
                    detected_format="text",
                    segments=(
                        SegmentFact(
                            segment_index=1,
                            text="Mission: Internal web application for agency case management.",
                            locator=_text_locator(),
                            extraction_method="text",
                        ),
                    ),
                ),
            ),
            context_tokens=8192,
            max_output_tokens=1024,
            model_request=_model_request(),
            step_id=uuid.uuid4(),
            client_factory=lambda: fake,
            before_call=before_call,
        )
    )
    assert reservations == [1, 2]
    assert result.llm_call_count == 2
    assert result.validation_outcome == "repair_succeeded"


def test_missing_config_yields_failed_without_hidden_model_fields(tmp_path: Path) -> None:
    def failing_factory(_config: Any) -> TextModelClient:
        raise TextModelConfigurationError("missing credentials")

    async def noop_before_call(_attempt: int) -> None:
        return None

    result = _run(
        run_normalize_proposal(
            profile_id="fisma_agency_security",
            document=_empty_fisma_document(),
            field_provenance={},
            artifacts=(
                ArtifactFacts(
                    artifact_id=ARTIFACT_ID,
                    sha256=SHA256,
                    filename="narrative.txt",
                    detected_format="text",
                    segments=(
                        SegmentFact(
                            segment_index=1,
                            text="Mission: Internal web application for agency case management.",
                            locator=_text_locator(),
                            extraction_method="text",
                        ),
                    ),
                ),
            ),
            context_tokens=8192,
            max_output_tokens=1024,
            model_request=_model_request(),
            step_id=uuid.uuid4(),
            client_factory=lambda: failing_factory(_config(tmp_path)),
            before_call=noop_before_call,
        )
    )
    assert result.validation_outcome == "model_not_configured"
    assert result.llm_call_count == 1
    assert result.model_calls == ()
    from ato_service.normalization_service import _missing_response_requires_reconciliation

    assert (
        _missing_response_requires_reconciliation(
            result=result,
            model_call_started=True,
        )
        is False
    )


def test_build_model_call_request_uses_runtime_policy_booleans(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        TEXT_MODEL_ENDPOINT_POLICY_APPROVED=False,
        CUI_MODEL_BOUNDARY_APPROVED=False,
    )
    request = build_model_call_request(snapshot=_revision_snapshot(), config=config)
    assert request.endpoint_policy_approved is False
    assert request.cui_boundary_approved is False


def test_protected_artifact_writes_do_not_require_db(tmp_path: Path) -> None:
    from ato_service.normalization_service import (
        build_fact_bundle_envelope,
        build_prompt_artifact_payload,
        validate_fact_bundle_envelope,
    )
    from ato_service.normalize_proposal.fact_bundle import build_fact_bundle
    from ato_service.normalization_artifacts import write_normalization_protected_artifact

    bundle = build_fact_bundle(
        profile_id="fisma_agency_security",
        empty_targets=("/system/mission_summary",),
        artifacts=(
            ArtifactFacts(
                artifact_id=ARTIFACT_ID,
                sha256=SHA256,
                filename="narrative.txt",
                detected_format="text",
                segments=(
                    SegmentFact(
                        segment_index=1,
                        text="Mission: Internal web application for agency case management.",
                        locator=_text_locator(),
                        extraction_method="text",
                    ),
                ),
            ),
        ),
        context_tokens=8192,
        max_output_tokens=1024,
    )
    envelope = build_fact_bundle_envelope(
        package_revision_id=REVISION_ID,
        bundle_payload=bundle.prompt_payload,
    )
    validate_fact_bundle_envelope(envelope)
    prompt_payload = build_prompt_artifact_payload(bundle=bundle)
    step_id = uuid.uuid4()
    storage_root = tmp_path / "storage"
    prompt_bytes = json.dumps(prompt_payload, sort_keys=True).encode("utf-8")
    fact_bytes = json.dumps(envelope, sort_keys=True).encode("utf-8")
    write_normalization_protected_artifact(
        storage_root=storage_root,
        package_revision_id=str(REVISION_ID).lower(),
        step_id=str(step_id).lower(),
        artifact_kind="prompt",
        payload=prompt_bytes,
        max_bytes=512 * 1024,
    )
    write_normalization_protected_artifact(
        storage_root=storage_root,
        package_revision_id=str(REVISION_ID).lower(),
        step_id=str(step_id).lower(),
        artifact_kind="fact_bundle",
        payload=fact_bytes,
        max_bytes=2 * 1024 * 1024,
    )
    assert (storage_root / "revisions" / str(REVISION_ID).lower() / "normalization").exists()


def test_terminalize_completed_step_sets_response_envelope_hash(tmp_path: Path) -> None:
    from ato_service.db.models import PackageNormalizationStep
    from ato_service.normalize_proposal.types import ModelCallMetadata, NormalizeProposalResult

    step = PackageNormalizationStep(
        step_id=uuid.uuid4(),
        package_revision_id=REVISION_ID,
        step_key="normalize_proposal",
        status="running",
        input_digest="d" * 64,
        llm_call_count=1,
        repair_attempted=False,
        created_at=NOW,
        started_at=NOW,
    )
    response_sha = "f" * 64
    result = NormalizeProposalResult(
        document=_empty_fisma_document(),
        field_provenance={
            "/system/mission_summary": {
                "source_artifact_id": str(ARTIFACT_ID).lower(),
                "source_sha256": SHA256,
                "source_locator": _text_locator(),
                "extraction_method": "llm_normalize",
                "model_step_id": str(step.step_id).lower(),
            }
        },
        validation_outcome="accepted",
        llm_call_count=1,
        merged_targets=("/system/mission_summary",),
        rejected_proposals=(),
        omitted_segment_ids=(),
        context_complete=True,
        fact_bundle_sha256="e" * 64,
        prompt_version="1.0.0",
        prompt_sha256="c" * 64,
        response_sha256=response_sha,
        model_calls=(
            ModelCallMetadata(
                attempt=1,
                raw_response=VALID_RESPONSE,
                response_sha256=response_sha,
                latency_ms=25,
            ),
        ),
    )
    from ato_service.normalization_artifacts import StoredNormalizationArtifact
    from ato_service.normalization_service import StoredNormalizationArtifacts

    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=step.step_id,
        input_digest="d" * 64,
        result=result,
        deterministic_draft=_deterministic_draft(),
        protected_artifacts=StoredNormalizationArtifacts(
            prompt=StoredNormalizationArtifact(
                storage_key="revisions/x/normalization/y/prompt.json",
                sha256="p" * 64,
                size_bytes=10,
            ),
            fact_bundle=StoredNormalizationArtifact(
                storage_key="revisions/x/normalization/y/fact-bundle.json",
                sha256="e" * 64,
                size_bytes=10,
            ),
            response=StoredNormalizationArtifact(
                storage_key="revisions/x/normalization/y/response.json",
                sha256=response_sha,
                size_bytes=10,
            ),
        ),
        runtime_metadata={
            "schema_id": "https://ato.local/schemas/normalize-proposal-response.schema.json",
            "prompt_version": "1.0.0",
            "prompt_sha256": "p" * 64,
            "endpoint_profile": "mock",
            "endpoint_host": "api.openai.com",
            "model_requested": "gpt-4o-mini",
            "temperature": 0.0,
            "input_limit": 8192,
            "output_limit": 1024,
            "timeout_seconds": 30.0,
        },
    )
    terminalize_normalization_step(MagicMock(), step=step, pending=pending, now=NOW)
    assert step.status == "completed"
    assert step.response_sha256 == response_sha
    assert step.prompt_sha256 == "p" * 64
    assert step.fact_bundle_sha256 == "e" * 64
    assert step.prompt_sha256 != result.prompt_sha256
    assert step.latency_ms == 25
    assert step.validation_outcome == "accepted"


def test_normalization_invariant_error_accepts_message_keyword() -> None:
    error = NormalizationInvariantError(message="digest mismatch")
    assert error.message == "digest mismatch"
    assert str(error) == "digest mismatch"


def test_unconfigured_runtime_metadata_is_non_throwing(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data",
        },
        base_dir=tmp_path,
    )
    metadata = resolve_text_model_runtime_metadata(config)
    assert metadata["endpoint_profile"] == "mock"
    assert metadata["model_requested"] == "unconfigured"
    assert metadata["endpoint_host"] == "unconfigured"


def test_build_artifact_facts_rejects_extra_outcome() -> None:
    snapshot = _artifact_snapshot()
    outcome = ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=10,
        vision_status="not_needed",
        segments=(),
    )
    with pytest.raises(NormalizationInvariantError, match="do not match"):
        build_artifact_facts(
            artifacts=(),
            artifact_outcomes=[(snapshot, outcome)],
        )


def test_build_artifact_facts_rejects_duplicate_outcomes() -> None:
    snapshot = _artifact_snapshot()
    outcome = ExtractionOutcome(
        status="succeeded",
        detected_format="text",
        detected_media_type="text/plain",
        page_count=None,
        total_text_characters=10,
        vision_status="not_needed",
        segments=(),
    )
    with pytest.raises(NormalizationInvariantError, match="duplicate"):
        build_artifact_facts(
            artifacts=(snapshot,),
            artifact_outcomes=[(snapshot, outcome), (snapshot, outcome)],
        )


def test_verify_commit_rejects_mismatched_step_id() -> None:
    from ato_service.db.models import PackageNormalizationStep

    step = PackageNormalizationStep(
        step_id=uuid.uuid4(),
        package_revision_id=REVISION_ID,
        step_key="normalize_proposal",
        status="running",
        input_digest="d" * 64,
        llm_call_count=1,
        repair_attempted=False,
        created_at=NOW,
        started_at=NOW,
    )
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=uuid.uuid4(),
        input_digest="d" * 64,
        result=None,
        deterministic_draft=_deterministic_draft(),
        protected_artifacts=None,
        runtime_metadata=None,
    )
    with pytest.raises(NormalizationInvariantError, match="mismatched step identifier"):
        verify_normalization_step_for_commit(
            step=step,
            pending=pending,
            package_revision_id=REVISION_ID,
        )


def test_terminalize_policy_blocked_sets_error_retryable_false() -> None:
    from ato_service.normalization_artifacts import StoredNormalizationArtifact
    from ato_service.db.models import PackageNormalizationStep
    from ato_service.normalization_service import StoredNormalizationArtifacts
    from ato_service.normalize_proposal.types import NormalizeProposalResult

    step = PackageNormalizationStep(
        step_id=uuid.uuid4(),
        package_revision_id=REVISION_ID,
        step_key="normalize_proposal",
        status="reserved",
        input_digest="d" * 64,
        llm_call_count=0,
        repair_attempted=False,
        created_at=NOW,
    )
    result = NormalizeProposalResult(
        document=_empty_fisma_document(),
        field_provenance={},
        validation_outcome="rejected_routing",
        llm_call_count=0,
        merged_targets=(),
        rejected_proposals=(),
        omitted_segment_ids=(),
        context_complete=True,
        fact_bundle_sha256=None,
        prompt_version="1.0.0",
        prompt_sha256="p" * 64,
        response_sha256=None,
        model_calls=(),
        error_code="policy_denied",
    )
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=step.step_id,
        input_digest="d" * 64,
        result=result,
        deterministic_draft=_deterministic_draft(),
        protected_artifacts=StoredNormalizationArtifacts(
            prompt=StoredNormalizationArtifact(
                storage_key="revisions/x/normalization/y/prompt.json",
                sha256="p" * 64,
                size_bytes=10,
            ),
            fact_bundle=StoredNormalizationArtifact(
                storage_key="revisions/x/normalization/y/fact-bundle.json",
                sha256="f" * 64,
                size_bytes=10,
            ),
            response=None,
        ),
        runtime_metadata={
            "schema_id": "https://ato.local/schemas/normalize-proposal-response.schema.json",
            "prompt_version": "1.0.0",
            "endpoint_profile": "mock",
            "endpoint_host": "unconfigured",
            "model_requested": "unconfigured",
            "temperature": 0.0,
            "input_limit": 8192,
            "output_limit": 1024,
            "timeout_seconds": 30.0,
        },
    )
    terminalize_normalization_step(MagicMock(), step=step, pending=pending, now=NOW)
    assert step.status == "policy_blocked"
    assert step.error_retryable is False
    assert step.error_code == "policy_denied"
    assert step.validation_outcome == "rejected_routing"
    assert step.prompt_sha256 == "p" * 64
    assert step.fact_bundle_sha256 == "f" * 64
    assert step.endpoint_host == "unconfigured"


def test_protected_write_uses_asyncio_to_thread(tmp_path: Path) -> None:
    from ato_service.normalization_artifacts import StoredNormalizationArtifact
    from ato_service.normalization_service import _write_protected_artifact

    expected = StoredNormalizationArtifact(
        storage_key="revisions/x/normalization/y/prompt.json",
        sha256="a" * 64,
        size_bytes=4,
    )

    async def _run_write() -> StoredNormalizationArtifact:
        with patch(
            "ato_service.normalization_service.asyncio.to_thread",
            return_value=expected,
        ) as to_thread:
            artifact = await _write_protected_artifact(
                storage_root=tmp_path,
                package_revision_id=str(REVISION_ID).lower(),
                step_id=str(uuid.uuid4()).lower(),
                artifact_kind="prompt",
                payload=b"{}",
                max_bytes=1024,
            )
            to_thread.assert_awaited_once()
            return artifact

    artifact = _run(_run_write())
    assert artifact.sha256 == expected.sha256


def test_before_call_uses_fresh_now_factory(tmp_path: Path) -> None:
    from ato_service.normalization_artifacts import StoredNormalizationArtifact
    from ato_service.normalization_service import _transition_step_for_call

    observed: list[datetime] = []

    def scripted_clock() -> datetime:
        value = NOW + timedelta(seconds=len(observed))
        observed.append(value)
        return value

    step = MagicMock()
    step.input_digest = "d" * 64
    step.status = "reserved"
    step.llm_call_count = 0

    class SessionContext:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *_args: object) -> None:
            return None

    prompt_artifact = StoredNormalizationArtifact(
        storage_key="revisions/x/normalization/y/prompt.json",
        sha256="p" * 64,
        size_bytes=10,
    )
    fact_bundle_artifact = StoredNormalizationArtifact(
        storage_key="revisions/x/normalization/y/fact-bundle.json",
        sha256="f" * 64,
        size_bytes=10,
    )

    async def _invoke() -> None:
        with patch(
            "ato_service.normalization_service.session_scope",
            return_value=SessionContext(),
        ), patch(
            "ato_service.normalization_service._load_owned_normalization_context",
            return_value=(MagicMock(), step),
        ):
            await _transition_step_for_call(
                session_factory=MagicMock(),
                snapshot=_revision_snapshot(),
                claimed=MagicMock(
                    package_revision_id=REVISION_ID,
                    work_phase="deterministic_extract",
                    fence_token=FENCE_TOKEN,
                ),
                lease_owner="intake-test",
                now_factory=scripted_clock,
                step_id=uuid.uuid4(),
                input_digest="d" * 64,
                attempt=1,
                runtime_metadata=resolve_text_model_runtime_metadata(_config(tmp_path)),
                prompt_artifact=prompt_artifact,
                fact_bundle_artifact=fact_bundle_artifact,
            )

    _run(_invoke())
    assert len(observed) == 1
    assert step.started_at == observed[0]
    assert step.prompt_sha256 == prompt_artifact.sha256
    assert step.fact_bundle_sha256 == fact_bundle_artifact.sha256


def test_running_step_replay_returns_reconciliation_with_step_id() -> None:
    from ato_service.db.models import PackageNormalizationStep
    from ato_service.normalization_service import _reserve_normalization_step

    running_step = PackageNormalizationStep(
        step_id=uuid.uuid4(),
        package_revision_id=REVISION_ID,
        step_key="normalize_proposal",
        status="running",
        input_digest="d" * 64,
        llm_call_count=1,
        repair_attempted=False,
        created_at=NOW,
        started_at=NOW,
    )
    work = MagicMock()
    marked: list[str] = []

    async def fake_mark(
        session: object,
        *,
        step: PackageNormalizationStep,
        claimed: object,
        lease_owner: str,
        now: datetime,
    ) -> None:
        marked.append(step.status)
        step.status = "reconciliation_required"

    class SessionContext:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _invoke() -> object:
        with patch(
            "ato_service.normalization_service.session_scope",
            return_value=SessionContext(),
        ), patch(
            "ato_service.normalization_service._load_owned_normalization_context",
            return_value=(work, running_step),
        ), patch(
            "ato_service.normalization_service._mark_reconciliation_in_session",
            side_effect=fake_mark,
        ):
            return await _reserve_normalization_step(
                session_factory=MagicMock(),
                snapshot=_revision_snapshot(),
                claimed=MagicMock(
                    package_revision_id=REVISION_ID,
                    work_phase="deterministic_extract",
                    fence_token=FENCE_TOKEN,
                ),
                lease_owner="intake-test",
                now=NOW,
                input_digest="d" * 64,
                runtime_metadata=resolve_text_model_runtime_metadata(
                    load_runtime_config_from_dict(
                        {
                            "schema_version": "1.0.0",
                            "runtime_profile": "dev_local",
                            "STORAGE_DATA_PATH": "/data",
                        },
                        base_dir=ROOT,
                    )
                ),
            )

    outcome = _run(_invoke())
    assert outcome.reconciliation_required is True
    assert outcome.step_id == running_step.step_id
    assert marked == ["running"]
