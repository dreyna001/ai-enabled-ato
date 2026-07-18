"""Deterministic fake-based tests for routed model-assisted analysis."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ato_service.analysis_profile import expected_assessment_item_ids, load_pinned_profile
from ato_service.analysis_runs import StartRunInput, start_run
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.citation_validation import build_evidence_citation
from ato_service.db.models import AnalysisRun, Job, MatrixRow, PackageRevision, SealedPackageContent
from ato_service.idempotency import IdempotencyReplay, canonical_json_bytes
from ato_service.jobs import ClaimedJob, JobAttempt
from ato_service.model_assisted_analyzer import (
    ModelAssistedAnalysisProcessingError,
    build_model_call_request,
    process_next_model_assisted_analysis,
)
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.sufficiency_matrix.parse import validate_and_parse_response
from ato_service.sufficiency_matrix.runner import run_sufficiency_matrix
from ato_service.text_llm import ChatMessage, TextModelCallError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "sufficiency_matrix" / "fixtures"
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ATTEMPT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
SOURCE_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
STATEMENT = (
    "The provider maintains documented account management procedures."
)


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _config(tmp_path: Path, **overrides: Any) -> Any:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": str(tmp_path / "storage"),
        "TEXT_MODEL_ENDPOINT_URL": "https://mock.local/v1",
        "TEXT_MODEL_NAME": "mock-assisted",
        "TEXT_MODEL_CONTEXT_TOKENS": 8192,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS": 1024,
        "TEXT_MODEL_TIMEOUT_SECONDS": 30,
        "TEXT_MODEL_ENDPOINT_PROFILE": "external_openai",
        "MAX_MODEL_CALLS_PER_RUN": 4,
    }
    document.update(overrides)
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _resolved_input_budget(tmp_path: Path, **overrides: Any) -> int:
    return _config(tmp_path, **overrides).resolve_text_model_context_budget().input_budget_tokens


@dataclass
class FakeTextClient:
    responses: list[str] = field(default_factory=list)
    calls: int = 0
    provider: str = "openai_compatible"
    fail_with_timeout: bool = False

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        system: str | None = None,
    ) -> str:
        self.calls += 1
        if self.fail_with_timeout:
            raise TextModelCallError("OpenAI-compatible text model request failed")
        if not self.responses:
            raise TextModelCallError("no fake response configured")
        return self.responses.pop(0)


def _source_sha256(pointer: str, text: str) -> str:
    return hashlib.sha256(f"{pointer}:{text}".encode("utf-8")).hexdigest()


def _sealed_document(*, control_id: str = "FR-1") -> dict[str, Any]:
    return {
        "package": {"profile_id": "fedramp_20x_program", "title": "Demo"},
        "security_controls": {
            control_id: {
                "implementation_statement": STATEMENT,
            }
        },
        "evidence": {},
    }


def _field_provenance(*, control_id: str = "FR-1") -> dict[str, Any]:
    pointer = f"/security_controls/{control_id}/implementation_statement"
    return {
        pointer: {
            "source_artifact_id": str(SOURCE_ID).lower(),
            "source_sha256": _source_sha256(pointer, STATEMENT),
        }
    }


def _sealed(content_sha256: str | None = None) -> SealedPackageContent:
    document = _sealed_document()
    provenance = _field_provenance()
    digest = content_sha256 or hashlib.sha256(
        canonical_json_bytes({"document": document, "field_provenance": provenance})
    ).hexdigest()
    return SealedPackageContent(
        package_revision_id=REVISION_ID,
        document=document,
        field_provenance=provenance,
        content_sha256=digest,
        sealed_at=NOW,
    )


def _package_revision(*, data_origin: str = "synthetic", sensitivity: str = "internal_unclassified") -> PackageRevision:
    sealed = _sealed()
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=uuid.uuid4(),
        parent_revision_id=None,
        profile_id="fedramp_20x_program",
        certification_class="C",
        impact_level=None,
        data_origin=data_origin,
        sensitivity=sensitivity,
        effective_data_labels=[sensitivity, data_origin],
        authority_manifest_id="authority.v2",
        content_manifest_sha256="a" * 64,
        package_content_sha256=sealed.content_sha256,
        revision_version=1,
        status="ready",
        created_by="tester",
        created_at=NOW,
    )


def _analysis_run(*, run_type: str = "targeted") -> AnalysisRun:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    return AnalysisRun(
        run_id=RUN_ID,
        package_revision_id=REVISION_ID,
        parent_run_id=None,
        run_type=run_type,
        status="running",
        requested_by="tester",
        requested_at=NOW,
        started_at=NOW,
        completed_at=None,
        authority_manifest_id="authority.v2",
        analysis_profile_sha256=hashlib.sha256(
            canonical_json_bytes(profile)
        ).hexdigest(),
        config_fingerprint="c" * 64,
        prompt_bundle_sha256="d" * 64,
        model_profile="openai_compatible",
        artifact_manifest_sha256=None,
        llm_call_count=0,
        assessment_item_ids=["FR-1"],
        error_code=None,
        error_retryable=None,
    )


def _claimed() -> ClaimedJob:
    job = Job(
        job_id=JOB_ID,
        run_id=RUN_ID,
        step_key="sufficiency_matrix",
        step_idempotent=True,
        status="leased",
        attempt_count=1,
        available_at=NOW,
        lease_owner="worker",
        lease_expires_at=NOW,
        heartbeat_at=NOW,
        last_error_code=None,
    )
    attempt = JobAttempt(
        attempt_id=ATTEMPT_ID,
        job_id=JOB_ID,
        run_id=RUN_ID,
        step_key="sufficiency_matrix",
        attempt_number=1,
        status="active",
        lease_owner="worker",
        started_at=NOW,
        completed_at=None,
        error_code=None,
        error_retryable=None,
    )
    return ClaimedJob(job=job, attempt=attempt, run_started=True)


def _valid_response_json() -> str:
    sources = _field_provenance()
    pointer = "/security_controls/FR-1/implementation_statement"
    source_sha256 = sources[pointer]["source_sha256"]
    citation = build_evidence_citation(
        source=type(
            "Source",
            (),
            {
                "source_id": str(SOURCE_ID).lower(),
                "source_sha256": source_sha256,
                "text": STATEMENT,
            },
        )(),
        start_offset=0,
        end_offset=len(STATEMENT),
    )
    payload = {
        "schema_version": "1.0.0",
        "rows": [
            {
                "assessment_item_id": "FR-1",
                "model_proposed_status": "supported",
                "finding_summary": "Supported by sealed statement.",
                "gaps": [],
                "assessor_questions": [],
                "citations": [citation],
                "context_complete": True,
            }
        ],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def test_build_model_call_request_requires_production_approval_flags(tmp_path: Path) -> None:
    revision = _package_revision(data_origin="redacted_nonproduction", sensitivity="customer_sensitive")
    request = build_model_call_request(package_revision=revision, config=_config(tmp_path))
    assert request.endpoint_policy_approved is False


def test_routing_denial_makes_zero_llm_calls(tmp_path: Path) -> None:
    revision = _package_revision(data_origin="customer_production", sensitivity="customer_sensitive")
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(package_revision=revision, config=_config(tmp_path)),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=FakeTextClient(responses=[_valid_response_json()]),
        )
    )
    assert result.validation_outcome == "rejected_routing"
    assert result.llm_call_count == 0
    assert result.error_code == "model_routing_denied"


def test_happy_targeted_run_persists_rows_with_one_llm_call(tmp_path: Path) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    client = FakeTextClient(responses=[_valid_response_json()])
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.complete_job",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.append_audit_event",
            AsyncMock(),
        )
        result = _run(
            process_next_model_assisted_analysis(
                session,
                claimed=_claimed(),
                package_revision=_package_revision(),
                analysis_run=_analysis_run(run_type="targeted"),
                sealed=_sealed(),
                storage_root=tmp_path / "storage",
                project_root=ROOT,
                config=_config(tmp_path),
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
                text_client=client,
            )
        )
    assert result.llm_call_count == 1
    assert client.calls == 1
    assert result.matrix_row_count == 1
    assert session.add.call_count >= 2


def test_happy_full_run_covers_all_profile_items(tmp_path: Path) -> None:
    profile = load_pinned_profile(profile_id="fisma_agency_security", project_root=ROOT)
    expected_ids = expected_assessment_item_ids(profile)
    session = AsyncMock()
    session.add = MagicMock()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.complete_job",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.append_audit_event",
            AsyncMock(),
        )
        result = _run(
            process_next_model_assisted_analysis(
                session,
                claimed=_claimed(),
                package_revision=PackageRevision(
                    package_revision_id=REVISION_ID,
                    system_id=uuid.uuid4(),
                    parent_revision_id=None,
                    profile_id="fisma_agency_security",
                    certification_class=None,
                    impact_level="moderate",
                    data_origin="synthetic",
                    sensitivity="internal_unclassified",
                    effective_data_labels=["internal_unclassified", "synthetic"],
                    authority_manifest_id="authority.v2",
                    content_manifest_sha256="a" * 64,
                    package_content_sha256=_sealed().content_sha256,
                    revision_version=1,
                    status="ready",
                    created_by="tester",
                    created_at=NOW,
                ),
                analysis_run=AnalysisRun(
                    run_id=RUN_ID,
                    package_revision_id=REVISION_ID,
                    parent_run_id=None,
                    run_type="full",
                    status="running",
                    requested_by="tester",
                    requested_at=NOW,
                    started_at=NOW,
                    completed_at=None,
                    authority_manifest_id="authority.v2",
                    analysis_profile_sha256=hashlib.sha256(canonical_json_bytes(profile)).hexdigest(),
                    config_fingerprint="c" * 64,
                    prompt_bundle_sha256="d" * 64,
                    model_profile="openai_compatible",
                    artifact_manifest_sha256=None,
                    llm_call_count=0,
                    assessment_item_ids=list(expected_ids),
                    error_code=None,
                    error_retryable=None,
                ),
                sealed=_sealed(),
                storage_root=tmp_path / "storage",
                project_root=ROOT,
                config=_config(tmp_path),
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
                text_client=FakeTextClient(responses=[]),
            )
        )
    assert result.matrix_row_count == len(expected_ids)
    assert result.llm_call_count == 0


def test_malformed_response_uses_one_repair_then_fails(tmp_path: Path) -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    client = FakeTextClient(responses=["not-json", _valid_response_json()])
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(package_revision=_package_revision(), config=_config(tmp_path)),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=client,
        )
    )
    assert result.validation_outcome == "accepted"
    assert client.calls == 2
    assert result.llm_call_count == 2


def test_malformed_response_after_repair_fails_without_persistence(tmp_path: Path) -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    client = FakeTextClient(responses=["not-json", "still-not-json"])
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(
                package_revision=_package_revision(),
                config=_config(tmp_path, MAX_MODEL_CALLS_PER_RUN=2),
            ),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=client,
        )
    )
    assert result.validation_outcome == "repair_exhausted"
    assert result.row_payloads == ()


def test_timeout_is_retryable_and_surfaces_model_timeout(tmp_path: Path) -> None:
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    client = FakeTextClient(fail_with_timeout=True)
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(package_revision=_package_revision(), config=_config(tmp_path)),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=client,
        )
    )
    assert result.validation_outcome == "model_timeout"
    assert result.retryable is True


def test_citation_mismatch_rejects_row(tmp_path: Path) -> None:
    raw = json.loads(_valid_response_json())
    raw["rows"][0]["citations"][0]["chunk_id"] = "f" * 64
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(package_revision=_package_revision(), config=_config(tmp_path)),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=FakeTextClient(responses=[json.dumps(raw)]),
        )
    )
    assert result.validation_outcome == "rejected_citation"


def test_missing_duplicate_and_extra_rows_fail_coverage() -> None:
    raw = json.loads(_valid_response_json())
    with pytest.raises(Exception):
        validate_and_parse_response(raw_text=json.dumps(raw), expected_assessment_item_ids=("FR-1", "FR-2"))
    duplicate = copy.deepcopy(raw)
    duplicate["rows"].append(copy.deepcopy(duplicate["rows"][0]))
    with pytest.raises(Exception):
        validate_and_parse_response(raw_text=json.dumps(duplicate), expected_assessment_item_ids=("FR-1",))
    extra = copy.deepcopy(raw)
    extra["rows"][0]["assessment_item_id"] = "EXTRA-1"
    with pytest.raises(Exception):
        validate_and_parse_response(raw_text=json.dumps(extra), expected_assessment_item_ids=("FR-1",))


def test_status_ceiling_rejects_supported_without_evidence(tmp_path: Path) -> None:
    raw = {
        "schema_version": "1.0.0",
        "rows": [
            {
                "assessment_item_id": "FR-1",
                "model_proposed_status": "supported",
                "finding_summary": "Claimed without evidence.",
                "gaps": [],
                "assessor_questions": [],
                "citations": [],
                "context_complete": True,
            }
        ],
    }
    profile = load_pinned_profile(profile_id="fedramp_20x_program", project_root=ROOT)
    result = _run(
        run_sufficiency_matrix(
            run_id=RUN_ID,
            profile=profile,
            assessment_item_ids=("FR-1",),
            sealed=_sealed(),
            model_request=build_model_call_request(package_revision=_package_revision(), config=_config(tmp_path)),
            input_budget_tokens=_resolved_input_budget(tmp_path),
            max_output_tokens=1024,
            model_requested="mock-assisted",
            text_client=FakeTextClient(responses=[json.dumps(raw)]),
        )
    )
    assert result.validation_outcome == "rejected_status_ceiling"


def test_start_run_replay_returns_same_payload(tmp_path: Path) -> None:
    session = AsyncMock()
    package_revision = MagicMock()
    package_revision.package_revision_id = REVISION_ID
    package_revision.profile_id = "fisma_agency_security"
    package_revision.data_origin = "synthetic"
    package_revision.status = "ready"
    package_revision.package_content_sha256 = "a" * 64
    system = MagicMock(owner_group="owners", viewer_groups=["viewers"])
    result = MagicMock()
    result.one_or_none.return_value = (package_revision, system)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=0)
    replay_payload = {"run_id": str(RUN_ID).lower(), "run_type": "full"}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "ato_service.analysis_runs.load_idempotency_replay",
            AsyncMock(
                return_value=IdempotencyReplay(
                    response_status=202,
                    response_body=replay_payload,
                )
            ),
        )
        response = _run(
            start_run(
                session,
                principal=type("Principal", (), {"actor_id": "actor-1", "groups": ("owners",)})(),
                package_revision_id=REVISION_ID,
                request=StartRunInput(run_type="full", parent_run_id=None, assessment_item_ids=()),
                config=_config(tmp_path),
                authority_manifest_id="authority.v2",
                project_root=ROOT,
                idempotency_key="idempotency-key-01234567",
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
            )
        )
    assert response.replayed is True
    assert response.payload == replay_payload


def test_partial_rollback_leaves_no_matrix_rows_on_failure(tmp_path: Path) -> None:
    session = AsyncMock()
    added: list[Any] = []
    session.add = lambda obj: added.append(obj)
    with pytest.raises(ModelAssistedAnalysisProcessingError) as exc_info:
        _run(
            process_next_model_assisted_analysis(
                session,
                claimed=_claimed(),
                package_revision=_package_revision(),
                analysis_run=_analysis_run(run_type="targeted"),
                sealed=_sealed(),
                storage_root=tmp_path / "storage",
                project_root=ROOT,
                config=_config(tmp_path),
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
                text_client=FakeTextClient(responses=["not-json", "still-not-json"]),
            )
        )
    assert exc_info.value.error_code == "model_response_schema_invalid"
    assert not any(isinstance(obj, MatrixRow) for obj in added)


def test_policy_blocked_terminalizes_run_with_zero_llm_calls(tmp_path: Path) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    analysis_run = _analysis_run(run_type="targeted")
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        pytest.raises(ModelAssistedAnalysisProcessingError) as exc_info,
    ):
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.complete_job",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "ato_service.model_assisted_analyzer.append_audit_event",
            AsyncMock(),
        )
        _run(
            process_next_model_assisted_analysis(
                session,
                claimed=_claimed(),
                package_revision=_package_revision(
                    data_origin="customer_production",
                    sensitivity="customer_sensitive",
                ),
                analysis_run=analysis_run,
                sealed=_sealed(),
                storage_root=tmp_path / "storage",
                project_root=ROOT,
                config=_config(
                    tmp_path,
                    TEXT_MODEL_ENDPOINT_POLICY_APPROVED=True,
                    CUI_MODEL_BOUNDARY_APPROVED=True,
                ),
                hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                now=NOW,
                text_client=FakeTextClient(responses=[_valid_response_json()]),
            )
        )
    assert exc_info.value.policy_blocked is True
    assert analysis_run.status == "policy_blocked"
    assert analysis_run.llm_call_count == 0
