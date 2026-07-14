"""Optional live-PostgreSQL acceptance test for deterministic analysis runs."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.analysis_profile import expected_assessment_item_ids, load_pinned_fisma_synthetic_profile
from ato_service.analysis_runs import StartRunInput, start_run
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.db.models import AnalysisRun, Job, MatrixRow, PackageRevision, RunStep, System
from ato_service.db.session import create_async_engine_from_url
from ato_service.deterministic_analyzer_worker import process_next_deterministic_analysis_job
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_deterministic_analysis_run_completes_with_exact_matrix(tmp_path: Path) -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        now = datetime.now(timezone.utc)
        system_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        config = load_runtime_config_from_dict(
            {
                "schema_version": "1.0.0",
                "runtime_profile": "dev_local",
                "STORAGE_DATA_PATH": str(tmp_path / "storage"),
            },
            base_dir=tmp_path,
        )
        profile = load_pinned_fisma_synthetic_profile(project_root=ROOT)
        expected_ids = expected_assessment_item_ids(profile)
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=system_id,
                            display_name="Deterministic analysis integration",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=["viewers"],
                            created_at=now,
                            archived_at=None,
                        )
                    )
                    session.add(
                        PackageRevision(
                            package_revision_id=revision_id,
                            system_id=system_id,
                            parent_revision_id=None,
                            profile_id="fisma_agency_security",
                            certification_class=None,
                            impact_level="moderate",
                            data_origin="synthetic",
                            sensitivity="internal_unclassified",
                            effective_data_labels=[
                                "internal_unclassified",
                                "synthetic",
                            ],
                            authority_manifest_id="authority.v2",
                            content_manifest_sha256="a" * 64,
                            revision_version=3,
                            status="ready",
                            created_by="integration@example.test",
                            created_at=now,
                        )
                    )
                    await session.flush()

                    principal = type(
                        "Principal",
                        (),
                        {"actor_id": "integration@example.test", "groups": ("owners",)},
                    )()
                    started = await start_run(
                        session,
                        principal=principal,
                        package_revision_id=revision_id,
                        request=StartRunInput(
                            run_type="deterministic_only",
                            parent_run_id=None,
                            assessment_item_ids=(),
                        ),
                        config=config,
                        authority_manifest_id="authority.v2",
                        project_root=ROOT,
                        idempotency_key="integration-start-run-key1",
                        hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                        now=now,
                    )
                    run_id = uuid.UUID(started.payload["run_id"])
                    await session.flush()

                    completed = await process_next_deterministic_analysis_job(
                        session,
                        storage_root=config.storage_data_path,
                        project_root=ROOT,
                        hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                        lease_owner="integration-worker",
                        now=now,
                        config=config,
                    )
                    assert completed is not None
                    await session.flush()

                    run = await session.get(AnalysisRun, run_id)
                    assert run is not None
                    assert run.status == "succeeded"
                    assert run.llm_call_count == 0
                    assert run.artifact_manifest_sha256 is not None

                    row_ids = list(
                        (
                            await session.execute(
                                select(MatrixRow.assessment_item_id).where(
                                    MatrixRow.run_id == run_id
                                )
                            )
                        ).scalars()
                    )
                    require_exact_matrix_coverage(expected_ids, row_ids)

                    step_count = await session.scalar(
                        select(func.count(RunStep.step_id)).where(RunStep.run_id == run_id)
                    )
                    job_count = await session.scalar(
                        select(func.count(Job.job_id)).where(Job.run_id == run_id)
                    )
                    assert step_count == 1
                    assert job_count == 1
                    assert started.payload["run_type"] == "deterministic_only"
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(exercise())
