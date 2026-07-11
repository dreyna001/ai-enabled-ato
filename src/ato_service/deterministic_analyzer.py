"""Deterministic analysis-run worker step for dev_local synthetic packages."""

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
    GeneratedRunFile,
    write_artifact_manifest,
    write_run_output_file,
)
from ato_service.audit import append_audit_event
from ato_service.db.models import AnalysisRun, MatrixRow, PackageRevision, RunStep
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

DETERMINISTIC_STEP_KEY = "sufficiency_matrix"
DETERMINISTIC_STEP_TYPE = "sufficiency_matrix"
DETERMINISTIC_SCHEMA_ID = "ato.matrix-row.v1"
DETERMINISTIC_PROMPT_VERSION = "deterministic-1"
DETERMINISTIC_ENDPOINT_HOST = "deterministic.local"
DETERMINISTIC_MODEL_NAME = "none"
DETERMINISTIC_PROVIDER_REQUEST_ID = "deterministic"
DETERMINISTIC_VALIDATION_OUTCOME = "passed"
MATRIX_OUTPUT_PATH = "machine/matrix.json"
WORKER_ACTOR_ID = "deterministic-analyzer-worker"


class DeterministicAnalyzerRuntimeError(Exception):
    """Raised when the deterministic analyzer worker is started outside its gate."""


class DeterministicAnalysisProcessingError(Exception):
    """Raised when deterministic analysis cannot complete."""

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
class DeterministicAnalysisResult:
    """Outcome of one claimed deterministic analysis job."""

    run_id: uuid.UUID
    package_revision_id: uuid.UUID
    matrix_row_count: int
    artifact_manifest_sha256: str


def require_deterministic_analyzer_runtime(config: RuntimeConfig) -> None:
    """Require the dev_local runtime profile for the deterministic analyzer worker."""
    if config.runtime_profile != "dev_local":
        raise DeterministicAnalyzerRuntimeError(
            "deterministic analyzer worker requires runtime_profile=dev_local"
        )


def _deterministic_prompt_sha256() -> str:
    return hashlib.sha256(
        canonical_json_bytes({"step": DETERMINISTIC_STEP_KEY, "version": DETERMINISTIC_PROMPT_VERSION})
    ).hexdigest()


def _deterministic_fact_bundle_sha256(*, package_revision_id: uuid.UUID) -> str:
    return hashlib.sha256(
        canonical_json_bytes({"package_revision_id": format_uuid(package_revision_id)})
    ).hexdigest()


def _deterministic_response_sha256(*, rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes({"rows": rows})).hexdigest()


def _build_matrix_rows(
    *,
    run_id: uuid.UUID,
    profile: dict[str, Any],
    assessment_item_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assessment_item_id in assessment_item_ids:
        matrix_row_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{format_uuid(run_id)}:{assessment_item_id}",
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
                "model_proposed_status": "insufficient_evidence",
                "system_status": "insufficient_evidence",
                "finding_summary": (
                    f"No usable evidence linked for {assessment_item_id} in the "
                    "synthetic package snapshot."
                ),
                "gaps": [
                    "No usable evidence linked for this assessment item.",
                ],
                "assessor_questions": [],
                "citations": [],
                "context_complete": False,
                "producing_run_id": format_uuid(run_id),
                "source_run_id": format_uuid(run_id),
            }
        )
    return rows


async def process_next_deterministic_analysis(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    package_revision: PackageRevision,
    analysis_run: AnalysisRun,
    storage_root: Path,
    project_root: Path,
    hmac_key: bytes,
    now: datetime,
) -> DeterministicAnalysisResult:
    """Execute one deterministic matrix step for a claimed analyzer job."""
    if claimed.job.step_key != DETERMINISTIC_STEP_KEY:
        raise DeterministicAnalysisProcessingError(
            "unexpected job step_key",
            error_code="reconciliation_required",
        )
    if analysis_run.run_type != "deterministic_only":
        raise DeterministicAnalysisProcessingError(
            "deterministic worker only supports deterministic_only runs",
            error_code="prohibited_model_action",
        )
    if package_revision.data_origin != "synthetic":
        raise DeterministicAnalysisProcessingError(
            "deterministic worker requires synthetic package revisions",
            error_code="model_routing_denied",
        )
    if package_revision.status != "ready":
        raise DeterministicAnalysisProcessingError(
            "deterministic worker requires ready package revisions",
            error_code="illegal_state_transition",
        )

    profile = load_pinned_fisma_synthetic_profile(project_root=project_root)
    expected_ids = expected_assessment_item_ids(profile)
    row_payloads = _build_matrix_rows(
        run_id=analysis_run.run_id,
        profile=profile,
        assessment_item_ids=expected_ids,
    )
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

    response_sha256 = _deterministic_response_sha256(rows=row_payloads)
    session.add(
        RunStep(
            step_id=uuid.uuid4(),
            run_id=analysis_run.run_id,
            step_key=DETERMINISTIC_STEP_KEY,
            step_type=DETERMINISTIC_STEP_TYPE,
            schema_id=DETERMINISTIC_SCHEMA_ID,
            prompt_version=DETERMINISTIC_PROMPT_VERSION,
            prompt_sha256=_deterministic_prompt_sha256(),
            fact_bundle_sha256=_deterministic_fact_bundle_sha256(
                package_revision_id=package_revision.package_revision_id,
            ),
            endpoint_profile=EndpointProfile.MOCK.value,
            endpoint_host=DETERMINISTIC_ENDPOINT_HOST,
            model_requested=DETERMINISTIC_MODEL_NAME,
            model_reported=DETERMINISTIC_MODEL_NAME,
            temperature=0,
            input_limit=1,
            output_limit=1,
            timeout_seconds=1,
            attempt=claimed.attempt.attempt_number,
            provider_request_id=DETERMINISTIC_PROVIDER_REQUEST_ID,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            response_sha256=response_sha256,
            validation_outcome=DETERMINISTIC_VALIDATION_OUTCOME,
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
        raise DeterministicAnalysisProcessingError(
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
    analysis_run.llm_call_count = 0
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
            "llm_call_count": 0,
        },
        now=now,
    )

    return DeterministicAnalysisResult(
        run_id=analysis_run.run_id,
        package_revision_id=package_revision.package_revision_id,
        matrix_row_count=len(row_payloads),
        artifact_manifest_sha256=stored_manifest.sha256,
    )
