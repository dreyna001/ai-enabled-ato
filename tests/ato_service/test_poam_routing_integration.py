"""Integration tests for POA&M routing persistence and replay safety."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.db.models import (
    AnalysisRun,
    Disposition,
    EvidenceRequest,
    MatrixRow,
    PackageRevision,
    PoamCandidate,
    ReviewRevision,
    System,
)
from ato_service.db.session import create_async_engine_from_url
from ato_service.review_revisions import update_disposition

HMAC_KEY = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
RUN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REVIEW_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ROW_INSUFFICIENT = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ROW_PARTIAL = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
SYSTEM_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
REVISION_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id="reviewer@example.test",
        groups=("owners",),
        csrf_token="c" * 32,
        allowed_origins=("https://portal.example",),
    )


@pytest.mark.integration
def test_weakness_confirmed_creates_single_poam_candidate_on_replay() -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=SYSTEM_ID,
                            display_name="POA&M routing",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=["owners"],
                            created_at=NOW,
                            archived_at=None,
                        )
                    )
                    session.add(
                        PackageRevision(
                            package_revision_id=REVISION_ID,
                            system_id=SYSTEM_ID,
                            parent_revision_id=None,
                            profile_id="fisma_agency_security",
                            certification_class=None,
                            impact_level="moderate",
                            data_origin="synthetic",
                            sensitivity="internal_unclassified",
                            effective_data_labels=["internal_unclassified", "synthetic"],
                            authority_manifest_id="authority.v2",
                            content_manifest_sha256="a" * 64,
                            revision_version=1,
                            status="ready",
                            created_by="operator",
                            created_at=NOW,
                        )
                    )
                    session.add(
                        AnalysisRun(
                            run_id=RUN_ID,
                            package_revision_id=REVISION_ID,
                            parent_run_id=None,
                            run_type="deterministic_only",
                            status="succeeded",
                            authority_manifest_id="authority.v2",
                            analysis_profile_sha256="b" * 64,
                            config_fingerprint="c" * 64,
                            prompt_bundle_sha256="d" * 64,
                            assessment_item_ids=["AC-1"],
                            created_by="operator",
                            created_at=NOW,
                            started_at=NOW,
                            completed_at=NOW,
                            llm_call_count=0,
                            error_code=None,
                            error_retryable=None,
                            artifact_manifest_sha256="e" * 64,
                        )
                    )
                    session.add(
                        MatrixRow(
                            matrix_row_id=ROW_PARTIAL,
                            run_id=RUN_ID,
                            assessment_item_type="nist_control",
                            assessment_item_id="AC-1",
                            model_proposed_status="partial",
                            system_status="partial",
                            finding_summary="Gap remains",
                            gaps=["missing evidence"],
                            assessor_questions=[],
                            citations=[],
                            context_complete=False,
                            producing_run_id=RUN_ID,
                            source_run_id=RUN_ID,
                        )
                    )
                    session.add(
                        ReviewRevision(
                            review_revision_id=REVIEW_ID,
                            run_id=RUN_ID,
                            version=1,
                            status="draft",
                            created_by="reviewer@example.test",
                            created_at=NOW,
                        )
                    )
                    session.add(
                        Disposition(
                            disposition_id=uuid.uuid4(),
                            review_revision_id=REVIEW_ID,
                            matrix_row_id=ROW_PARTIAL,
                            decision="pending",
                            edited_summary=None,
                            notes=None,
                            version=1,
                            decided_by="reviewer@example.test",
                            decided_at=NOW,
                        )
                    )
                    await session.flush()

                    payload1, _ = await update_disposition(
                        session,
                        principal=_principal(),
                        review_revision_id=REVIEW_ID,
                        matrix_row_id=ROW_PARTIAL,
                        decision="weakness_confirmed",
                        edited_summary=None,
                        notes="confirmed",
                        if_match='"v1"',
                        hmac_key=HMAC_KEY,
                        now=NOW,
                    )
                    payload2, _ = await update_disposition(
                        session,
                        principal=_principal(),
                        review_revision_id=REVIEW_ID,
                        matrix_row_id=ROW_PARTIAL,
                        decision="weakness_confirmed",
                        edited_summary=None,
                        notes="confirmed again",
                        if_match='"v2"',
                        hmac_key=HMAC_KEY,
                        now=NOW,
                    )

                    result = await session.execute(select(PoamCandidate))
                    candidates = result.scalars().all()
                    assert len(candidates) == 1
                    assert payload1["poam_candidate_id"] == payload2["poam_candidate_id"]
                finally:
                    await transaction.rollback()
                    await session.close()
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(exercise())


@pytest.mark.integration
def test_insufficient_evidence_routes_to_evidence_request_only() -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=SYSTEM_ID,
                            display_name="Evidence routing",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=["owners"],
                            created_at=NOW,
                            archived_at=None,
                        )
                    )
                    session.add(
                        PackageRevision(
                            package_revision_id=REVISION_ID,
                            system_id=SYSTEM_ID,
                            parent_revision_id=None,
                            profile_id="fisma_agency_security",
                            certification_class=None,
                            impact_level="moderate",
                            data_origin="synthetic",
                            sensitivity="internal_unclassified",
                            effective_data_labels=["internal_unclassified", "synthetic"],
                            authority_manifest_id="authority.v2",
                            content_manifest_sha256="a" * 64,
                            revision_version=1,
                            status="ready",
                            created_by="operator",
                            created_at=NOW,
                        )
                    )
                    session.add(
                        AnalysisRun(
                            run_id=RUN_ID,
                            package_revision_id=REVISION_ID,
                            parent_run_id=None,
                            run_type="deterministic_only",
                            status="succeeded",
                            authority_manifest_id="authority.v2",
                            analysis_profile_sha256="b" * 64,
                            config_fingerprint="c" * 64,
                            prompt_bundle_sha256="d" * 64,
                            assessment_item_ids=["AC-2"],
                            created_by="operator",
                            created_at=NOW,
                            started_at=NOW,
                            completed_at=NOW,
                            llm_call_count=0,
                            error_code=None,
                            error_retryable=None,
                            artifact_manifest_sha256="e" * 64,
                        )
                    )
                    session.add(
                        MatrixRow(
                            matrix_row_id=ROW_INSUFFICIENT,
                            run_id=RUN_ID,
                            assessment_item_type="nist_control",
                            assessment_item_id="AC-2",
                            model_proposed_status="insufficient_evidence",
                            system_status="insufficient_evidence",
                            finding_summary="No evidence",
                            gaps=["missing"],
                            assessor_questions=[],
                            citations=[],
                            context_complete=False,
                            producing_run_id=RUN_ID,
                            source_run_id=RUN_ID,
                        )
                    )
                    session.add(
                        ReviewRevision(
                            review_revision_id=REVIEW_ID,
                            run_id=RUN_ID,
                            version=1,
                            status="draft",
                            created_by="reviewer@example.test",
                            created_at=NOW,
                        )
                    )
                    session.add(
                        Disposition(
                            disposition_id=uuid.uuid4(),
                            review_revision_id=REVIEW_ID,
                            matrix_row_id=ROW_INSUFFICIENT,
                            decision="pending",
                            edited_summary=None,
                            notes=None,
                            version=1,
                            decided_by="reviewer@example.test",
                            decided_at=NOW,
                        )
                    )
                    await session.flush()

                    await update_disposition(
                        session,
                        principal=_principal(),
                        review_revision_id=REVIEW_ID,
                        matrix_row_id=ROW_INSUFFICIENT,
                        decision="evidence_requested",
                        edited_summary=None,
                        notes="need docs",
                        if_match='"v1"',
                        hmac_key=HMAC_KEY,
                        now=NOW,
                    )

                    evidence_result = await session.execute(select(EvidenceRequest))
                    evidence_rows = evidence_result.scalars().all()
                    poam_result = await session.execute(select(PoamCandidate))
                    assert len(evidence_rows) == 1
                    assert poam_result.scalars().all() == []
                finally:
                    await transaction.rollback()
                    await session.close()
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(exercise())
