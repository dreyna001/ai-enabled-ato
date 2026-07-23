"""End-to-end workflow helpers for PostgreSQL integration tests."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ato_service.analysis_profile import expected_assessment_item_ids, load_runtime_profile
from ato_service.fisma_control_inventory import load_fisma_control_inventory
from ato_service.fisma_profile import compile_fisma_agency_security_profile
from ato_service.analysis_runs import StartRunInput, start_run
from ato_service.auth_context import AuthorizationDeniedError
from ato_service.concurrency import format_package_revision_etag
from ato_service.db.models import (
    EvidenceRequest,
    ExportRecord,
    MatrixRow,
    PackageRevision,
    PackageRevisionSearchIndex,
    PoamCandidate,
    SealedPackageContent,
)
from ato_service.deterministic_analyzer_worker import process_next_deterministic_analysis_job
from ato_service.export_service import (
    SelfApprovalDeniedError,
    approve_export,
    create_export_draft,
    deliver_export_download,
    submit_export_draft,
)
from ato_service.matrix_coverage import require_exact_matrix_coverage
from ato_service.object_authorization import authorize_package_revision_read
from ato_service.package_revisions import (
    PackageRevisionNotFoundError,
    confirm_package_revision,
    create_package_revision,
    finalize_package_revision,
)
from ato_service.package_search_index import search_revision_chunks
from ato_service.review_revisions import (
    create_review_comment,
    create_review_revision,
    submit_review_revision,
    update_disposition,
)
from ato_service.source_artifacts import upload_source_artifact
from ato_service.synthetic_intake import (
    process_next_synthetic_extraction,
    process_next_synthetic_scan,
)
from ato_service.systems import create_system
from tests.integration_support.factories import (
    APPROVER,
    ASSESSOR,
    OUTSIDER,
    OWNER,
    REVIEWER,
    minimal_synthetic_manifest,
    profile_revision_input,
    system_create_kwargs,
)
from tests.integration_support.postgres import AUTHORITY_MANIFEST_ID, PostgresIntegrationHarness

FISMA_INVENTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "contracts"
    / "fixtures"
    / "fisma-control-inventory.valid.example.json"
)
FISMA_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "contracts" / "authority-manifest.json"
)
FISMA_PROFILE_GENERATED_AT = datetime(2026, 7, 10, 22, 33, 12, tzinfo=timezone.utc)


def ensure_fisma_runtime_profile(harness: PostgresIntegrationHarness) -> None:
    """Compile one digest-pinned FISMA profile into the harness dev_local config."""
    if harness.config.document.get("FISMA_ANALYSIS_PROFILE_FILE_REFERENCE") is not None:
        return

    inventory = load_fisma_control_inventory(path=FISMA_INVENTORY_PATH)
    profile = compile_fisma_agency_security_profile(
        inventory=inventory,
        manifest_path=FISMA_MANIFEST_PATH,
        project_root=harness.project_root,
        generated_at=FISMA_PROFILE_GENERATED_AT,
    )
    profile_bytes = json.dumps(profile).encode("utf-8")
    digest = hashlib.sha256(profile_bytes).hexdigest()
    profile_file = harness.tmp_path / "fisma-analysis-profile.json"
    profile_file.write_bytes(profile_bytes)
    harness.config.document["FISMA_ANALYSIS_PROFILE_FILE_REFERENCE"] = {
        "path": str(profile_file),
        "expected_sha256": digest,
    }


def _prepare_profile_runtime(
    harness: PostgresIntegrationHarness,
    *,
    profile_id: str,
) -> None:
    if profile_id == "fisma_agency_security":
        ensure_fisma_runtime_profile(harness)


@dataclass(slots=True)
class WorkflowArtifacts:
    system_id: uuid.UUID
    package_revision_id: uuid.UUID
    run_id: uuid.UUID
    review_revision_id: uuid.UUID
    export_draft_id: uuid.UUID
    approval_id: uuid.UUID
    profile_id: str
    zip_sha256: str
    zip_bytes: bytes


async def run_profile_workflow(
    harness: PostgresIntegrationHarness,
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> WorkflowArtifacts:
    """Execute the bounded synthetic workflow through exact ZIP/hash download."""
    _prepare_profile_runtime(harness, profile_id=profile_id)
    session = harness.session
    revision_input = profile_revision_input(
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
    )

    system_result = await create_system(
        session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        idempotency_key=f"system-{profile_id}",
        now=harness.now,
        **system_create_kwargs(display_name=f"E2E {profile_id}"),
    )
    system_id = uuid.UUID(system_result.payload["system_id"])

    revision_result = await create_package_revision(
        session,
        principal=OWNER,
        system_id=system_id,
        request=revision_input,
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"revision-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    package_revision_id = uuid.UUID(revision_result.payload["package_revision_id"])

    await upload_source_artifact(
        session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        blob_store=harness.blob_store,
        limits=harness.config.limits,
        package_revision_id=package_revision_id,
        idempotency_key=f"upload-{profile_id}",
        source=io.BytesIO(minimal_synthetic_manifest(profile_id)),
        display_filename=f"{profile_id}-synthetic.json",
        declared_media_type="application/json",
        artifact_kind="manifest",
        source_date=None,
        now=harness.now,
    )

    await finalize_package_revision(
        session,
        principal=OWNER,
        package_revision_id=package_revision_id,
        idempotency_key=f"finalize-{profile_id}",
        hmac_key=harness.hmac_key,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        limits=harness.config.limits,
        now=harness.now,
    )
    assert await process_next_synthetic_scan(
        session,
        hmac_key=harness.hmac_key,
        now=harness.now,
    ) is not None
    assert await process_next_synthetic_extraction(
        session,
        blob_store=harness.blob_store,
        hmac_key=harness.hmac_key,
        now=harness.now,
    ) is not None

    revision = await session.get(PackageRevision, package_revision_id)
    assert revision is not None
    assert revision.status == "awaiting_confirmation"
    etag = format_package_revision_etag(revision.revision_version)
    await confirm_package_revision(
        session,
        principal=OWNER,
        package_revision_id=package_revision_id,
        if_match=etag,
        idempotency_key=f"confirm-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
        config=harness.config,
        blob_store=harness.blob_store,
    )

    sealed = await session.scalar(
        select(SealedPackageContent).where(
            SealedPackageContent.package_revision_id == package_revision_id
        )
    )
    assert sealed is not None
    assert sealed.field_provenance is not None

    search_count = await session.scalar(
        select(func.count(PackageRevisionSearchIndex.package_revision_id)).where(
            PackageRevisionSearchIndex.package_revision_id == package_revision_id
        )
    )
    assert int(search_count or 0) >= 1
    search_page = await search_revision_chunks(
        session,
        package_revision_id=package_revision_id,
        query="policy",
        limit=5,
    )
    assert search_page.total_count >= 0

    started = await start_run(
        session,
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
        idempotency_key=f"run-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    run_id = uuid.UUID(started.payload["run_id"])
    assert await process_next_deterministic_analysis_job(
        session,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        hmac_key=harness.hmac_key,
        lease_owner="integration-worker",
        now=harness.now,
        config=harness.config,
    ) is not None

    profile = load_runtime_profile(
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
        project_root=harness.project_root,
        config=harness.config,
    )
    expected_ids = expected_assessment_item_ids(profile)
    row_ids = list(
        (
            await session.execute(select(MatrixRow.assessment_item_id).where(MatrixRow.run_id == run_id))
        ).scalars()
    )
    require_exact_matrix_coverage(expected_ids, row_ids)

    review = await create_review_revision(
        session,
        principal=REVIEWER,
        run_id=run_id,
        idempotency_key=f"review-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    review_revision_id = uuid.UUID(review.payload["review_revision_id"])
    matrix_rows = list(
        (await session.execute(select(MatrixRow).where(MatrixRow.run_id == run_id))).scalars()
    )
    assert matrix_rows
    matrix_rows[0].system_status = "partial"
    matrix_rows[0].model_proposed_status = "partial"
    await session.flush()

    review_etag = review.etag
    _, review_etag = await update_disposition(
        session,
        principal=REVIEWER,
        review_revision_id=review_revision_id,
        matrix_row_id=matrix_rows[0].matrix_row_id,
        decision="weakness_confirmed",
        edited_summary=None,
        notes="weakness confirmed in integration test",
        if_match=review_etag,
        hmac_key=harness.hmac_key,
        now=harness.now,
    )

    if len(matrix_rows) > 1:
        _, review_etag = await update_disposition(
            session,
            principal=REVIEWER,
            review_revision_id=review_revision_id,
            matrix_row_id=matrix_rows[1].matrix_row_id,
            decision="evidence_requested",
            edited_summary=None,
            notes="evidence requested in integration test",
            if_match=review_etag,
            hmac_key=harness.hmac_key,
            now=harness.now,
        )
        remaining_rows = matrix_rows[2:]
    else:
        remaining_rows = []

    for matrix_row in remaining_rows:
        _, review_etag = await update_disposition(
            session,
            principal=REVIEWER,
            review_revision_id=review_revision_id,
            matrix_row_id=matrix_row.matrix_row_id,
            decision="accepted",
            edited_summary=None,
            notes="accepted",
            if_match=review_etag,
            hmac_key=harness.hmac_key,
            now=harness.now,
        )

    weakness_row = matrix_rows[0]

    await create_review_comment(
        session,
        principal=REVIEWER,
        review_revision_id=review_revision_id,
        matrix_row_id=weakness_row.matrix_row_id,
        body="Integration review comment",
        idempotency_key=f"comment-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )

    poam_count = await session.scalar(select(func.count(PoamCandidate.poam_candidate_id)))
    assert int(poam_count or 0) >= 1
    if len(matrix_rows) > 1:
        evidence_count = await session.scalar(
            select(func.count(EvidenceRequest.evidence_request_id))
        )
        assert int(evidence_count or 0) >= 1

    submitted = await submit_review_revision(
        session,
        principal=REVIEWER,
        review_revision_id=review_revision_id,
        if_match=review_etag,
        idempotency_key=f"submit-review-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    assert submitted.payload["status"] == "submitted"

    export_draft = await create_export_draft(
        session,
        principal=REVIEWER,
        review_revision_id=review_revision_id,
        project_root=harness.project_root,
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"export-draft-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    export_draft_id = uuid.UUID(export_draft.payload["export_draft_id"])

    approval = await submit_export_draft(
        session,
        principal=REVIEWER,
        export_draft_id=export_draft_id,
        if_match='"v1"',
        idempotency_key=f"export-submit-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    approval_id = uuid.UUID(approval.payload["approval_id"])

    with pytest.raises(SelfApprovalDeniedError):
        await approve_export(
            session,
            principal=REVIEWER,
            approval_id=approval_id,
            idempotency_key=f"self-approve-{profile_id}",
            hmac_key=harness.hmac_key,
            now=harness.now,
            project_root=harness.project_root,
            authority_manifest_id=AUTHORITY_MANIFEST_ID,
        )

    await approve_export(
        session,
        principal=APPROVER,
        approval_id=approval_id,
        idempotency_key=f"approve-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
        project_root=harness.project_root,
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
    )

    download = await deliver_export_download(
        session,
        principal=OWNER,
        export_id=approval_id,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"download-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    zip_sha256 = hashlib.sha256(download.zip_bytes).hexdigest()
    assert zipfile.is_zipfile(io.BytesIO(download.zip_bytes))

    replay = await deliver_export_download(
        session,
        principal=OWNER,
        export_id=approval_id,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"download-{profile_id}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    assert replay.replayed is True
    assert hashlib.sha256(replay.zip_bytes).hexdigest() == zip_sha256

    export_record = await session.scalar(
        select(ExportRecord).where(ExportRecord.approval_id == approval_id)
    )
    assert export_record is not None

    return WorkflowArtifacts(
        system_id=system_id,
        package_revision_id=package_revision_id,
        run_id=run_id,
        review_revision_id=review_revision_id,
        export_draft_id=export_draft_id,
        approval_id=approval_id,
        profile_id=profile_id,
        zip_sha256=zip_sha256,
        zip_bytes=download.zip_bytes,
    )


async def assert_tenant_isolation(
    harness: PostgresIntegrationHarness,
    *,
    artifacts: WorkflowArtifacts,
) -> None:
    """Deny cross-tenant reads without leaking owner groups."""
    with pytest.raises(AuthorizationDeniedError) as exc_info:
        await authorize_package_revision_read(
            harness.session,
            principal=OUTSIDER,
            package_revision_id=artifacts.package_revision_id,
            not_found_error=PackageRevisionNotFoundError,
        )
    message = str(exc_info.value)
    assert "owners" not in message
    assert exc_info.value.error_code == "authorization_denied"


async def seed_ready_revision(
    harness: PostgresIntegrationHarness,
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    id_suffix: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a ready package revision for recovery-focused tests."""
    _prepare_profile_runtime(harness, profile_id=profile_id)
    system_result = await create_system(
        harness.session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        idempotency_key=f"seed-system-{id_suffix}",
        now=harness.now,
        **system_create_kwargs(display_name=f"Seed {id_suffix}"),
    )
    system_id = uuid.UUID(system_result.payload["system_id"])
    revision_result = await create_package_revision(
        harness.session,
        principal=OWNER,
        system_id=system_id,
        request=profile_revision_input(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
        ),
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"seed-revision-{id_suffix}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    package_revision_id = uuid.UUID(revision_result.payload["package_revision_id"])
    await upload_source_artifact(
        harness.session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        blob_store=harness.blob_store,
        limits=harness.config.limits,
        package_revision_id=package_revision_id,
        idempotency_key=f"seed-upload-{id_suffix}",
        source=io.BytesIO(minimal_synthetic_manifest(profile_id)),
        display_filename="seed.json",
        declared_media_type="application/json",
        artifact_kind="manifest",
        source_date=None,
        now=harness.now,
    )
    await finalize_package_revision(
        harness.session,
        principal=OWNER,
        package_revision_id=package_revision_id,
        idempotency_key=f"seed-finalize-{id_suffix}",
        hmac_key=harness.hmac_key,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        limits=harness.config.limits,
        now=harness.now,
    )
    await process_next_synthetic_scan(
        harness.session,
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    await process_next_synthetic_extraction(
        harness.session,
        blob_store=harness.blob_store,
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    revision = await harness.session.get(PackageRevision, package_revision_id)
    assert revision is not None
    await confirm_package_revision(
        harness.session,
        principal=OWNER,
        package_revision_id=package_revision_id,
        if_match=format_package_revision_etag(revision.revision_version),
        idempotency_key=f"seed-confirm-{id_suffix}",
        hmac_key=harness.hmac_key,
        now=harness.now,
        config=harness.config,
        blob_store=harness.blob_store,
    )
    return system_id, package_revision_id


async def seed_pre_confirm(
    harness: PostgresIntegrationHarness,
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
    id_suffix: str,
) -> uuid.UUID:
    """Create a synthetic revision stopped at awaiting_confirmation."""
    _prepare_profile_runtime(harness, profile_id=profile_id)
    system_result = await create_system(
        harness.session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        idempotency_key=f"pre-system-{id_suffix}",
        now=harness.now,
        **system_create_kwargs(display_name=f"Pre-confirm {id_suffix}"),
    )
    system_id = uuid.UUID(system_result.payload["system_id"])
    revision_result = await create_package_revision(
        harness.session,
        principal=OWNER,
        system_id=system_id,
        request=profile_revision_input(
            profile_id=profile_id,
            certification_class=certification_class,
            impact_level=impact_level,
        ),
        authority_manifest_id=AUTHORITY_MANIFEST_ID,
        idempotency_key=f"pre-revision-{id_suffix}",
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    package_revision_id = uuid.UUID(revision_result.payload["package_revision_id"])
    await upload_source_artifact(
        harness.session,
        principal=OWNER,
        audit_hmac_key=harness.hmac_key,
        blob_store=harness.blob_store,
        limits=harness.config.limits,
        package_revision_id=package_revision_id,
        idempotency_key=f"pre-upload-{id_suffix}",
        source=io.BytesIO(minimal_synthetic_manifest(profile_id)),
        display_filename="pre.json",
        declared_media_type="application/json",
        artifact_kind="manifest",
        source_date=None,
        now=harness.now,
    )
    await finalize_package_revision(
        harness.session,
        principal=OWNER,
        package_revision_id=package_revision_id,
        idempotency_key=f"pre-finalize-{id_suffix}",
        hmac_key=harness.hmac_key,
        storage_root=harness.storage_root,
        project_root=harness.project_root,
        limits=harness.config.limits,
        now=harness.now,
    )
    await process_next_synthetic_scan(
        harness.session,
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    await process_next_synthetic_extraction(
        harness.session,
        blob_store=harness.blob_store,
        hmac_key=harness.hmac_key,
        now=harness.now,
    )
    revision = await harness.session.get(PackageRevision, package_revision_id)
    assert revision is not None
    assert revision.status == "awaiting_confirmation"
    return package_revision_id
