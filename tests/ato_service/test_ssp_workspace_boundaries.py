"""Focused SSP evidence and database-boundary regressions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from io import BytesIO
from pathlib import Path
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from docx import Document
import pytest
from sqlalchemy import select, text

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.blobs import BlobStore
from ato_service.db.base import Base
from ato_service.db.models import SspAgencyDocxRender, SspWorkspaceRevision
from ato_service.db.session import create_async_engine_from_url, create_session_factory
from ato_service.malware_scan import (
    MalwareScanOutcome,
    MalwareScanResult,
    MalwareScannerUnavailableError,
)
from ato_service.ssp_workspace.contracts import FactContent, Provenance, RevisionContent
from ato_service.ssp_workspace.evidence import (
    _scan_before_processing,
    ingest_workspace_evidence,
)
from ato_service.ssp_workspace.generation import GenerationExecution, ModelPrompt
from ato_service.ssp_workspace.generation_contracts import GenerationResult
from ato_service.ssp_workspace.persistence import StaleWorkspaceRevisionError, save_revision
from ato_service.ssp_workspace.profile_bundles import load_profile_bundle
from ato_service.ssp_workspace.profiles import activate_profile, import_profile
from ato_service.runtime_config import load_runtime_config_from_dict
from ato_service.ssp_workspace.service import (
    AgencyDocxRenderReference,
    _read_snapshot_transaction,
    create_agency_docx_render,
    create_initialized_workspace,
    generate_workspace_draft,
)
from ato_service.systems import create_system

import ato_service.db.models  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    PROJECT_ROOT / "reference" / "ssp_profiles" / "synthetic-fisma-rev5-1.0.0"
)


def _dev_model_config(tmp_path: Path):
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/storage",
            "INSTALLATION_CUSTOMER_ENTERPRISE_ID": "dev-local-enterprise",
            "PROCESS_CAPABILITIES": {
                "api": True,
                "intake_worker": True,
                "analyzer_worker": True,
                "portal_static": False,
                "malware_scanning": False,
                "text_model_calls": True,
                "vision_model_calls": False,
                "oidc_authentication": False,
                "package_search": True,
                "package_chat": False,
            },
            "TEXT_MODEL_ENDPOINT_PROFILE": "mock",
            "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
        },
        base_dir=tmp_path,
    )


async def _create_isolated_schema(engine) -> str:
    schema = f"ssp_boundary_{uuid.uuid4().hex}"
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET search_path TO "{schema}"'))
        await connection.run_sync(Base.metadata.create_all)
    return schema


async def _drop_isolated_schema(engine, schema: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


async def _set_search_path(session, schema: str) -> None:
    await session.execute(text(f'SET search_path TO "{schema}"'))
    await session.commit()


async def _seed_workspace(
    factory,
    schema: str,
    *,
    actor_id: str,
    hmac_key: bytes,
    now: datetime,
) -> tuple[object, object]:
    bundle = load_profile_bundle(PROFILE_PATH)
    async with factory() as setup:
        await _set_search_path(setup, schema)
        profile_row = await import_profile(
            setup,
            bundle=bundle,
            imported_by=actor_id,
            now=now,
        )
        await activate_profile(
            setup,
            profile_version_id=profile_row.profile_version_id,
            now=now,
        )
        principal = AuthenticatedPrincipal(
            actor_id=actor_id,
            groups=("system-owners",),
            csrf_token="c" * 32,
            allowed_origins=("https://portal.example",),
        )
        system_result = await create_system(
            setup,
            principal=principal,
            audit_hmac_key=hmac_key,
            idempotency_key="ssp-boundary-system-create-1",
            display_name="Boundary regression system",
            external_system_id="BOUNDARY-001",
            owner_group="system-owners",
            viewer_groups=["system-viewers"],
            customer_enterprise_id="dev-local-enterprise",
            now=now,
        )
        workspace = await create_initialized_workspace(
            setup,
            system_id=uuid.UUID(system_result.payload["system_id"]),
            profile_version_id=profile_row.profile_version_id,
            impact_level="low",
            actor_id=actor_id,
            now=now,
            audit_hmac_key=hmac_key,
        )
        await setup.commit()
    return workspace, profile_row


def _production_config(*, malware_scanning: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_profile="onprem_production",
        document={
            "PROCESS_CAPABILITIES": {"malware_scanning": malware_scanning},
            "MALWARE_SCANNER_ENABLED": True,
            "MALWARE_SCANNER_ID": "clamav",
        },
        limits=SimpleNamespace(max_single_file_bytes=1_000_000),
        extraction_limits=SimpleNamespace(),
        vision_model_enabled=False,
    )


def test_production_scan_capability_blocks_before_scanner_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("scanner must not resolve"))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner", resolver
    )

    with pytest.raises(MalwareScannerUnavailableError, match="disabled"):
        asyncio.run(
            _scan_before_processing(
                config=_production_config(malware_scanning=False),
                content=b"customer evidence",
            )
        )

    resolver.assert_not_called()


def test_production_scan_rejects_injected_dev_substitute() -> None:
    from ato_service.malware_scan import DevLocalIntegrityScanSubstitute

    with pytest.raises(MalwareScannerUnavailableError, match="configured scanner"):
        asyncio.run(
            _scan_before_processing(
                config=_production_config(),
                content=b"customer evidence",
                scanner=DevLocalIntegrityScanSubstitute(),
            )
        )


def test_evidence_scan_precedes_storage_and_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    workspace = SimpleNamespace(
        workspace_id=workspace_id,
        current_revision_id=revision_id,
    )
    revision = SimpleNamespace(
        revision_id=revision_id,
        workspace_id=workspace_id,
        content=RevisionContent().model_dump(mode="json"),
    )
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    async def execute(statement: object) -> MagicMock:
        sql = str(statement)
        result = MagicMock()
        if "ssp_workspaces" in sql:
            result.scalar_one_or_none.return_value = workspace
        elif "ssp_workspace_revisions" in sql:
            result.scalar_one_or_none.return_value = revision
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session.execute.side_effect = execute
    events: list[str] = []

    class Scanner:
        def scan_verified_bytes(self, **_: object) -> MalwareScanResult:
            events.append("scan")
            return MalwareScanResult(MalwareScanOutcome.CLEAN)

    blob_store = BlobStore(tmp_path)
    store_stream = blob_store.store_stream

    def store(source: object, *, max_bytes: int) -> object:
        events.append("store")
        return store_stream(source, max_bytes=max_bytes)

    monkeypatch.setattr(blob_store, "store_stream", store)
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner",
        lambda config: Scanner(),
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.extract_content",
        lambda **_: events.append("parse")
        or SimpleNamespace(status="processed", detected_format="txt", segments=()),
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.append_audit_event", AsyncMock()
    )

    asyncio.run(
        ingest_workspace_evidence(
            session,
            workspace_id=workspace_id,
            expected_revision_id=revision_id,
            filename="evidence.txt",
            media_type="text/plain",
            content=b"customer evidence",
            actor_id="isso@example.gov",
            now=datetime.now(UTC),
            blob_store=blob_store,
            config=_production_config(),
            audit_hmac_key=b"audit-key",
        )
    )

    assert events == ["scan", "store", "parse"]


@pytest.mark.integration
def test_snapshot_transaction_releases_postgres_root_before_slow_work() -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine_from_url(url)
        factory = create_session_factory(engine)
        lock_key = 2_147_483_000
        try:
            async with factory() as session, factory() as observer:
                async with _read_snapshot_transaction(session):
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                    assert session.in_transaction()

                assert not session.in_transaction()
                slow_work_started = asyncio.Event()
                release_slow_work = asyncio.Event()

                async def slow_work() -> None:
                    slow_work_started.set()
                    await release_slow_work.wait()

                work = asyncio.create_task(slow_work())
                await slow_work_started.wait()
                released = await observer.execute(
                    text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )
                assert released.scalar_one() is True
                await observer.rollback()
                release_slow_work.set()
                await work
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_generation_slow_callback_has_no_root_transaction_and_rejects_stale_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        config = _dev_model_config(tmp_path)
        engine = create_async_engine_from_url(url)
        schema = await _create_isolated_schema(engine)
        factory = create_session_factory(engine)
        hmac_key = b"k" * 32
        principal = AuthenticatedPrincipal(
            actor_id="integration@example.gov",
            groups=("system-owners",),
            csrf_token="c" * 32,
            allowed_origins=("https://portal.example",),
        )
        try:
            bundle = load_profile_bundle(PROFILE_PATH)
            async with factory() as setup:
                await _set_search_path(setup, schema)
                profile_row = await import_profile(
                    setup,
                    bundle=bundle,
                    imported_by=principal.actor_id,
                    now=datetime.now(UTC),
                )
                await activate_profile(
                    setup,
                    profile_version_id=profile_row.profile_version_id,
                    now=datetime.now(UTC),
                )
                system_result = await create_system(
                    setup,
                    principal=principal,
                    audit_hmac_key=hmac_key,
                    idempotency_key="ssp-boundary-system-create-1",
                    display_name="Boundary regression system",
                    external_system_id="BOUNDARY-001",
                    owner_group="system-owners",
                    viewer_groups=["system-viewers"],
                    customer_enterprise_id="dev-local-enterprise",
                    now=datetime.now(UTC),
                )
                workspace = await create_initialized_workspace(
                    setup,
                    system_id=uuid.UUID(system_result.payload["system_id"]),
                    profile_version_id=profile_row.profile_version_id,
                    impact_level="low",
                    actor_id=principal.actor_id,
                    now=datetime.now(UTC),
                    audit_hmac_key=hmac_key,
                )
                await setup.commit()
            workspace_id = workspace.workspace_id
            async with factory() as worker_session, factory() as modifier_session:
                await _set_search_path(worker_session, schema)
                await _set_search_path(modifier_session, schema)
                revision = (
                    await worker_session.execute(
                        select(SspWorkspaceRevision)
                        .where(
                            SspWorkspaceRevision.revision_id == workspace.current_revision_id
                        )
                    )
                ).scalar_one()
                expected_revision_id = revision.revision_id
                await worker_session.rollback()
                callback_started = asyncio.Event()
                release_callback = asyncio.Event()
                callback_transactions: list[bool] = []

                async def slow_model(_prompt: ModelPrompt) -> str:
                    callback_transactions.append(worker_session.in_transaction())
                    callback_started.set()
                    await release_callback.wait()
                    return "unused"

                async def fake_generation(
                    _request: object, model: object
                ) -> GenerationExecution[GenerationResult]:
                    await model(ModelPrompt("system", "user"))
                    return GenerationExecution(
                        value=GenerationResult(sections=(), controls=(), questions=()),
                        attempts=1,
                        repair_attempted=False,
                    )

                monkeypatch.setattr(
                    "ato_service.ssp_workspace.service.generate_initial_ssp",
                    fake_generation,
                )
                generation = asyncio.create_task(
                    generate_workspace_draft(
                        worker_session,
                        workspace_id=workspace_id,
                        expected_revision_id=expected_revision_id,
                        model=slow_model,
                        config=config,
                        actor_id=principal.actor_id,
                        now=datetime.now(UTC),
                        audit_hmac_key=hmac_key,
                    )
                )
                try:
                    await asyncio.wait_for(callback_started.wait(), timeout=15)
                    current = (
                        await modifier_session.execute(
                            select(SspWorkspaceRevision)
                            .where(
                                SspWorkspaceRevision.revision_id == expected_revision_id
                            )
                        )
                    ).scalar_one()
                    changed_content = RevisionContent.model_validate(
                        current.content
                    ).model_copy(
                        update={
                            "facts": (
                                FactContent(
                                    key="concurrent.change",
                                    value="changed while model was waiting",
                                    provenance=Provenance.ISSO_ENTERED,
                                ),
                            )
                        }
                    )
                    await save_revision(
                        modifier_session,
                        workspace_id=workspace_id,
                        content=changed_content,
                        created_by=principal.actor_id,
                        now=datetime.now(UTC),
                        expected_revision_id=expected_revision_id,
                    )
                    await modifier_session.commit()
                    release_callback.set()
                    with pytest.raises(StaleWorkspaceRevisionError):
                        await generation
                    await worker_session.rollback()

                    assert callback_transactions == [False]
                    assert not worker_session.in_transaction()
                finally:
                    release_callback.set()
                    if not generation.done():
                        generation.cancel()
                    await asyncio.gather(generation, return_exceptions=True)
        finally:
            await _drop_isolated_schema(engine, schema)
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_agency_docx_cached_render_returns_detached_reference(
    tmp_path: Path,
) -> None:
    url = os.environ.get("ATO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ATO_TEST_DATABASE_URL is not configured")

    async def exercise() -> None:
        engine = create_async_engine_from_url(url)
        schema = await _create_isolated_schema(engine)
        factory = create_session_factory(engine)
        config = _dev_model_config(tmp_path)
        now = datetime.now(UTC)
        hmac_key = b"k" * 32
        try:
            workspace, profile = await _seed_workspace(
                factory,
                schema,
                actor_id="integration@example.gov",
                hmac_key=hmac_key,
                now=now,
            )
            document = Document()
            document.add_paragraph("Agency template")
            template_buffer = BytesIO()
            document.save(template_buffer)
            template_bytes = template_buffer.getvalue()
            template_sha256 = hashlib.sha256(template_bytes).hexdigest()
            output_sha256 = "b" * 64
            cached_id = uuid.uuid4()
            revision_id = workspace.current_revision_id
            async with factory() as session:
                await _set_search_path(session, schema)
                session.add(
                    SspAgencyDocxRender(
                        render_id=cached_id,
                        workspace_id=workspace.workspace_id,
                        profile_version_id=profile.profile_version_id,
                        source_revision_id=revision_id,
                        source_revision_sha256="c" * 64,
                        template_storage_key=f"{template_sha256[:2]}/{template_sha256}",
                        template_sha256=template_sha256,
                        template_filename="agency-template.docx",
                        mapping_plan={"schema_version": "1.0.0"},
                        review_result={"schema_version": "1.0.0"},
                        output_storage_key=f"{output_sha256[:2]}/{output_sha256}",
                        output_sha256=output_sha256,
                        status="awaiting_approval",
                        created_by="integration@example.gov",
                        created_at=now,
                        resolved_by=None,
                        resolved_at=None,
                    )
                )
                await session.commit()

            async with factory() as session:
                await _set_search_path(session, schema)
                with patch(
                    "ato_service.ssp_workspace.service._approved_export_snapshot",
                    new=AsyncMock(
                        return_value={
                            "content_sha256": "c" * 64,
                            "sections": (),
                        }
                    ),
                ):
                    reference = await create_agency_docx_render(
                        session,
                        workspace_id=workspace.workspace_id,
                        source_revision_id=revision_id,
                        template_filename="agency-template.docx",
                        template_bytes=template_bytes,
                        actor_id="integration@example.gov",
                        now=now,
                        blob_store=BlobStore(tmp_path / "storage"),
                        config=config,
                        model=AsyncMock(),
                        audit_hmac_key=hmac_key,
                    )
                assert isinstance(reference, AgencyDocxRenderReference)
                assert reference.render_id == cached_id
                assert reference.output_sha256 == output_sha256
                assert not session.in_transaction()
        finally:
            await _drop_isolated_schema(engine, schema)
            await engine.dispose()

    asyncio.run(exercise())
