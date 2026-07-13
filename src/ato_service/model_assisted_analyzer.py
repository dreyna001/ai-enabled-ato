"""Model-assisted analysis using sealed package content (dev_local mock route)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.analysis_profile import (
    assessment_item_type_for_id,
    expected_assessment_item_ids,
    load_pinned_fisma_synthetic_profile,
)
from ato_service.artifact_manifests import (
    ArtifactManifestCommitError,
    write_artifact_manifest,
    write_run_output_file,
)
from ato_service.audit import append_audit_event
from ato_service.citation_validation import (
    CitationValidationError,
    build_evidence_citation,
    build_sealed_citable_sources,
    validate_citations,
)
from ato_service.db.models import AnalysisRun, MatrixRow, PackageRevision, RunStep, SealedPackageContent
from ato_service.domain_mapping import format_uuid
from ato_service.idempotency import canonical_json_bytes
from ato_service.jobs import ClaimedJob, complete_job
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    require_analysis_run_transition,
)
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.model_routing import EndpointProfile
from ato_service.runtime_config import RuntimeConfig

TARGETED_STEP_KEY = "sufficiency_matrix"
TARGETED_STEP_TYPE = "sufficiency_matrix"
TARGETED_SCHEMA_ID = "ato.matrix-row.v1"
TARGETED_PROMPT_VERSION = "sealed-mock-1"
TARGETED_ENDPOINT_HOST = "mock.local"
TARGETED_MODEL_NAME = "mock-assisted"
TARGETED_PROVIDER_REQUEST_ID = "mock-assisted"
MATRIX_OUTPUT_PATH = "machine/matrix.json"
WORKER_ACTOR_ID = "model-assisted-analyzer-worker"


class ModelAssistedAnalyzerRuntimeError(Exception):
    """Raised when the model-assisted analyzer worker is started outside its gate."""


class ModelAssistedAnalysisProcessingError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ModelAssistedAnalysisResult:
    run_id: uuid.UUID
    package_revision_id: uuid.UUID
    matrix_row_count: int
    artifact_manifest_sha256: str
    llm_call_count: int


def require_model_assisted_analyzer_runtime(config: RuntimeConfig) -> None:
    if config.runtime_profile != "dev_local":
        raise ModelAssistedAnalyzerRuntimeError(
            "model-assisted analyzer worker requires runtime_profile=dev_local"
        )


def _prompt_sha256() -> str:
    return hashlib.sha256(
        canonical_json_bytes({"step": TARGETED_STEP_KEY, "version": TARGETED_PROMPT_VERSION})
    ).hexdigest()


def _fact_bundle_sha256(*, package_revision_id: uuid.UUID, content_sha256: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "package_revision_id": format_uuid(package_revision_id),
                "package_content_sha256": content_sha256,
            }
        )
    ).hexdigest()


def _response_sha256(*, rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes({"rows": rows})).hexdigest()


def _build_matrix_rows_from_sealed_content(
    *,
    run_id: uuid.UUID,
    profile: dict[str, Any],
    assessment_item_ids: tuple[str, ...],
    sealed: SealedPackageContent,
) -> list[dict[str, Any]]:
    sources = build_sealed_citable_sources(
        sealed_document=sealed.document,
        field_provenance=sealed.field_provenance,
    )
    rows: list[dict[str, Any]] = []
    for assessment_item_id in assessment_item_ids:
        matrix_row_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{format_uuid(run_id)}:{assessment_item_id}",
        )
        control = (sealed.document.get("security_controls") or {}).get(assessment_item_id)
        citations: list[dict[str, Any]] = []
        status = "insufficient_evidence"
        summary = (
            f"No usable sealed evidence linked for {assessment_item_id} in the "
            "package snapshot."
        )
        if isinstance(control, dict):
            statement = control.get("implementation_statement")
            if isinstance(statement, str) and statement.strip():
                source = sources.get(assessment_item_id)
                if source is not None and len(statement) >= 8:
                    citations = [
                        build_evidence_citation(
                            source=source,
                            start_offset=0,
                            end_offset=min(len(statement), 120),
                        )
                    ]
                    validate_citations(citations=citations, sources=sources)
                    status = "supported"
                    summary = (
                        f"Sealed implementation statement supports {assessment_item_id} "
                        "within the cited byte range."
                    )
        rows.append(
            {
                "schema_version": "2.0.0",
                "object_type": "matrix_row",
                "matrix_row_id": format_uuid(matrix_row_id),
                "assessment_item_type": assessment_item_type_for_id(
                    profile,
                    assessment_item_id,
                ),
                "assessment_item_id": assessment_item_id,
                "model_proposed_status": status,
                "system_status": status,
                "finding_summary": summary,
                "gaps": [] if status == "supported" else ["No usable evidence linked."],
                "assessor_questions": [],
                "citations": citations,
                "context_complete": status == "supported",
                "producing_run_id": format_uuid(run_id),
                "source_run_id": format_uuid(run_id),
            }
        )
    return rows


async def process_next_model_assisted_analysis(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    package_revision: PackageRevision,
    analysis_run: AnalysisRun,
    sealed: SealedPackageContent,
    storage_root: Path,
    project_root: Path,
    hmac_key: bytes,
    now: datetime,
) -> ModelAssistedAnalysisResult:
    """Execute one targeted matrix step using sealed package bytes and mock routing."""
    if claimed.job.step_key != TARGETED_STEP_KEY:
        raise ModelAssistedAnalysisProcessingError(
            "unexpected job step_key",
            error_code="reconciliation_required",
        )
    if analysis_run.run_type != "targeted":
        raise ModelAssistedAnalysisProcessingError(
            "model-assisted worker only supports targeted runs",
            error_code="prohibited_model_action",
        )
    if package_revision.data_origin != "synthetic":
        raise ModelAssistedAnalysisProcessingError(
            "model-assisted worker requires synthetic package revisions",
            error_code="model_routing_denied",
        )
    if package_revision.status != "ready":
        raise ModelAssistedAnalysisProcessingError(
            "model-assisted worker requires ready package revisions",
            error_code="illegal_state_transition",
        )
    if sealed.content_sha256 != package_revision.package_content_sha256:
        raise ModelAssistedAnalysisProcessingError(
            "sealed package digest does not match revision binding",
            error_code="artifact_digest_mismatch",
        )

    profile = load_pinned_fisma_synthetic_profile(project_root=project_root)
    expected_ids = expected_assessment_item_ids(profile)
    try:
        row_payloads = _build_matrix_rows_from_sealed_content(
            run_id=analysis_run.run_id,
            profile=profile,
            assessment_item_ids=expected_ids,
            sealed=sealed,
        )
    except CitationValidationError as exc:
        raise ModelAssistedAnalysisProcessingError(
            exc.message,
            error_code=exc.error_code,
        ) from exc

    require_exact_matrix_coverage(
        expected_ids,
        [row["assessment_item_id"] for row in row_payloads],
    )

    matrix_bytes = json.dumps(
        row_payloads,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generated_file = write_run_output_file(
        storage_root=storage_root,
        run_id=format_uuid(analysis_run.run_id),
        relative_path=MATRIX_OUTPUT_PATH,
        payload=matrix_bytes,
    )

    for payload in row_payloads:
        session.add(
            MatrixRow(
                matrix_row_id=uuid.UUID(payload["matrix_row_id"]),
                run_id=analysis_run.run_id,
                assessment_item_type=payload["assessment_item_type"],
                assessment_item_id=payload["assessment_item_id"],
                model_proposed_status=payload["model_proposed_status"],
                system_status=payload["system_status"],
                finding_summary=payload["finding_summary"],
                gaps=list(payload["gaps"]),
                assessor_questions=list(payload["assessor_questions"]),
                citations=list(payload["citations"]),
                context_complete=payload["context_complete"],
                producing_run_id=analysis_run.run_id,
                source_run_id=analysis_run.run_id,
            )
        )

    response_sha256 = _response_sha256(rows=row_payloads)
    session.add(
        RunStep(
            step_id=uuid.uuid4(),
            run_id=analysis_run.run_id,
            step_key=TARGETED_STEP_KEY,
            step_type=TARGETED_STEP_TYPE,
            schema_id=TARGETED_SCHEMA_ID,
            prompt_version=TARGETED_PROMPT_VERSION,
            prompt_sha256=_prompt_sha256(),
            fact_bundle_sha256=_fact_bundle_sha256(
                package_revision_id=package_revision.package_revision_id,
                content_sha256=sealed.content_sha256,
            ),
            endpoint_profile=EndpointProfile.MOCK.value,
            endpoint_host=TARGETED_ENDPOINT_HOST,
            model_requested=TARGETED_MODEL_NAME,
            model_reported=TARGETED_MODEL_NAME,
            temperature=0,
            input_limit=1,
            output_limit=1,
            timeout_seconds=1,
            attempt=claimed.attempt.attempt_number,
            provider_request_id=TARGETED_PROVIDER_REQUEST_ID,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            response_sha256=response_sha256,
            validation_outcome="passed",
            completed_at=now,
        )
    )

    try:
        stored_manifest = write_artifact_manifest(
            run_id=format_uuid(analysis_run.run_id),
            package_revision_id=format_uuid(package_revision.package_revision_id),
            authority_manifest_id=analysis_run.authority_manifest_id,
            analysis_profile_sha256=analysis_run.analysis_profile_sha256,
            config_fingerprint=analysis_run.config_fingerprint,
            prompt_bundle_sha256=analysis_run.prompt_bundle_sha256,
            completed_at=now,
            generated_files=[generated_file],
            storage_root=storage_root,
            project_root=project_root,
        )
    except ArtifactManifestCommitError as exc:
        raise ModelAssistedAnalysisProcessingError(
            str(exc),
            error_code="artifact_manifest_commit_failed",
        ) from exc

    require_analysis_run_transition(
        AnalysisRunStatus(analysis_run.status),
        AnalysisRunStatus.SUCCEEDED,
        condition=AnalysisRunTransitionCondition.OUTPUTS_COMMITTED,
    )
    analysis_run.status = AnalysisRunStatus.SUCCEEDED.value
    analysis_run.completed_at = now
    analysis_run.artifact_manifest_sha256 = stored_manifest.sha256
    analysis_run.llm_call_count = 1
    analysis_run.error_code = None
    analysis_run.error_retryable = None

    await complete_job(
        session,
        job_id=claimed.job.job_id,
        lease_owner=claimed.attempt.lease_owner,
        now=now,
    )

    await append_audit_event(
        session,
        hmac_key=hmac_key,
        actor_type="service",
        actor_id=WORKER_ACTOR_ID,
        action="analysis_run.completed",
        object_type="analysis_run",
        object_id=format_uuid(analysis_run.run_id),
        outcome="succeeded",
        reason_code=None,
        metadata={
            "run_type": analysis_run.run_type,
            "matrix_row_count": len(row_payloads),
            "artifact_manifest_sha256": stored_manifest.sha256,
            "llm_call_count": 1,
            "sealed_content_sha256": sealed.content_sha256,
        },
        occurred_at=now,
    )

    return ModelAssistedAnalysisResult(
        run_id=analysis_run.run_id,
        package_revision_id=package_revision.package_revision_id,
        matrix_row_count=len(row_payloads),
        artifact_manifest_sha256=stored_manifest.sha256,
        llm_call_count=1,
    )
