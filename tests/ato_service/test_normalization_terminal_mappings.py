"""ORM field mappings for terminal PackageNormalizationStep statuses."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from ato_service.db.base import Base
from ato_service.db.models import PackageNormalizationStep
from ato_service.draft_builder import AggregatedIntakeDraft
from ato_service.normalization_artifacts import StoredNormalizationArtifact
from ato_service.normalization_service import (
    PendingNormalizationOutcome,
    StoredNormalizationArtifacts,
    terminalize_normalization_step,
)
from ato_service.normalize_proposal.types import ModelCallMetadata, NormalizeProposalResult

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _empty_document() -> dict[str, object]:
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
        "extensions": {"unmapped_segments": []},
    }


def _draft() -> AggregatedIntakeDraft:
    return AggregatedIntakeDraft(
        document=_empty_document(),
        field_provenance={},
        system_context_proposal=None,
        segment_count=0,
    )


def _status_fields_ddl() -> str:
    return str(
        CreateTable(Base.metadata.tables["package_normalization_steps"]).compile(
            dialect=postgresql.dialect()
        )
    )


def _base_step(*, status: str) -> PackageNormalizationStep:
    return PackageNormalizationStep(
        step_id=uuid.uuid4(),
        package_revision_id=REVISION_ID,
        step_key="normalize_proposal",
        status=status,
        input_digest="d" * 64,
        llm_call_count=0,
        repair_attempted=False,
        created_at=NOW,
    )


def _protected_artifacts() -> StoredNormalizationArtifacts:
    return StoredNormalizationArtifacts(
        prompt=StoredNormalizationArtifact(
            storage_key=(
                "revisions/11111111-1111-4111-8111-111111111111/normalization/"
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/prompt.json"
            ),
            sha256="p" * 64,
            size_bytes=10,
        ),
        fact_bundle=StoredNormalizationArtifact(
            storage_key=(
                "revisions/11111111-1111-4111-8111-111111111111/normalization/"
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/fact-bundle.json"
            ),
            sha256="f" * 64,
            size_bytes=10,
        ),
        response=StoredNormalizationArtifact(
            storage_key=(
                "revisions/11111111-1111-4111-8111-111111111111/normalization/"
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/response.json"
            ),
            sha256="r" * 64,
            size_bytes=10,
        ),
    )


def test_status_constraint_sql_covers_terminal_mappings() -> None:
    ddl = _status_fields_ddl()
    for status in (
        "reserved",
        "running",
        "completed",
        "policy_blocked",
        "failed",
        "reconciliation_required",
    ):
        assert f"status = '{status}'" in ddl


def test_terminalize_failed_sets_retryable_and_error_fields() -> None:
    step = _base_step(status="running")
    step.started_at = NOW
    step.llm_call_count = 1
    result = NormalizeProposalResult(
        document=_empty_document(),
        field_provenance={},
        validation_outcome="model_call_failed",
        llm_call_count=1,
        merged_targets=(),
        rejected_proposals=(),
        omitted_segment_ids=(),
        context_complete=True,
        fact_bundle_sha256="f" * 64,
        prompt_version="1.0.0",
        prompt_sha256="c" * 64,
        response_sha256=None,
        model_calls=(),
        error_code="model_timeout",
    )
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=step.step_id,
        input_digest="d" * 64,
        result=result,
        deterministic_draft=_draft(),
        protected_artifacts=None,
        runtime_metadata=None,
    )
    terminalize_normalization_step(MagicMock(), step=step, pending=pending, now=NOW)
    assert step.status == "failed"
    assert step.error_code == "model_timeout"
    assert step.error_retryable is True
    assert step.validation_outcome == "model_call_failed"
    assert step.completed_at == NOW


def test_terminalize_reconciliation_pending_sets_required_fields() -> None:
    step = _base_step(status="running")
    step.started_at = NOW
    step.llm_call_count = 1
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=True,
        step_id=step.step_id,
        input_digest="d" * 64,
        result=None,
        deterministic_draft=_draft(),
        protected_artifacts=None,
        runtime_metadata=None,
    )
    terminalize_normalization_step(MagicMock(), step=step, pending=pending, now=NOW)
    assert step.status == "reconciliation_required"
    assert step.error_code == "ambiguous_running_step"
    assert step.error_retryable is False
    assert step.validation_outcome == "reconciliation_required"
    assert step.completed_at == NOW


def test_terminalize_completed_sets_protected_hashes_not_inner_contract_hashes() -> None:
    step = _base_step(status="running")
    step.started_at = NOW
    step.llm_call_count = 1
    protected = _protected_artifacts()
    response_sha = "r" * 64
    result = NormalizeProposalResult(
        document=_empty_document(),
        field_provenance={},
        validation_outcome="accepted",
        llm_call_count=1,
        merged_targets=("/system/mission_summary",),
        rejected_proposals=(),
        omitted_segment_ids=(),
        context_complete=True,
        fact_bundle_sha256="inner-f" * 8,
        prompt_version="1.0.0",
        prompt_sha256="inner-p" * 8,
        response_sha256=response_sha,
        model_calls=(
            ModelCallMetadata(
                attempt=1,
                raw_response="{}",
                response_sha256=response_sha,
                latency_ms=9,
            ),
        ),
    )
    pending = PendingNormalizationOutcome(
        skipped=False,
        reconciliation_required=False,
        step_id=step.step_id,
        input_digest="d" * 64,
        result=result,
        deterministic_draft=_draft(),
        protected_artifacts=protected,
        runtime_metadata={
            "schema_id": "https://ato.local/schemas/normalize-proposal-response.schema.json",
            "prompt_version": "1.0.0",
            "endpoint_profile": "external_openai",
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
    assert step.prompt_sha256 == protected.prompt.sha256
    assert step.fact_bundle_sha256 == protected.fact_bundle.sha256
    assert step.prompt_sha256 != result.prompt_sha256
    assert step.fact_bundle_sha256 != result.fact_bundle_sha256
