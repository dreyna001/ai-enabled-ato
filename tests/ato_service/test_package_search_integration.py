"""Optional PostgreSQL integration tests for package search indexes."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ato_service.blobs import BlobStore
from ato_service.db.models import (
    PackageRevision,
    PackageRevisionSearchChunk,
    PackageRevisionSearchIndex,
    SealedPackageContent,
    SourceArtifact,
    System,
    SystemContextSnapshot,
)
from ato_service.db.session import create_async_engine_from_url
from ato_service.package_search_index import (
    delete_revision_search_index,
    rebuild_revision_search_index,
    search_revision_chunks,
)
from ato_service.runtime_config import load_runtime_config


@pytest.mark.integration
def test_postgres_search_index_rebuild_search_and_delete(tmp_path) -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        now = datetime.now(timezone.utc)
        system_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        config_path = tmp_path / "runtime-config.dev_local.json"
        config_path.write_text(
            '{"runtime_profile":"dev_local","STORAGE_DATA_PATH":"'
            + str(tmp_path / "storage")
            + '"}',
            encoding="utf-8",
        )
        config = load_runtime_config(config_path, base_dir=tmp_path)
        blob_store = BlobStore(tmp_path / "storage")
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    session.add(
                        System(
                            system_id=system_id,
                            display_name="Search integration",
                            external_system_id=None,
                            customer_enterprise_id="dev-local-enterprise",
                            owner_group="owners",
                            viewer_groups=[],
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
                            effective_data_labels=["internal_unclassified", "synthetic"],
                            authority_manifest_id="authority.v2",
                            content_manifest_sha256="a" * 64,
                            package_content_sha256="b" * 64,
                            revision_version=3,
                            status="ready",
                            created_by="integration@example.test",
                            created_at=now,
                        )
                    )
                    session.add(
                        SystemContextSnapshot(
                            system_context_snapshot_id=snapshot_id,
                            system_id=system_id,
                            version=1,
                            content_sha256="d" * 64,
                            document={"system": {"display_name": "Search integration"}},
                            created_by="integration@example.test",
                            created_at=now,
                        )
                    )
                    session.add(
                        SourceArtifact(
                            artifact_id=artifact_id,
                            package_revision_id=revision_id,
                            display_filename="policy.txt",
                            storage_key="sha256/" + ("c" * 64),
                            sha256="c" * 64,
                            size_bytes=32,
                            declared_media_type="text/plain",
                            detected_media_type="text/plain",
                            artifact_kind="evidence",
                            malware_scan_status="clean",
                            extraction_status="succeeded",
                            source_date=None,
                            uploaded_at=now,
                        )
                    )
                    session.add(
                        SealedPackageContent(
                            package_revision_id=revision_id,
                            document_schema_version="1.0.0",
                            document={
                                "security_controls": {
                                    "AC-1": {
                                        "implementation_statement": "password policy enforced",
                                    }
                                },
                                "evidence": {},
                            },
                            field_provenance={},
                            content_sha256="b" * 64,
                            system_context_snapshot_id=snapshot_id,
                            sealed_by="integration@example.test",
                            sealed_at=now,
                        )
                    )
                    await session.flush()
                    count = await rebuild_revision_search_index(
                        session,
                        package_revision_id=revision_id,
                        config=config,
                        blob_store=blob_store,
                        now=now,
                    )
                    assert count >= 1
                    page = await search_revision_chunks(
                        session,
                        package_revision_id=revision_id,
                        query="password policy",
                        limit=10,
                    )
                    assert page.items
                    index_row = (
                        await session.execute(
                            select(PackageRevisionSearchIndex).where(
                                PackageRevisionSearchIndex.package_revision_id == revision_id
                            )
                        )
                    ).scalar_one()
                    assert index_row.chunk_count >= 1
                    await delete_revision_search_index(
                        session,
                        package_revision_id=revision_id,
                    )
                    remaining = (
                        await session.execute(
                            select(PackageRevisionSearchChunk).where(
                                PackageRevisionSearchChunk.package_revision_id == revision_id
                            )
                        )
                    ).scalars().all()
                    assert remaining == []
                finally:
                    await transaction.rollback()
                    await session.close()
        finally:
            await engine.dispose()

    asyncio.run(exercise())
