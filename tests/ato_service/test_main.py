"""Tests for config-driven app construction and runtime entrypoint wiring."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ato_service.authority_manifest import AuthorityManifestVerificationError
from ato_service.db.dsn import DATABASE_DSN_FILE_ENV_VAR
from ato_service.db.session import DatabaseConfigurationError
from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.main import (
    RUNTIME_CONFIG_PATH_ENV_VAR,
    RUNTIME_STATE_ATTR,
    AppRuntimeState,
    _resolve_audit_hmac_key_for_startup,
    build_app_from_config,
    create_app,
    load_app_from_config_path,
    resolve_database_dsn,
)
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config_from_dict,
)

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = "postgresql+asyncpg://ato:secret@localhost:5432/ato_test"


def _dev_config(tmp_path: Path) -> RuntimeConfig:
    return load_runtime_config_from_dict(
        {
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "STORAGE_DATA_PATH": "/data/ato-storage",
        },
        base_dir=tmp_path,
    )


def _write_runtime_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "runtime_profile": "dev_local",
                "STORAGE_DATA_PATH": "/data/ato-storage",
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _write_draft_authority_manifest(tmp_path: Path) -> tuple[Path, Path]:
    artifact_bytes = b"draft-authority-bytes"
    artifact_path = (
        tmp_path
        / "reference"
        / "authorities"
        / "fixture"
        / "draft-authority.json"
    )
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(artifact_bytes)
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": "fixture.draft",
        "status": "draft",
        "created_at": "2026-07-10T22:33:12Z",
        "approved_at": None,
        "approved_by": None,
        "sources": [
            {
                "authority_id": "fixture-authority",
                "title": "Fixture Authority",
                "source_url": "https://example.test/draft-authority.json",
                "source_version_or_date": "2026-07-10",
                "retrieved_at_utc": "2026-07-10T22:33:12Z",
                "effective_date": "2026-07-10",
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "size_bytes": len(artifact_bytes),
                "local_path": (
                    "reference/authorities/fixture/draft-authority.json"
                ),
                "review_status": "pending",
            }
        ],
    }
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, artifact_path


@pytest.fixture
def dsn_file(tmp_path: Path) -> Path:
    path = tmp_path / "database.dsn"
    path.write_text(POSTGRES_URL, encoding="utf-8")
    return path


def test_resolve_database_dsn_uses_protected_file_for_dev_local(
    tmp_path: Path,
    dsn_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(dsn_file))
    config = _dev_config(tmp_path)

    assert resolve_database_dsn(config) == POSTGRES_URL


def test_resolve_database_dsn_prefers_runtime_credential_reference(
    tmp_path: Path,
    dsn_file: Path,
) -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "DATABASE_DSN_CREDENTIAL_REFERENCE": {
                "source": "root_owned_file",
                "path": str(dsn_file.resolve()),
            },
        },
    )

    assert resolve_database_dsn(config) == POSTGRES_URL


@pytest.mark.parametrize(
    "dsn",
    [
        POSTGRES_URL,
        "postgresql://ato:secret@localhost:5432/ato_test",
        "postgres://ato:secret@localhost:5432/ato_test",
    ],
)
def test_build_app_from_config_validates_without_connecting(
    tmp_path: Path,
    dsn: str,
) -> None:
    config = _dev_config(tmp_path)
    config.storage_data_path.mkdir(parents=True, exist_ok=True)

    with (
        patch("ato_service.main.create_async_engine_from_url") as create_engine,
        patch("ato_service.main.create_session_factory") as create_session_factory,
    ):
        engine = MagicMock()
        engine.connect = MagicMock()
        engine.dispose = AsyncMock()
        create_engine.return_value = engine

        build_app_from_config(config, dsn=dsn)

        create_engine.assert_not_called()
        create_session_factory.assert_not_called()
        engine.connect.assert_not_called()


def test_build_app_from_config_rejects_non_postgresql_explicit_dsn(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)

    with patch("ato_service.main.create_async_engine_from_url") as create_engine:
        with pytest.raises(DatabaseConfigurationError, match="PostgreSQL is required"):
            build_app_from_config(config, dsn="sqlite:///ato.db")

        create_engine.assert_not_called()


def test_valid_draft_manifest_starts_and_disposes_engine(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    config.storage_data_path.mkdir(parents=True, exist_ok=True)
    manifest_path, _artifact_path = _write_draft_authority_manifest(tmp_path)

    with (
        patch("ato_service.main.create_async_engine_from_url") as create_engine,
        patch("ato_service.main.create_session_factory") as create_session_factory,
    ):
        engine = MagicMock()
        connect_cm = AsyncMock()
        connection = AsyncMock()
        query_result = MagicMock()
        query_result.scalar_one.return_value = 0
        connection.execute = AsyncMock(return_value=query_result)
        connect_cm.__aenter__.return_value = connection
        connect_cm.__aexit__.return_value = None
        engine.connect.return_value = connect_cm
        engine.dispose = AsyncMock()
        create_engine.return_value = engine
        session_factory = MagicMock()
        create_session_factory.return_value = session_factory

        app = build_app_from_config(
            config,
            dsn=POSTGRES_URL,
            authority_manifest_path=manifest_path,
            project_root=tmp_path,
            audit_hmac_key=b"audit-test-key",
        )

        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200

            runtime_state = getattr(app.state, RUNTIME_STATE_ATTR)
            assert isinstance(runtime_state, AppRuntimeState)
            assert runtime_state.config is config
            assert runtime_state.storage_root == config.storage_data_path
            assert runtime_state.authority_manifest_id == "fixture.draft"
            assert runtime_state.snapshot.project_root == tmp_path
            assert runtime_state.session_factory is session_factory
            assert runtime_state.audit_hmac_key == b"audit-test-key"

        create_session_factory.assert_called_once_with(engine)
        engine.dispose.assert_awaited_once()
        runtime_state = getattr(app.state, RUNTIME_STATE_ATTR)
        assert runtime_state.session_factory is None
        assert runtime_state.audit_hmac_key is None


def test_missing_authority_manifest_fails_startup_before_engine_creation(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    missing_manifest = tmp_path / "missing-authority-manifest.json"

    with patch("ato_service.main.create_async_engine_from_url") as create_engine:
        app = build_app_from_config(
            config,
            dsn=POSTGRES_URL,
            authority_manifest_path=missing_manifest,
            project_root=tmp_path,
        )

        with pytest.raises(
            AuthorityManifestVerificationError,
            match="missing or unreadable",
        ):
            with TestClient(app):
                pass

        create_engine.assert_not_called()


def test_schema_invalid_authority_manifest_fails_startup(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    manifest_path = tmp_path / "authority-manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "1.0.0"}),
        encoding="utf-8",
    )

    with patch("ato_service.main.create_async_engine_from_url") as create_engine:
        app = build_app_from_config(
            config,
            dsn=POSTGRES_URL,
            authority_manifest_path=manifest_path,
            project_root=tmp_path,
        )

        with pytest.raises(
            AuthorityManifestVerificationError,
            match="schema validation",
        ):
            with TestClient(app):
                pass

        create_engine.assert_not_called()


def test_digest_invalid_authority_manifest_fails_startup(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    manifest_path, artifact_path = _write_draft_authority_manifest(tmp_path)
    artifact_path.write_bytes(b"x" * artifact_path.stat().st_size)

    with patch("ato_service.main.create_async_engine_from_url") as create_engine:
        app = build_app_from_config(
            config,
            dsn=POSTGRES_URL,
            authority_manifest_path=manifest_path,
            project_root=tmp_path,
        )

        with pytest.raises(
            AuthorityManifestVerificationError,
            match="sha256 does not match",
        ):
            with TestClient(app):
                pass

        create_engine.assert_not_called()


def test_build_app_from_config_disposes_engine_on_lifespan_failure(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)
    config.storage_data_path.mkdir(parents=True, exist_ok=True)
    manifest_path, _artifact_path = _write_draft_authority_manifest(tmp_path)

    with (
        patch("ato_service.main.create_async_engine_from_url") as create_engine,
        patch("ato_service.main.create_session_factory") as create_session_factory,
    ):
        engine = MagicMock()
        engine.dispose = AsyncMock()
        create_engine.return_value = engine
        session_factory = MagicMock()
        create_session_factory.return_value = session_factory

        app = build_app_from_config(
            config,
            dsn=POSTGRES_URL,
            authority_manifest_path=manifest_path,
            project_root=tmp_path,
            audit_hmac_key=b"audit-test-key",
        )

        async def _run_lifespan_with_failure() -> None:
            async with app.router.lifespan_context(app):
                raise RuntimeError("failure during serving")

        with pytest.raises(RuntimeError, match="failure during serving"):
            asyncio.run(_run_lifespan_with_failure())

        engine.dispose.assert_awaited_once()
        runtime_state = getattr(app.state, RUNTIME_STATE_ATTR)
        assert runtime_state.session_factory is None
        assert runtime_state.audit_hmac_key is None


def test_resolve_audit_hmac_key_for_startup_reads_credential_reference(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_bytes = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
    key_file.write_bytes(key_bytes)
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
                "source": "root_owned_file",
                "path": str(key_file.resolve()),
            },
        },
    )

    assert (
        _resolve_audit_hmac_key_for_startup(config, injected_key=None) == key_bytes
    )


def test_resolve_audit_hmac_key_for_startup_injection_precedence(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(b"file-key-should-not-be-read")
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document={
            "schema_version": "1.0.0",
            "runtime_profile": "dev_local",
            "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
                "source": "root_owned_file",
                "path": str(key_file.resolve()),
            },
        },
    )

    with patch("ato_service.main.resolve_runtime_audit_hmac_key") as resolve_mock:
        assert (
            _resolve_audit_hmac_key_for_startup(
                config,
                injected_key=b"injected-audit-key",
            )
            == b"injected-audit-key"
        )
        resolve_mock.assert_not_called()


def test_resolve_audit_hmac_key_for_startup_fails_onprem_without_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onprem_example = ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"
    document = json.loads(onprem_example.read_text(encoding="utf-8"))
    document.pop("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE", None)
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)
    config = RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=Path("/var/ato-packages"),
        document=document,
    )

    with pytest.raises(RuntimeConfigError, match="AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE"):
        _resolve_audit_hmac_key_for_startup(config, injected_key=None)


def test_resolve_audit_hmac_key_for_startup_allows_dev_local_without_reference(
    tmp_path: Path,
) -> None:
    config = _dev_config(tmp_path)

    assert _resolve_audit_hmac_key_for_startup(config, injected_key=None) is None


def test_load_app_from_config_path_builds_app(
    tmp_path: Path,
    dsn_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABASE_DSN_FILE_ENV_VAR, str(dsn_file))
    config_path = _write_runtime_config(tmp_path)
    storage_root = (tmp_path / "data" / "ato-storage").resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    manifest_path, _artifact_path = _write_draft_authority_manifest(tmp_path)

    with patch("ato_service.main.create_async_engine_from_url") as create_engine:
        engine = MagicMock()
        connect_cm = AsyncMock()
        connection = AsyncMock()
        query_result = MagicMock()
        query_result.scalar_one.return_value = 0
        connection.execute = AsyncMock(return_value=query_result)
        connect_cm.__aenter__.return_value = connection
        connect_cm.__aexit__.return_value = None
        engine.connect.return_value = connect_cm
        engine.dispose = AsyncMock()
        create_engine.return_value = engine

        app = load_app_from_config_path(
            config_path,
            base_dir=tmp_path,
            authority_manifest_path=manifest_path,
            project_root=tmp_path,
        )

        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503

        engine.dispose.assert_awaited_once()


def test_main_requires_config_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ato_service.main import main

    monkeypatch.delenv(RUNTIME_CONFIG_PATH_ENV_VAR, raising=False)

    with pytest.raises(SystemExit, match=RUNTIME_CONFIG_PATH_ENV_VAR):
        main([])


def test_create_app_mounts_p1_package_routes_without_runtime() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))
    paths = set(app.openapi()["paths"])

    assert all(not path.startswith("/api/v1") for path in paths)
    assert "/systems" in paths
    assert "/systems/{system_id}/package-revisions" in paths
    assert "/package-revisions/{id}" in paths
    assert "/package-revisions/{id}/files" in paths
    assert "/package-revisions/{id}/finalize" in paths
    assert "/package-revisions/{id}/confirm" in paths
    assert "/package-revisions/{id}/proposals" not in paths
    assert "/health/live" in paths
    assert "/health/ready" in paths


def test_create_app_openapi_avoids_double_api_prefix() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))
    schema = app.openapi()

    assert schema["servers"] == [{"url": "/api/v1"}]
    assert all(not path.startswith("/api/v1") for path in schema["paths"])
    assert "/systems" in schema["paths"]
    assert schema["paths"]["/health/live"]["get"]["servers"] == [
        {
            "url": "/",
            "description": "Application root outside the versioned API base path",
        }
    ]


def test_create_app_description_documents_implemented_p1_subset() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))

    assert "P1.1 Systems + PackageRevision" in app.description
    assert "unimplemented" in app.description.lower()


def test_create_app_exposes_health_and_api_when_runtime_absent() -> None:
    app = create_app(readiness_probe=AsyncMock(return_value={}))

    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200

        api = client.get("/api/v1/systems")
        assert api.status_code == 401
        assert api.headers["content-type"].startswith("application/problem+json")
        assert api.json()["error_code"] == "authentication_required"
