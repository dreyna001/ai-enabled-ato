"""Tests for async readiness dependency checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.db.dsn import DATABASE_DSN_FILE_ENV_VAR
from ato_service.health import READINESS_CHECK_NAMES
from ato_service.readiness import ReadinessDependencies, create_readiness_probe, run_readiness_checks
from ato_service.runtime_config import RuntimeConfig, load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = ROOT / "docs" / "contracts" / "authority-manifest.json"
POSTGRES_URL = "postgresql+asyncpg://ato:secret@localhost:5432/ato_test"


def _run(coro):
    return asyncio.run(coro)


def _dev_config(
    tmp_path: Path,
    *,
    extra: dict | None = None,
) -> RuntimeConfig:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "STORAGE_DATA_PATH": "/data/ato-storage",
    }
    if extra:
        document.update(extra)
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _make_engine(*, probe_error: Exception | None = None) -> AsyncEngine:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock()
    connection.execute = AsyncMock(
        side_effect=probe_error,
    )
    connect_cm = AsyncMock()
    connect_cm.__aenter__.return_value = connection
    connect_cm.__aexit__.return_value = None
    engine.connect.return_value = connect_cm
    return engine


def _make_deps(
    tmp_path: Path,
    *,
    engine: AsyncEngine | None = None,
    manifest_path: Path | None = None,
    config: RuntimeConfig | None = None,
) -> ReadinessDependencies:
    resolved_config = config or _dev_config(tmp_path)
    resolved_config.storage_data_path.mkdir(parents=True, exist_ok=True)
    return ReadinessDependencies(
        config=resolved_config,
        authority_manifest_path=manifest_path or DEFAULT_MANIFEST_PATH,
        project_root=ROOT,
        get_engine=lambda: engine,
    )


def _write_approved_manifest(
    tmp_path: Path,
    *,
    artifact_bytes: bytes = b"approved-authority-bytes",
) -> Path:
    artifact_dir = tmp_path / "reference" / "authorities" / "fixture"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "authority.json"
    artifact_path.write_bytes(artifact_bytes)
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "fixture.approved",
        "status": "approved",
        "created_at": "2026-07-10T22:33:12Z",
        "approved_at": "2026-07-10T22:33:12Z",
        "approved_by": "fixture-operator",
        "sources": [
            {
                "authority_id": "fixture-authority",
                "title": "Fixture Authority",
                "source_url": "https://example.test/authority.json",
                "source_version_or_date": "2026-07-10",
                "retrieved_at_utc": "2026-07-10T22:33:12Z",
                "effective_date": "2026-07-10",
                "sha256": digest,
                "size_bytes": len(artifact_bytes),
                "local_path": "reference/authorities/fixture/authority.json",
                "review_status": "reviewed",
            }
        ],
    }
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_all_checks_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dsn_file = tmp_path / "database.dsn"
    dsn_file.write_text(POSTGRES_URL, encoding="utf-8")
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(dsn_file))

    manifest_path = _write_approved_manifest(tmp_path)
    deps = _make_deps(
        tmp_path,
        engine=_make_engine(),
        manifest_path=manifest_path,
    )
    deps = ReadinessDependencies(
        config=deps.config,
        authority_manifest_path=manifest_path,
        project_root=tmp_path,
        get_engine=deps.get_engine,
    )

    checks = _run(run_readiness_checks(deps))

    assert set(checks) == set(READINESS_CHECK_NAMES)
    assert checks == {
        "database": "ok",
        "storage": "ok",
        "authority_manifest": "ok",
        "jobs": "ok",
        "configuration": "ok",
    }


def test_database_unavailable_on_probe_failure(tmp_path: Path) -> None:
    deps = _make_deps(
        tmp_path,
        engine=_make_engine(probe_error=RuntimeError("connection refused")),
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["database"] == "unavailable"
    assert checks["jobs"] == "ok"
    assert set(checks) == set(READINESS_CHECK_NAMES)


def test_database_unavailable_when_engine_missing(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, engine=None)

    checks = _run(run_readiness_checks(deps))

    assert checks["database"] == "unavailable"


def test_storage_unavailable_when_root_missing(tmp_path: Path) -> None:
    config = _dev_config(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("not-a-directory", encoding="utf-8")
    missing_root = blocker / "storage"
    deps = ReadinessDependencies(
        config=RuntimeConfig(
            runtime_profile=config.runtime_profile,
            storage_data_path=missing_root,
            document=config.document,
        ),
        authority_manifest_path=DEFAULT_MANIFEST_PATH,
        project_root=ROOT,
        get_engine=lambda: _make_engine(),
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["storage"] == "unavailable"


def test_storage_unavailable_on_read_only_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "readonly-storage"
    storage_root.mkdir()
    config = _dev_config(tmp_path)
    deps = ReadinessDependencies(
        config=RuntimeConfig(
            runtime_profile=config.runtime_profile,
            storage_data_path=storage_root,
            document=config.document,
        ),
        authority_manifest_path=DEFAULT_MANIFEST_PATH,
        project_root=ROOT,
        get_engine=lambda: _make_engine(),
    )

    original_open = Path.open

    def _restricted_open(self, *args, **kwargs):
        if self.name.startswith("readiness-"):
            raise PermissionError("storage root is read-only")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _restricted_open)

    checks = _run(run_readiness_checks(deps))

    assert checks["storage"] == "unavailable"


def test_storage_probe_leaves_no_files(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, engine=_make_engine())

    _run(run_readiness_checks(deps))

    temp_dir = deps.config.storage_data_path / "_tmp"
    if temp_dir.exists():
        assert list(temp_dir.glob("readiness-*")) == []


def test_authority_manifest_unavailable_when_missing(tmp_path: Path) -> None:
    deps = _make_deps(
        tmp_path,
        engine=_make_engine(),
        manifest_path=tmp_path / "missing-authority-manifest.json",
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["authority_manifest"] == "unavailable"


def test_authority_manifest_unavailable_on_digest_mismatch(tmp_path: Path) -> None:
    manifest_path = _write_approved_manifest(tmp_path)
    artifact_path = tmp_path / "reference" / "authorities" / "fixture" / "authority.json"
    artifact_path.write_bytes(b"tampered-bytes")

    deps = ReadinessDependencies(
        config=_dev_config(tmp_path),
        authority_manifest_path=manifest_path,
        project_root=tmp_path,
        get_engine=lambda: _make_engine(),
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["authority_manifest"] == "unavailable"


def test_authority_manifest_degraded_for_draft_status(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, engine=_make_engine(), manifest_path=DEFAULT_MANIFEST_PATH)

    checks = _run(run_readiness_checks(deps))

    assert checks["authority_manifest"] == "degraded"


def test_authority_manifest_verification_streams_artifact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _write_approved_manifest(tmp_path)

    def _reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("authority artifacts must not be loaded with read_bytes")

    monkeypatch.setattr(Path, "read_bytes", _reject_read_bytes)

    manifest = verify_authority_manifest(manifest_path, project_root=tmp_path)

    assert manifest["manifest_id"] == "fixture.approved"


def test_jobs_placeholder_always_ok(tmp_path: Path) -> None:
    deps = _make_deps(
        tmp_path,
        engine=_make_engine(probe_error=RuntimeError("database down")),
        manifest_path=tmp_path / "missing-authority-manifest.json",
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["jobs"] == "ok"


def test_configuration_unavailable_when_dsn_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATABASE_DSN_FILE_ENV_VAR, raising=False)
    deps = _make_deps(tmp_path, engine=_make_engine())

    checks = _run(run_readiness_checks(deps))

    assert checks["configuration"] == "unavailable"


def test_configuration_unavailable_for_malformed_schema_version(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    malformed = RuntimeConfig(
        runtime_profile=config.runtime_profile,
        storage_data_path=config.storage_data_path,
        document={**config.document, "schema_version": "9.9.9"},
    )
    malformed.storage_data_path.mkdir(parents=True, exist_ok=True)
    deps = ReadinessDependencies(
        config=malformed,
        authority_manifest_path=DEFAULT_MANIFEST_PATH,
        project_root=ROOT,
        get_engine=lambda: _make_engine(),
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["configuration"] == "unavailable"


def test_create_readiness_probe_returns_exact_five_keys(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, engine=_make_engine())
    probe = create_readiness_probe(deps)

    checks = _run(probe())

    assert tuple(checks.keys()) == READINESS_CHECK_NAMES


def test_probe_swallows_check_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _make_deps(tmp_path, engine=_make_engine())

    def _exploding_verify(*args, **kwargs):
        raise AuthorityManifestVerificationError("internal verifier failure")

    monkeypatch.setattr(
        "ato_service.readiness.verify_authority_manifest",
        _exploding_verify,
    )

    checks = _run(run_readiness_checks(deps))

    assert checks["authority_manifest"] == "unavailable"
    assert set(checks) == set(READINESS_CHECK_NAMES)


def test_probe_contains_unexpected_sync_check_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _make_deps(tmp_path, engine=_make_engine())

    def _storage_failure(_deps: ReadinessDependencies):
        raise RuntimeError("unexpected storage failure")

    def _authority_failure(_deps: ReadinessDependencies):
        raise TypeError("unexpected authority failure")

    def _configuration_failure(_deps: ReadinessDependencies):
        raise AssertionError("unexpected configuration failure")

    monkeypatch.setattr("ato_service.readiness._check_storage", _storage_failure)
    monkeypatch.setattr(
        "ato_service.readiness._check_authority_manifest",
        _authority_failure,
    )
    monkeypatch.setattr(
        "ato_service.readiness._check_configuration",
        _configuration_failure,
    )

    checks = _run(run_readiness_checks(deps))

    assert checks == {
        "database": "ok",
        "storage": "unavailable",
        "authority_manifest": "unavailable",
        "jobs": "ok",
        "configuration": "unavailable",
    }
