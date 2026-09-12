"""Focused tests for the agency DOCX malware-scan processing boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest
from docx import Document

from ato_service.blobs import BlobStore
from ato_service.malware_scan import (
    MalwareScanOutcome,
    MalwareScanResult,
    MalwareScannerUnavailableError,
)
from ato_service.ssp_workspace.agency_docx import AgencyDocxError
from ato_service.ssp_workspace.model_policy import SspModelPolicyError
from ato_service.ssp_workspace.service import (
    AgencyDocxUploadError,
    create_agency_docx_render,
)


def _template_bytes() -> bytes:
    document = Document()
    document.add_paragraph("System Name:")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _config(
    *,
    runtime_profile: str = "onprem_production",
    malware_scanning: bool = True,
    scanner_enabled: bool = True,
    scanner_configured: bool = True,
    text_model_calls: bool = True,
    max_single_file_bytes: int = 5_000_000,
) -> SimpleNamespace:
    document: dict[str, object] = {
        "PROCESS_CAPABILITIES": {
            "malware_scanning": malware_scanning,
            "text_model_calls": text_model_calls,
        },
        "MALWARE_SCANNER_ENABLED": scanner_enabled,
        "MALWARE_SCANNER_ID": "clamav",
        "MALWARE_SCANNER_TRANSPORT": "tcp_loopback",
        "MALWARE_SCANNER_HOST": "127.0.0.1",
        "MALWARE_SCANNER_PORT": 3310,
        "MALWARE_SCANNER_TIMEOUT_SECONDS": 1,
        "TEXT_MODEL_ENDPOINT_PROFILE": "internal_openai_compatible",
        "TEXT_MODEL_ENDPOINT_POLICY_APPROVED": True,
    }
    if not scanner_configured:
        document.pop("MALWARE_SCANNER_ID")
    if runtime_profile == "dev_local":
        document["TEXT_MODEL_ENDPOINT_PROFILE"] = "mock"
    return SimpleNamespace(
        runtime_profile=runtime_profile,
        document=document,
        limits=SimpleNamespace(max_single_file_bytes=max_single_file_bytes),
    )


def _session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _snapshot() -> dict[str, object]:
    return {
        "content_sha256": "a" * 64,
        "sections": [{"section_id": "purpose"}],
    }


class _Scanner:
    def __init__(
        self,
        result: MalwareScanResult,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.events = events
        self.calls: list[dict[str, object]] = []

    def scan_verified_bytes(
        self,
        *,
        content_bytes: bytes,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> MalwareScanResult:
        if self.events is not None:
            self.events.append("scan")
        self.calls.append(
            {
                "content_bytes": content_bytes,
                "expected_sha256": expected_sha256,
                "expected_size_bytes": expected_size_bytes,
            }
        )
        return self.result


def _create_kwargs(
    *,
    blob_store: BlobStore,
    config: SimpleNamespace,
    model: object,
    template_bytes: bytes,
) -> dict[str, object]:
    return {
        "workspace_id": uuid.uuid4(),
        "source_revision_id": uuid.uuid4(),
        "template_filename": "agency-template.docx",
        "template_bytes": template_bytes,
        "actor_id": "isso@example.gov",
        "now": datetime.now(UTC),
        "blob_store": blob_store,
        "config": config,
        "model": model,
        "audit_hmac_key": b"test-audit-key------",
    }


def test_clean_scan_precedes_outline_parse_database_snapshot_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_bytes = _template_bytes()
    events: list[str] = []
    scanner = _Scanner(MalwareScanResult(MalwareScanOutcome.CLEAN), events)
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner",
        lambda config: scanner,
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.service.require_ssp_model_allowed",
        lambda config: events.append("policy"),
    )

    async def approved_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        events.append("snapshot")
        return _snapshot()

    monkeypatch.setattr(
        "ato_service.ssp_workspace.service._approved_export_snapshot",
        approved_snapshot,
    )

    @asynccontextmanager
    async def snapshot_transaction(_session: object) -> AsyncIterator[None]:
        events.append("db_start")
        yield
        events.append("db_end")

    monkeypatch.setattr(
        "ato_service.ssp_workspace.service._read_snapshot_transaction",
        snapshot_transaction,
    )

    def extract_outline(*args: object, **kwargs: object) -> object:
        events.append("parse")
        return object()

    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline",
        extract_outline,
    )
    monkeypatch.setattr(
        "ato_service.extraction.limits.resolve_extraction_limits_from_config",
        lambda config: object(),
    )

    model_mock = MagicMock()

    async def generate_mapping_plan(
        outline: object,
        snapshot: dict[str, object],
        model: object,
    ) -> object:
        assert model is model_mock
        events.append("model")
        raise AgencyDocxError("stop after ordering check")

    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan",
        generate_mapping_plan,
    )

    session = _session()

    workspace = SimpleNamespace(profile_version_id=uuid.uuid4())

    async def execute(statement: object) -> MagicMock:
        result = MagicMock()
        if "ssp_workspaces" in str(statement):
            result.scalar_one.return_value = workspace
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=execute)

    with pytest.raises(AgencyDocxUploadError, match="stop after ordering check"):
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=BlobStore(tmp_path),
                    config=_config(),
                    model=model_mock,
                    template_bytes=template_bytes,
                ),
            )
        )

    assert events == [
        "scan",
        "policy",
        "parse",
        "db_start",
        "snapshot",
        "db_end",
        "model",
    ]
    assert scanner.calls[0] == {
        "content_bytes": template_bytes,
        "expected_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "expected_size_bytes": len(template_bytes),
    }


@pytest.mark.parametrize(
    ("outcome", "expected_exception"),
    [
        (MalwareScanOutcome.INFECTED, AgencyDocxUploadError),
        (MalwareScanOutcome.ERROR, MalwareScannerUnavailableError),
    ],
)
def test_infected_or_unavailable_scan_blocks_all_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: MalwareScanOutcome,
    expected_exception: type[Exception],
) -> None:
    scanner = _Scanner(MalwareScanResult(outcome))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner",
        lambda config: scanner,
    )
    extract = MagicMock()
    generate = AsyncMock()
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline", extract
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan", generate
    )
    model_mock = MagicMock()
    blob_store = BlobStore(tmp_path)
    store_stream = MagicMock(wraps=blob_store.store_stream)
    blob_store.store_stream = store_stream  # type: ignore[method-assign]
    session = _session()

    with pytest.raises(expected_exception):
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=blob_store,
                    config=_config(),
                    model=model_mock,
                    template_bytes=_template_bytes(),
                ),
            )
        )

    extract.assert_not_called()
    generate.assert_not_awaited()
    model_mock.assert_not_called()
    store_stream.assert_not_called()
    session.execute.assert_not_awaited()


def test_disabled_scanner_blocks_before_resolution_or_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("disabled scanner must not resolve"))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner", resolver
    )
    extract = MagicMock()
    generate = AsyncMock()
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline", extract
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan", generate
    )
    model_mock = MagicMock()
    blob_store = BlobStore(tmp_path)
    store_stream = MagicMock(wraps=blob_store.store_stream)
    blob_store.store_stream = store_stream  # type: ignore[method-assign]
    session = _session()

    with pytest.raises(MalwareScannerUnavailableError):
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=blob_store,
                    config=_config(malware_scanning=False),
                    model=model_mock,
                    template_bytes=_template_bytes(),
                ),
            )
        )

    resolver.assert_not_called()
    extract.assert_not_called()
    generate.assert_not_awaited()
    model_mock.assert_not_called()
    store_stream.assert_not_called()
    session.execute.assert_not_awaited()


def test_unconfigured_scanner_blocks_before_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extract = MagicMock()
    generate = AsyncMock()
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline", extract
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan", generate
    )
    model_mock = MagicMock()
    blob_store = BlobStore(tmp_path)
    store_stream = MagicMock(wraps=blob_store.store_stream)
    blob_store.store_stream = store_stream  # type: ignore[method-assign]
    session = _session()

    with pytest.raises(MalwareScannerUnavailableError):
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=blob_store,
                    config=_config(scanner_configured=False),
                    model=model_mock,
                    template_bytes=_template_bytes(),
                ),
            )
        )

    extract.assert_not_called()
    generate.assert_not_awaited()
    model_mock.assert_not_called()
    store_stream.assert_not_called()
    session.execute.assert_not_awaited()


@pytest.mark.parametrize(
    ("template_bytes", "max_single_file_bytes", "message"),
    [
        (b"", 5_000_000, "template file cannot be empty"),
        (b"oversized", 4, "template file exceeds configured limit"),
    ],
)
def test_empty_or_oversized_template_is_rejected_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template_bytes: bytes,
    max_single_file_bytes: int,
    message: str,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("invalid template must not scan"))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner", resolver
    )
    extract = MagicMock()
    generate = AsyncMock()
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline", extract
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan", generate
    )
    model_mock = MagicMock()
    blob_store = BlobStore(tmp_path)
    store_stream = MagicMock(wraps=blob_store.store_stream)
    blob_store.store_stream = store_stream  # type: ignore[method-assign]
    session = _session()

    with pytest.raises(AgencyDocxUploadError, match=message):
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=blob_store,
                    config=_config(max_single_file_bytes=max_single_file_bytes),
                    model=model_mock,
                    template_bytes=template_bytes,
                ),
            )
        )

    resolver.assert_not_called()
    extract.assert_not_called()
    generate.assert_not_awaited()
    model_mock.assert_not_called()
    store_stream.assert_not_called()
    session.execute.assert_not_awaited()


def test_disabled_model_remains_blocked_after_clean_production_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = _Scanner(MalwareScanResult(MalwareScanOutcome.CLEAN))
    monkeypatch.setattr(
        "ato_service.ssp_workspace.evidence.resolve_malware_scanner",
        lambda config: scanner,
    )
    extract = MagicMock()
    generate = AsyncMock()
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.extract_template_outline", extract
    )
    monkeypatch.setattr(
        "ato_service.ssp_workspace.agency_docx.generate_mapping_plan", generate
    )
    model_mock = MagicMock()
    blob_store = BlobStore(tmp_path)
    store_stream = MagicMock(wraps=blob_store.store_stream)
    blob_store.store_stream = store_stream  # type: ignore[method-assign]
    session = _session()

    with pytest.raises(SspModelPolicyError) as caught:
        asyncio.run(
            create_agency_docx_render(
                session,
                **_create_kwargs(
                    blob_store=blob_store,
                    config=_config(text_model_calls=False),
                    model=model_mock,
                    template_bytes=_template_bytes(),
                ),
            )
        )

    assert caught.value.error_code == "prohibited_model_action"
    extract.assert_not_called()
    generate.assert_not_awaited()
    model_mock.assert_not_called()
    store_stream.assert_not_called()
    session.execute.assert_not_awaited()
