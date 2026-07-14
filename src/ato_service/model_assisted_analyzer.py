"""Model-assisted analysis using sealed package content and routed gateway calls."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.analysis_profile import analysis_profile_sha256
from ato_service.artifact_manifests import (
    ArtifactManifestCommitError,
    write_artifact_manifest,
    write_run_output_file,
)
from ato_service.audit import append_audit_event
from ato_service.db.models import AnalysisRun, MatrixRow, PackageRevision, RunStep, SealedPackageContent
from ato_service.domain_mapping import format_uuid
from ato_service.idempotency import canonical_json_bytes
from ato_service.jobs import ClaimedJob, complete_job, transition_run_to_policy_blocked
from ato_service.lifecycle_transitions import (
    AnalysisRunStatus,
    AnalysisRunTransitionCondition,
    require_analysis_run_transition,
)
from ato_service.model_gateway import ModelCallRequest, ModelCapability
from ato_service.model_routing import DataOrigin, Sensitivity
from ato_service.normalization_service import resolve_text_model_endpoint_profile
from ato_service.runtime_config import RuntimeConfig
from ato_service.sufficiency_matrix.constants import RESPONSE_SCHEMA_ID
from ato_service.sufficiency_matrix.profile_catalog import load_profile_catalog
from ato_service.sufficiency_matrix.prompt import prompt_contract_metadata
from ato_service.sufficiency_matrix.runner import run_sufficiency_matrix
from ato_service.sufficiency_matrix.types import SufficiencyMatrixResult
from ato_service.text_llm import TextModelClient, text_model_is_configured

TARGETED_STEP_KEY = "sufficiency_matrix"
TARGETED_STEP_TYPE = "sufficiency_matrix"
MATRIX_OUTPUT_PATH = "machine/matrix.json"
WORKER_ACTOR_ID = "model-assisted-analyzer-worker"
_ROUTING_ERROR_CODES = frozenset(
    {
        "model_routing_denied",
        "classified_data_unsupported",
        "model_policy_not_approved",
        "prohibited_model_action",
    }
)


class ModelAssistedAnalyzerRuntimeError(Exception):
    """Raised when the model-assisted analyzer worker is started outside its gate."""


class ModelAssistedAnalysisProcessingError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        policy_blocked: bool = False,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.retryable = retryable
        self.policy_blocked = policy_blocked
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


def _resolve_endpoint_host(config: RuntimeConfig) -> str:
    document = config.document
    if not text_model_is_configured(document):
        return "mock.local"
    endpoint_url = document.get("TEXT_MODEL_ENDPOINT_URL")
    if isinstance(endpoint_url, str) and endpoint_url.strip():
        parsed = urlparse(endpoint_url.strip())
        if parsed.hostname:
            return parsed.hostname
    if config.text_model_provider == "aws_bedrock":
        region = document.get("AWS_REGION")
        if isinstance(region, str) and region.strip():
            return f"bedrock.{region.strip()}.amazonaws.com"
    return "model.local"


def _positive_int(document: dict[str, Any], key: str, *, default: int) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ModelAssistedAnalysisProcessingError(
            f"{key} must be a positive integer",
            error_code="reconciliation_required",
        )
    return raw


def build_model_call_request(
    *,
    package_revision: PackageRevision,
    config: RuntimeConfig,
    current_llm_call_count: int = 0,
) -> ModelCallRequest:
    document = config.document
    max_llm_calls = _positive_int(
        document,
        "MAX_MODEL_CALLS_PER_RUN",
        default=120,
    )
    return ModelCallRequest(
        capability=ModelCapability.SUFFICIENCY_MATRIX,
        data_origin=DataOrigin(package_revision.data_origin),
        sensitivity=Sensitivity(package_revision.sensitivity),
        endpoint_profile=resolve_text_model_endpoint_profile(config),
        endpoint_policy_approved=document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is True,
        cui_boundary_approved=document.get("CUI_MODEL_BOUNDARY_APPROVED") is True,
        vision_model_enabled=config.vision_model_enabled,
        current_llm_call_count=current_llm_call_count,
        max_llm_calls=max_llm_calls,
    )


def _resolved_assessment_item_ids(analysis_run: AnalysisRun) -> tuple[str, ...]:
    if analysis_run.assessment_item_ids:
        return tuple(str(item) for item in analysis_run.assessment_item_ids)
    raise ModelAssistedAnalysisProcessingError(
        "analysis run is missing assessment item ids",
        error_code="reconciliation_required",
    )


def _model_requested(config: RuntimeConfig) -> str:
    document = config.document
    if not text_model_is_configured(document):
        return "mock-assisted"
    model_name = document.get("TEXT_MODEL_NAME")
    if isinstance(model_name, str) and model_name.strip():
        return model_name.strip()
    return "mock-assisted"


def _runtime_limits(config: RuntimeConfig) -> tuple[int, int, float]:
    document = config.document
    context_tokens = _positive_int(document, "TEXT_MODEL_CONTEXT_TOKENS", default=8192)
    max_output_tokens = _positive_int(
        document,
        "TEXT_MODEL_MAX_OUTPUT_TOKENS",
        default=1024,
    )
    timeout_seconds = float(
        _positive_int(document, "TEXT_MODEL_TIMEOUT_SECONDS", default=30)
    )
    return context_tokens, max_output_tokens, timeout_seconds


async def _execute_sufficiency_matrix(
    *,
    analysis_run: AnalysisRun,
    package_revision: PackageRevision,
    sealed: SealedPackageContent,
    config: RuntimeConfig,
    project_root: Path,
    text_client: TextModelClient | None,
) -> SufficiencyMatrixResult:
    profile = load_profile_catalog(
        profile_id=package_revision.profile_id,
        project_root=project_root,
    )
    if analysis_run.analysis_profile_sha256 != analysis_profile_sha256(profile):
        raise ModelAssistedAnalysisProcessingError(
            "analysis profile digest does not match pinned catalog",
            error_code="artifact_digest_mismatch",
        )
    assessment_item_ids = _resolved_assessment_item_ids(analysis_run)
    context_tokens, max_output_tokens, _timeout_seconds = _runtime_limits(config)
    return await run_sufficiency_matrix(
        run_id=analysis_run.run_id,
        profile=profile,
        assessment_item_ids=assessment_item_ids,
        sealed=sealed,
        model_request=build_model_call_request(
            package_revision=package_revision,
            config=config,
        ),
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        model_requested=_model_requested(config),
        text_client=text_client,
    )


def _map_runner_failure(
    result: SufficiencyMatrixResult,
) -> ModelAssistedAnalysisProcessingError:
    if result.validation_outcome == "rejected_routing" and result.error_code in _ROUTING_ERROR_CODES:
        return ModelAssistedAnalysisProcessingError(
            result.error_code or "model_routing_denied",
            error_code=result.error_code or "model_routing_denied",
            policy_blocked=True,
        )
    if result.validation_outcome == "model_timeout":
        return ModelAssistedAnalysisProcessingError(
            "model request timed out",
            error_code="model_timeout",
            retryable=True,
        )
    if result.validation_outcome == "model_call_failed":
        return ModelAssistedAnalysisProcessingError(
            "model request failed",
            error_code=result.error_code or "model_call_failed",
            retryable=result.retryable,
        )
    if result.validation_outcome == "rejected_citation":
        return ModelAssistedAnalysisProcessingError(
            "citation validation failed",
            error_code="citation_validation_failed",
        )
    if result.validation_outcome == "rejected_status_ceiling":
        return ModelAssistedAnalysisProcessingError(
            "status ceiling violated",
            error_code="status_ceiling_violated",
        )
    if result.validation_outcome == "rejected_coverage":
        return ModelAssistedAnalysisProcessingError(
            "matrix coverage invalid",
            error_code="matrix_coverage_invalid",
        )
    if result.validation_outcome in {"repair_exhausted", "rejected_parse"}:
        return ModelAssistedAnalysisProcessingError(
            "model response schema invalid",
            error_code="model_response_schema_invalid",
        )
    if result.validation_outcome == "rejected_context_limit":
        return ModelAssistedAnalysisProcessingError(
            "context limit exceeded",
            error_code="context_limit_exceeded",
        )
    return ModelAssistedAnalysisProcessingError(
        result.error_code or "model_response_schema_invalid",
        error_code=result.error_code or "model_response_schema_invalid",
    )


async def process_next_model_assisted_analysis(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    package_revision: PackageRevision,
    analysis_run: AnalysisRun,
    sealed: SealedPackageContent,
    storage_root: Path,
    project_root: Path,
    config: RuntimeConfig,
    hmac_key: bytes,
    now: datetime,
    text_client: TextModelClient | None = None,
) -> ModelAssistedAnalysisResult:
    """Execute one sufficiency_matrix step using sealed content and routed model calls."""
    if claimed.job.step_key != TARGETED_STEP_KEY:
        raise ModelAssistedAnalysisProcessingError(
            "unexpected job step_key",
            error_code="reconciliation_required",
        )
    if analysis_run.run_type not in {"targeted", "full"}:
        raise ModelAssistedAnalysisProcessingError(
            "model-assisted worker only supports targeted and full runs",
            error_code="prohibited_model_action",
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

    result = await _execute_sufficiency_matrix(
        analysis_run=analysis_run,
        package_revision=package_revision,
        sealed=sealed,
        config=config,
        project_root=project_root,
        text_client=text_client,
    )
    if result.validation_outcome != "accepted":
        exc = _map_runner_failure(result)
        if exc.policy_blocked:
            transition_run_to_policy_blocked(
                analysis_run,
                now=now,
                error_code=exc.error_code,
            )
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
                action="analysis_run.policy_blocked",
                object_type="analysis_run",
                object_id=format_uuid(analysis_run.run_id),
                outcome="denied",
                reason_code=exc.error_code,
                metadata={
                    "run_type": analysis_run.run_type,
                    "llm_call_count": 0,
                },
                occurred_at=now,
            )
            raise exc
        raise exc

    row_payloads = list(result.row_payloads)
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

    contract = prompt_contract_metadata()
    document = config.document
    context_tokens, max_output_tokens, timeout_seconds = _runtime_limits(config)
    endpoint_profile = resolve_text_model_endpoint_profile(config)
    last_call = result.model_calls[-1] if result.model_calls else None
    session.add(
        RunStep(
            step_id=uuid.uuid4(),
            run_id=analysis_run.run_id,
            step_key=TARGETED_STEP_KEY,
            step_type=TARGETED_STEP_TYPE,
            schema_id=RESPONSE_SCHEMA_ID,
            prompt_version=contract["prompt_version"],
            prompt_sha256=contract["prompt_sha256"],
            fact_bundle_sha256=result.fact_bundle_sha256
            or _fact_bundle_sha256(
                package_revision_id=package_revision.package_revision_id,
                content_sha256=sealed.content_sha256,
            ),
            endpoint_profile=endpoint_profile.value,
            endpoint_host=_resolve_endpoint_host(config),
            model_requested=_model_requested(config),
            model_reported=last_call.model_reported if last_call else _model_requested(config),
            temperature=float(document.get("TEXT_MODEL_TEMPERATURE", 0.0)),
            input_limit=context_tokens,
            output_limit=max_output_tokens,
            timeout_seconds=int(timeout_seconds),
            attempt=claimed.attempt.attempt_number,
            provider_request_id=(
                last_call.provider_request_id if last_call else f"deterministic-{analysis_run.run_id}"
            ),
            input_tokens=last_call.input_tokens if last_call else 0,
            output_tokens=last_call.output_tokens if last_call else 0,
            latency_ms=last_call.latency_ms if last_call and last_call.latency_ms is not None else 0,
            response_sha256=result.response_sha256 or _response_sha256(rows=row_payloads),
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
    analysis_run.llm_call_count = result.llm_call_count
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
            "llm_call_count": result.llm_call_count,
            "sealed_content_sha256": sealed.content_sha256,
        },
        occurred_at=now,
    )

    return ModelAssistedAnalysisResult(
        run_id=analysis_run.run_id,
        package_revision_id=package_revision.package_revision_id,
        matrix_row_count=len(row_payloads),
        artifact_manifest_sha256=stored_manifest.sha256,
        llm_call_count=result.llm_call_count,
    )
