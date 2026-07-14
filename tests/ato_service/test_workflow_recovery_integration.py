"""Negative and recovery PostgreSQL workflow integration tests."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from ato_service.analysis_runs import StartRunInput, start_run
from ato_service.auth_context import AuthorizationDeniedError
from ato_service.concurrency import EtagMismatchError, IfMatchRequiredError
from ato_service.db.models import AnalysisRun, Job, MatrixRow, PackageRevision
from ato_service.deterministic_analyzer_worker import process_next_deterministic_analysis_job
from ato_service.export_service import (
    ExportValidationError,
    SelfApprovalDeniedError,
    approve_export,
    create_export_draft,
    process_approval_expiry,
    submit_export_draft,
)
from ato_service.idempotency import IdempotencyConflictError
from ato_service.jobs import recover_expired_leases
from ato_service.object_authorization import authorize_package_revision_read
from ato_service.package_revisions import (
    EmptyPackageRevisionError,
    PackageRevisionNotFoundError,
    confirm_package_revision,
    create_package_revision,
    finalize_package_revision,
)
from ato_service.review_revisions import (
    create_review_revision,
    submit_review_revision,
    update_disposition,
)
from ato_service.systems import create_system
from tests.integration_support.factories import (
    APPROVER,
    ASSESSOR,
    OUTSIDER,
    OWNER,
    REVIEWER,
    profile_revision_input,
    system_create_kwargs,
)
from tests.integration_support.postgres import (
    AUTHORITY_MANIFEST_ID,
    postgres_integration_harness,
    run_async,
)
from tests.integration_support.workflow import (
    run_profile_workflow,
    seed_pre_confirm,
    seed_ready_revision,
)


@pytest.mark.integration
def test_create_system_idempotency_replay_is_identical(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            kwargs = system_create_kwargs(display_name="Idempotent System")
            first = await create_system(
                harness.session,
                principal=OWNER,
                audit_hmac_key=harness.hmac_key,
                idempotency_key="same-system-key",
                now=harness.now,
                **kwargs,
            )
            second = await create_system(
                harness.session,
                principal=OWNER,
                audit_hmac_key=harness.hmac_key,
                idempotency_key="same-system-key",
                now=harness.now,
                **kwargs,
            )
            assert second.replayed is True
            assert first.payload == second.payload

    run_async(exercise())


@pytest.mark.integration
def test_create_system_idempotency_conflict_raises(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            await create_system(
                harness.session,
                principal=OWNER,
                audit_hmac_key=harness.hmac_key,
                idempotency_key="conflict-key",
                now=harness.now,
                **system_create_kwargs(display_name="First"),
            )
            with pytest.raises(IdempotencyConflictError):
                await create_system(
                    harness.session,
                    principal=OWNER,
                    audit_hmac_key=harness.hmac_key,
                    idempotency_key="conflict-key",
                    now=harness.now,
                    **system_create_kwargs(display_name="Second"),
                )

    run_async(exercise())


@pytest.mark.integration
def test_confirm_rejects_stale_etag(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            package_revision_id = await seed_pre_confirm(
                harness,
                profile_id="fisma_agency_security",
                certification_class=None,
                impact_level="moderate",
                id_suffix="stale-etag",
            )
            with pytest.raises(EtagMismatchError):
                await confirm_package_revision(
                    harness.session,
                    principal=OWNER,
                    package_revision_id=package_revision_id,
                    if_match='"v1"',
                    idempotency_key="confirm-stale",
                    hmac_key=harness.hmac_key,
                    now=harness.now,
                    config=harness.config,
                    blob_store=harness.blob_store,
                )

    run_async(exercise())


@pytest.mark.integration
def test_confirm_requires_if_match_header(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            system_result = await create_system(
                harness.session,
                principal=OWNER,
                audit_hmac_key=harness.hmac_key,
                idempotency_key="if-match-system",
                now=harness.now,
                **system_create_kwargs(display_name="If-Match"),
            )
            revision_result = await create_package_revision(
                harness.session,
                principal=OWNER,
                system_id=uuid.UUID(system_result.payload["system_id"]),
                request=profile_revision_input(
                    profile_id="fisma_agency_security",
                    certification_class=None,
                    impact_level="moderate",
                ),
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                idempotency_key="if-match-revision",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            with pytest.raises(IfMatchRequiredError):
                await confirm_package_revision(
                    harness.session,
                    principal=OWNER,
                    package_revision_id=uuid.UUID(
                        revision_result.payload["package_revision_id"]
                    ),
                    if_match=None,
                    idempotency_key="confirm-no-etag",
                    hmac_key=harness.hmac_key,
                    now=harness.now,
                )

    run_async(exercise())


@pytest.mark.integration
def test_expired_job_lease_recovery_completes_without_duplicate_rows(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            _, package_revision_id = await seed_ready_revision(
                harness,
                profile_id="fisma_agency_security",
                certification_class=None,
                impact_level="moderate",
                id_suffix="lease-recovery",
            )
            started = await start_run(
                harness.session,
                principal=ASSESSOR,
                package_revision_id=package_revision_id,
                request=StartRunInput(
                    run_type="deterministic_only",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=harness.config,
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                project_root=harness.project_root,
                idempotency_key="lease-run",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            run_id = uuid.UUID(started.payload["run_id"])

            job = await harness.session.scalar(
                select(Job).where(Job.run_id == run_id)
            )
            assert job is not None
            job.lease_owner = "crashed-worker"
            job.lease_expires_at = harness.now - timedelta(minutes=5)
            job.status = "leased"
            await harness.session.flush()

            recovered = await recover_expired_leases(
                harness.session,
                now=harness.now,
                max_attempts=3,
            )
            assert recovered >= 1

            first = await process_next_deterministic_analysis_job(
                harness.session,
                storage_root=harness.storage_root,
                project_root=harness.project_root,
                hmac_key=harness.hmac_key,
                lease_owner="recovery-worker",
                now=harness.now,
                config=harness.config,
            )
            assert first is not None
            second = await process_next_deterministic_analysis_job(
                harness.session,
                storage_root=harness.storage_root,
                project_root=harness.project_root,
                hmac_key=harness.hmac_key,
                lease_owner="recovery-worker",
                now=harness.now,
                config=harness.config,
            )
            assert second is None

            row_count = await harness.session.scalar(
                select(func.count(MatrixRow.matrix_row_id)).where(MatrixRow.run_id == run_id)
            )
            run = await harness.session.get(AnalysisRun, run_id)
            assert run is not None
            assert run.status == "succeeded"
            assert int(row_count or 0) == len(run.assessment_item_ids)

    run_async(exercise())


@pytest.mark.integration
def test_object_guessing_returns_not_found(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            missing_id = uuid.uuid4()
            with pytest.raises(PackageRevisionNotFoundError):
                await authorize_package_revision_read(
                    harness.session,
                    principal=OUTSIDER,
                    package_revision_id=missing_id,
                    not_found_error=PackageRevisionNotFoundError,
                )

    run_async(exercise())


@pytest.mark.integration
def test_authorization_denied_without_leaking_groups(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            artifacts = await run_profile_workflow(
                harness,
                profile_id="fisma_agency_security",
                certification_class=None,
                impact_level="moderate",
            )
            with pytest.raises(AuthorizationDeniedError) as exc_info:
                await authorize_package_revision_read(
                    harness.session,
                    principal=OUTSIDER,
                    package_revision_id=artifacts.package_revision_id,
                    not_found_error=PackageRevisionNotFoundError,
                )
            assert "owners" not in str(exc_info.value)

    run_async(exercise())


@pytest.mark.integration
def test_approval_expiry_blocks_late_approve(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            _, package_revision_id = await seed_ready_revision(
                harness,
                profile_id="fedramp_20x_program",
                certification_class="C",
                impact_level=None,
                id_suffix="approval-expiry",
            )
            started = await start_run(
                harness.session,
                principal=ASSESSOR,
                package_revision_id=package_revision_id,
                request=StartRunInput(
                    run_type="deterministic_only",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=harness.config,
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                project_root=harness.project_root,
                idempotency_key="approval-expiry-run",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            run_id = uuid.UUID(started.payload["run_id"])
            assert await process_next_deterministic_analysis_job(
                harness.session,
                storage_root=harness.storage_root,
                project_root=harness.project_root,
                hmac_key=harness.hmac_key,
                lease_owner="worker",
                now=harness.now,
                config=harness.config,
            )
            review = await create_review_revision(
                harness.session,
                principal=REVIEWER,
                run_id=run_id,
                idempotency_key="approval-expiry-review",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            review_revision_id = uuid.UUID(review.payload["review_revision_id"])
            matrix_row = await harness.session.scalar(
                select(MatrixRow).where(MatrixRow.run_id == run_id)
            )
            assert matrix_row is not None
            matrix_row.system_status = "partial"
            matrix_row.model_proposed_status = "partial"
            await harness.session.flush()
            _, review_etag = await update_disposition(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                matrix_row_id=matrix_row.matrix_row_id,
                decision="weakness_confirmed",
                edited_summary=None,
                notes="confirmed",
                if_match=review.etag,
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            await submit_review_revision(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                if_match=review_etag,
                idempotency_key="approval-expiry-submit-review",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            export_draft = await create_export_draft(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                project_root=harness.project_root,
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                idempotency_key="approval-expiry-export",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            approval = await submit_export_draft(
                harness.session,
                principal=REVIEWER,
                export_draft_id=uuid.UUID(export_draft.payload["export_draft_id"]),
                if_match='"v1"',
                idempotency_key="approval-expiry-submit-export",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            approval_id = uuid.UUID(approval.payload["approval_id"])
            expired_now = harness.now + timedelta(days=8)
            result = await process_approval_expiry(
                harness.session,
                now=expired_now,
                approval_expiry_days=7,
                hmac_key=harness.hmac_key,
            )
            assert result.pending_expired >= 1

            with pytest.raises(ExportValidationError) as exc_info:
                await approve_export(
                    harness.session,
                    principal=APPROVER,
                    approval_id=approval_id,
                    idempotency_key="late-approve",
                    hmac_key=harness.hmac_key,
                    now=expired_now,
                    project_root=harness.project_root,
                    authority_manifest_id=AUTHORITY_MANIFEST_ID,
                )
            assert exc_info.value.error_code in {
                "approval_expired",
                "illegal_state_transition",
            }

    run_async(exercise())


@pytest.mark.integration
def test_self_approval_denied_on_submit_and_approve(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            _, package_revision_id = await seed_ready_revision(
                harness,
                profile_id="fedramp_rev5_transition",
                certification_class=None,
                impact_level="moderate",
                id_suffix="self-approval",
            )
            started = await start_run(
                harness.session,
                principal=ASSESSOR,
                package_revision_id=package_revision_id,
                request=StartRunInput(
                    run_type="deterministic_only",
                    parent_run_id=None,
                    assessment_item_ids=(),
                ),
                config=harness.config,
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                project_root=harness.project_root,
                idempotency_key="self-approval-run",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            run_id = uuid.UUID(started.payload["run_id"])
            assert await process_next_deterministic_analysis_job(
                harness.session,
                storage_root=harness.storage_root,
                project_root=harness.project_root,
                hmac_key=harness.hmac_key,
                lease_owner="worker",
                now=harness.now,
                config=harness.config,
            )

            review = await create_review_revision(
                harness.session,
                principal=REVIEWER,
                run_id=run_id,
                idempotency_key="self-approval-review",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            review_revision_id = uuid.UUID(review.payload["review_revision_id"])
            matrix_row = await harness.session.scalar(
                select(MatrixRow).where(MatrixRow.run_id == run_id)
            )
            assert matrix_row is not None
            matrix_row.system_status = "partial"
            matrix_row.model_proposed_status = "partial"
            await harness.session.flush()
            _, review_etag = await update_disposition(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                matrix_row_id=matrix_row.matrix_row_id,
                decision="weakness_confirmed",
                edited_summary=None,
                notes="confirmed",
                if_match=review.etag,
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            await submit_review_revision(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                if_match=review_etag,
                idempotency_key="self-approval-submit-review",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            export_draft = await create_export_draft(
                harness.session,
                principal=REVIEWER,
                review_revision_id=review_revision_id,
                project_root=harness.project_root,
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                idempotency_key="self-approval-export",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            approval = await submit_export_draft(
                harness.session,
                principal=REVIEWER,
                export_draft_id=uuid.UUID(export_draft.payload["export_draft_id"]),
                if_match='"v1"',
                idempotency_key="self-approval-submit-export",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            with pytest.raises(SelfApprovalDeniedError):
                await approve_export(
                    harness.session,
                    principal=REVIEWER,
                    approval_id=uuid.UUID(approval.payload["approval_id"]),
                    idempotency_key="self-approval-approve",
                    hmac_key=harness.hmac_key,
                    now=harness.now,
                    project_root=harness.project_root,
                    authority_manifest_id=AUTHORITY_MANIFEST_ID,
                )

    run_async(exercise())


@pytest.mark.integration
def test_finalize_without_artifacts_preserves_uploading_state(tmp_path) -> None:
    async def exercise() -> None:
        async with postgres_integration_harness(tmp_path) as harness:
            system_result = await create_system(
                harness.session,
                principal=OWNER,
                audit_hmac_key=harness.hmac_key,
                idempotency_key="partial-system",
                now=harness.now,
                **system_create_kwargs(display_name="Partial failure"),
            )
            revision_result = await create_package_revision(
                harness.session,
                principal=OWNER,
                system_id=uuid.UUID(system_result.payload["system_id"]),
                request=profile_revision_input(
                    profile_id="fisma_agency_security",
                    certification_class=None,
                    impact_level="moderate",
                ),
                authority_manifest_id=AUTHORITY_MANIFEST_ID,
                idempotency_key="partial-revision",
                hmac_key=harness.hmac_key,
                now=harness.now,
            )
            package_revision_id = uuid.UUID(
                revision_result.payload["package_revision_id"]
            )
            with pytest.raises(EmptyPackageRevisionError):
                await finalize_package_revision(
                    harness.session,
                    principal=OWNER,
                    package_revision_id=package_revision_id,
                    idempotency_key="partial-finalize",
                    hmac_key=harness.hmac_key,
                    storage_root=harness.storage_root,
                    project_root=harness.project_root,
                    limits=harness.config.limits,
                    now=harness.now,
                )
            revision = await harness.session.get(PackageRevision, package_revision_id)
            assert revision is not None
            assert revision.status == "uploading"

    run_async(exercise())
