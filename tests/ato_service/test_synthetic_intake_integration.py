"""Optional live-PostgreSQL acceptance test for synthetic intake persistence."""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.blobs import BlobStore
from ato_service.db.models import (
    AuditEvent,
    FactProposal,
    PackageRevision,
    SourceArtifact,
    System,
)
from ato_service.db.session import create_async_engine_from_url
from ato_service.synthetic_intake import (
    process_next_synthetic_extraction,
    process_next_synthetic_scan,
)


@pytest.mark.integration
def test_synthetic_intake_persists_proposals_and_audit_atomically(
    tmp_path: Path,
) -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        now = datetime.now(timezone.utc)
        system_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        blob_store = BlobStore(tmp_path)
        stored = blob_store.store_stream(
            io.BytesIO(b'{"system":{"name":"Synthetic FISMA"}}'),
            max_bytes=1024 * 1024,
        )
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=system_id,
                            display_name="Synthetic intake integration",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=[],
                            created_at=now,
                            archived_at=None,
                        )
                    )
                    revision = PackageRevision(
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
                        revision_version=2,
                        status="scanning",
                        created_by="integration@example.test",
                        created_at=now,
                    )
                    artifact = SourceArtifact(
                        artifact_id=artifact_id,
                        package_revision_id=revision_id,
                        display_filename="synthetic.json",
                        storage_key=stored.storage_key,
                        sha256=stored.sha256,
                        size_bytes=stored.size_bytes,
                        declared_media_type="application/json",
                        detected_media_type="application/json",
                        artifact_kind="manifest",
                        malware_scan_status="pending",
                        extraction_status="pending",
                        source_date=None,
                        uploaded_at=now,
                    )
                    session.add_all([revision, artifact])
                    await session.flush()

                    scan = await process_next_synthetic_scan(
                        session,
                        hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                        now=now,
                    )
                    assert scan is not None
                    await session.flush()

                    extraction = await process_next_synthetic_extraction(
                        session,
                        blob_store=blob_store,
                        hmac_key=b"x" * MIN_AUDIT_HMAC_KEY_BYTES,
                        now=now,
                    )
                    assert extraction is not None
                    await session.flush()

                    await session.refresh(revision)
                    await session.refresh(artifact)
                    proposal_count = await session.scalar(
                        select(func.count(FactProposal.fact_proposal_id)).where(
                            FactProposal.package_revision_id == revision_id
                        )
                    )
                    audit_count = await session.scalar(
                        select(func.count(AuditEvent.audit_event_id)).where(
                            AuditEvent.object_id == str(revision_id).lower(),
                            AuditEvent.actor_id == "synthetic-intake-worker",
                        )
                    )
                    assert revision.status == "awaiting_confirmation"
                    assert revision.revision_version == 4
                    assert artifact.malware_scan_status == "clean"
                    assert artifact.extraction_status == "succeeded"
                    assert proposal_count == 1
                    assert audit_count == 2
                finally:
                    await session.close()
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(exercise())
