"""Tests for deterministic analyzer matrix generation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ato_service.analysis_profile import expected_assessment_item_ids, load_pinned_fisma_synthetic_profile
from ato_service.db.models import AnalysisRun, Job, PackageRevision
from ato_service.deterministic_analyzer import (
    DeterministicAnalysisProcessingError,
    _build_matrix_rows,
    process_next_deterministic_analysis,
    require_deterministic_analyzer_runtime,
)
from ato_service.jobs import ClaimedJob, JobAttempt
from ato_service.runtime_config import load_runtime_config_from_dict
from tests.ato_service.test_analysis_profile import fisma_runtime_config, write_digest_pinned_fisma_profile

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REVISION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ATTEMPT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
RUNTIME_AUTHORITY_MANIFEST_ID = "ato-authorities-2026-07-10-draft"


def test_build_matrix_rows_covers_all_profile_items_with_zero_llm_status() -> None:
    profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
    expected_ids = expected_assessment_item_ids(profile)
    rows = _build_matrix_rows(
        run_id=RUN_ID,
        profile=profile,
        assessment_item_ids=expected_ids,
    )

    assert len(rows) == len(expected_ids)
    assert {row["assessment_item_id"] for row in rows} == set(expected_ids)
    for row in rows:
        assert row["model_proposed_status"] == "insufficient_evidence"
        assert row["system_status"] == "insufficient_evidence"
        assert row["citations"] == []
        assert row["producing_run_id"] == str(RUN_ID).lower()


def test_require_deterministic_analyzer_runtime_rejects_non_dev_local(tmp_path: Path) -> None:
    from ato_service.deterministic_analyzer import DeterministicAnalyzerRuntimeError
    from ato_service.runtime_config import RuntimeConfigValidationError

    with pytest.raises((DeterministicAnalyzerRuntimeError, RuntimeConfigValidationError)):
        require_deterministic_analyzer_runtime(
            load_runtime_config_from_dict(
                {
                    "schema_version": "1.0.0",
                    "runtime_profile": "onprem",
                    "STORAGE_DATA_PATH": str(tmp_path / "storage"),
                },
                base_dir=tmp_path,
            )
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


def _package_revision(*, impact_level: str | None) -> PackageRevision:
    return PackageRevision(
        package_revision_id=REVISION_ID,
        system_id=uuid.uuid4(),
        parent_revision_id=None,
        profile_id="fisma_agency_security",
        certification_class=None,
        impact_level=impact_level,
        data_origin="synthetic",
        sensitivity="internal_unclassified",
        effective_data_labels=["internal_unclassified", "synthetic"],
        authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
        content_manifest_sha256="a" * 64,
        package_content_sha256="b" * 64,
        revision_version=1,
        status="ready",
        created_by="tester",
        created_at=NOW,
    )


def _analysis_run(*, profile_digest: str) -> AnalysisRun:
    return AnalysisRun(
        run_id=RUN_ID,
        package_revision_id=REVISION_ID,
        parent_run_id=None,
        run_type="deterministic_only",
        status="running",
        requested_by="tester",
        requested_at=NOW,
        started_at=NOW,
        completed_at=None,
        authority_manifest_id=RUNTIME_AUTHORITY_MANIFEST_ID,
        analysis_profile_sha256=profile_digest,
        config_fingerprint="c" * 64,
        prompt_bundle_sha256="d" * 64,
        model_profile="deterministic",
        artifact_manifest_sha256=None,
        llm_call_count=0,
        assessment_item_ids=(),
        error_code=None,
        error_retryable=None,
    )


def test_process_next_deterministic_analysis_rejects_profile_manifest_mismatch(
    tmp_path: Path,
) -> None:
    profile_file, digest, profile, impact_level = write_digest_pinned_fisma_profile(tmp_path)
    config = fisma_runtime_config(
        tmp_path,
        profile_path=profile_file,
        expected_sha256=digest,
    )
    mismatched_profile = dict(profile)
    mismatched_profile["authority_manifest_id"] = "wrong.manifest"
    session = AsyncMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "ato_service.deterministic_analyzer.load_runtime_profile",
            lambda **_kwargs: mismatched_profile,
        )
        with pytest.raises(DeterministicAnalysisProcessingError) as exc_info:
            asyncio.run(
                process_next_deterministic_analysis(
                    session,
                    claimed=_claimed(),
                    package_revision=_package_revision(impact_level=impact_level),
                    analysis_run=_analysis_run(profile_digest=digest),
                    storage_root=tmp_path / "storage",
                    project_root=ROOT,
                    config=config,
                    hmac_key=b"x" * 32,
                    now=NOW,
                )
            )

    assert exc_info.value.error_code == "reconciliation_required"
