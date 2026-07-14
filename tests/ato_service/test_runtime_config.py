"""Tests for runtime configuration loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ato_service.audit import MIN_AUDIT_HMAC_KEY_BYTES
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    RuntimeConfigPathError,
    RuntimeConfigSecretError,
    RuntimeConfigValidationError,
    RuntimeLimits,
    _parse_model_endpoint_url,
    _resolve_storage_data_path,
    load_runtime_config,
    load_runtime_config_from_dict,
    resolve_runtime_audit_hmac_key,
    resolve_runtime_database_dsn,
)

ROOT = Path(__file__).resolve().parents[2]
DEV_CONFIG_PATH = ROOT / "deployment" / "config" / "runtime-config.dev_local.json"
ONPREM_EXAMPLE_PATH = ROOT / "deployment" / "config" / "runtime-config.onprem.example.json"
LOOPBACK_DEV_FIXTURE_PATH = (
    ROOT / "docs" / "contracts" / "fixtures" / "runtime-config.valid.loopback-development.json"
)


@pytest.fixture
def production_native_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat POSIX production storage paths as native during Windows test runs."""
    monkeypatch.setattr(Path, "is_absolute", lambda self: True)


def test_load_valid_dev_config_resolves_storage_path(tmp_path: Path) -> None:
    config = load_runtime_config(DEV_CONFIG_PATH, base_dir=tmp_path)

    assert config.runtime_profile == "dev_local"
    assert config.storage_data_path == (tmp_path / "data" / "ato-storage").resolve()
    assert config.document["schema_version"] == "1.0.0"


def test_rejects_unknown_top_level_field() -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "unexpected_field": True,
    }

    with pytest.raises(RuntimeConfigValidationError, match="unexpected_field"):
        load_runtime_config_from_dict(document, base_dir=Path("/tmp/base"))


def test_rejects_malformed_schema_version() -> None:
    document = {
        "schema_version": "2.0.0",
        "runtime_profile": "dev_local",
    }

    with pytest.raises(RuntimeConfigValidationError, match="schema_version"):
        load_runtime_config_from_dict(document, base_dir=Path("/tmp/base"))


def test_rejects_production_relative_storage_path() -> None:
    with pytest.raises(RuntimeConfigPathError, match="absolutePath"):
        _resolve_storage_data_path(
            "var/ato-packages",
            runtime_profile="onprem_production",
            base_dir=None,
        )


def test_rejects_production_non_native_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "is_absolute", lambda self: False)

    with pytest.raises(RuntimeConfigPathError, match="absolute native"):
        _resolve_storage_data_path(
            "/var/ato-packages",
            runtime_profile="onprem_production",
            base_dir=None,
        )


def test_rejects_secret_bearing_value_in_model_name(tmp_path: Path) -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "TEXT_MODEL_NAME": "sk-123456789012345678901234567890",
    }

    with pytest.raises(RuntimeConfigSecretError, match="secret-like"):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def test_secret_scan_allows_credential_reference_objects(tmp_path: Path) -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "TEXT_MODEL_CREDENTIAL_REFERENCE": {
            "source": "systemd_credential",
            "identifier": "text-model",
        },
    }

    config = load_runtime_config_from_dict(document, base_dir=tmp_path)

    assert config.runtime_profile == "dev_local"


def test_dev_local_requires_base_dir_for_default_storage_path() -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
    }

    with pytest.raises(RuntimeConfigPathError, match="base_dir"):
        load_runtime_config_from_dict(document, base_dir=None)


def test_secret_scan_allows_database_dsn_credential_reference(tmp_path: Path) -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "DATABASE_DSN_CREDENTIAL_REFERENCE": {
            "source": "root_owned_file",
            "path": "/etc/ato/credentials/database-dsn",
        },
    }

    config = load_runtime_config_from_dict(document, base_dir=tmp_path)

    assert config.document["DATABASE_DSN_CREDENTIAL_REFERENCE"]["source"] == "root_owned_file"


def test_resolve_runtime_database_dsn_reads_root_owned_file_reference(
    tmp_path: Path,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    postgres_url = "postgresql+asyncpg://ato:secret@localhost:5432/ato"
    dsn_file.write_text(postgres_url, encoding="utf-8")
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "DATABASE_DSN_CREDENTIAL_REFERENCE": {
            "source": "root_owned_file",
            "path": str(dsn_file.resolve()),
        },
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    assert resolve_runtime_database_dsn(config) == postgres_url


def test_resolve_runtime_database_dsn_requires_credential_reference(
    tmp_path: Path,
) -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    with pytest.raises(RuntimeConfigError, match="DATABASE_DSN_CREDENTIAL_REFERENCE"):
        resolve_runtime_database_dsn(config)


def test_resolve_runtime_database_dsn_enables_metadata_enforcement_for_onprem(
    tmp_path: Path,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    postgres_url = "postgresql+asyncpg://ato:supersecret@localhost:5432/ato"
    dsn_file.write_text(postgres_url, encoding="utf-8")
    document = _minimal_onprem_document()
    reference = {
        "source": "root_owned_file",
        "path": str(dsn_file.resolve()),
    }
    document["DATABASE_DSN_CREDENTIAL_REFERENCE"] = reference
    config = RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=Path("/var/ato-packages"),
        document=document,
    )

    with patch(
        "ato_service.runtime_config.resolve_database_dsn_from_credential_reference",
        return_value=postgres_url,
    ) as resolve_mock:
        assert resolve_runtime_database_dsn(config) == postgres_url
        resolve_mock.assert_called_once_with(
            reference,
            enforce_root_owned_file_metadata=True,
        )


def test_resolve_runtime_database_dsn_omits_metadata_enforcement_for_dev_local(
    tmp_path: Path,
) -> None:
    dsn_file = tmp_path / "ato.dsn"
    postgres_url = "postgresql+asyncpg://ato:secret@localhost:5432/ato"
    dsn_file.write_text(postgres_url, encoding="utf-8")
    reference = {
        "source": "root_owned_file",
        "path": str(dsn_file.resolve()),
    }
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "DATABASE_DSN_CREDENTIAL_REFERENCE": reference,
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    with patch(
        "ato_service.runtime_config.resolve_database_dsn_from_credential_reference",
        return_value=postgres_url,
    ) as resolve_mock:
        assert resolve_runtime_database_dsn(config) == postgres_url
        resolve_mock.assert_called_once_with(
            reference,
            enforce_root_owned_file_metadata=False,
        )


def test_resolve_runtime_database_dsn_skips_metadata_for_systemd_onprem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ato_service.db.dsn import CREDENTIALS_DIRECTORY_ENV_VAR

    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    credential_file = cred_dir / "database-dsn"
    credential_file.write_text(
        "postgresql+asyncpg://ato:secret@localhost:5432/ato",
        encoding="utf-8",
    )
    monkeypatch.setenv(CREDENTIALS_DIRECTORY_ENV_VAR, str(cred_dir.resolve()))

    document = _minimal_onprem_document()
    document["DATABASE_DSN_CREDENTIAL_REFERENCE"] = {
        "source": "systemd_credential",
        "identifier": "database-dsn",
    }
    config = RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=Path("/var/ato-packages"),
        document=document,
    )

    assert (
        resolve_runtime_database_dsn(config)
        == "postgresql+asyncpg://ato:secret@localhost:5432/ato"
    )


def test_load_runtime_config_rejects_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeConfigValidationError, match="invalid JSON"):
        load_runtime_config(config_path, base_dir=tmp_path)


def _minimal_dev_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
    }
    document.update(overrides)
    return document


def test_limits_apply_published_defaults_when_omitted(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        _minimal_dev_document(),
        base_dir=tmp_path,
    )

    assert config.limits == RuntimeLimits(
        max_model_calls_per_run=120,
        max_package_bytes=2_147_483_648,
        max_single_file_bytes=104_857_600,
        max_files_per_revision=500,
        approval_expiry_days=7,
    )


def test_limits_accept_lower_configured_values(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        _minimal_dev_document(
            MAX_MODEL_CALLS_PER_RUN=10,
            MAX_PACKAGE_BYTES=1_000,
            MAX_SINGLE_FILE_BYTES=512,
            MAX_FILES_PER_REVISION=25,
        ),
        base_dir=tmp_path,
    )

    assert config.limits == RuntimeLimits(
        max_model_calls_per_run=10,
        max_package_bytes=1_000,
        max_single_file_bytes=512,
        max_files_per_revision=25,
        approval_expiry_days=7,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("MAX_MODEL_CALLS_PER_RUN", 121, "domain maximum"),
        ("MAX_SINGLE_FILE_BYTES", 104_857_601, "content-manifest maximum"),
        ("MAX_FILES_PER_REVISION", 501, "content-manifest maximum"),
    ],
)
def test_limits_reject_values_above_domain_ceilings(
    tmp_path: Path,
    field: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(RuntimeConfigValidationError, match=message):
        load_runtime_config_from_dict(
            _minimal_dev_document(**{field: value}),
            base_dir=tmp_path,
        )


def test_vision_model_enabled_defaults_false_when_absent(tmp_path: Path) -> None:
    config = load_runtime_config_from_dict(
        _minimal_dev_document(),
        base_dir=tmp_path,
    )

    assert config.vision_model_enabled is False


def test_vision_model_enabled_accepts_explicit_true(tmp_path: Path) -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=_minimal_dev_document(VISION_MODEL_ENABLED=True),
    )

    assert config.vision_model_enabled is True


def test_vision_model_enabled_rejects_non_boolean(tmp_path: Path) -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=_minimal_dev_document(VISION_MODEL_ENABLED="yes"),
    )

    with pytest.raises(RuntimeConfigValidationError, match="VISION_MODEL_ENABLED"):
        _ = config.vision_model_enabled


def test_limits_property_validates_manually_constructed_config(tmp_path: Path) -> None:
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=_minimal_dev_document(MAX_MODEL_CALLS_PER_RUN=200),
    )

    with pytest.raises(RuntimeConfigValidationError, match="domain maximum"):
        _ = config.limits


def _minimal_onprem_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(ONPREM_EXAMPLE_PATH.read_text(encoding="utf-8"))
    document.update(overrides)
    return document


def test_load_onprem_example_passes_semantic_validation(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(_minimal_onprem_document())

    assert config.runtime_profile == "onprem_production"
    assert config.document["STORAGE_DATA_PATH"] == "/var/ato-packages"
    assert config.storage_data_path == Path("/var/ato-packages")


def test_load_onprem_config_from_external_path_without_base_dir(
    tmp_path: Path,
    production_native_path: None,
) -> None:
    external_dir = tmp_path / "external-etc" / "ato-analyzer"
    external_dir.mkdir(parents=True)
    config_path = external_dir / "runtime-config.json"
    config_path.write_text(
        ONPREM_EXAMPLE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.runtime_profile == "onprem_production"
    assert config.document["STORAGE_DATA_PATH"] == "/var/ato-packages"
    assert config.storage_data_path == Path("/var/ato-packages")


def test_onprem_production_returns_lexical_absolute_storage_path(
    production_native_path: None,
) -> None:
    resolved = _resolve_storage_data_path(
        "/var/ato-packages",
        runtime_profile="onprem_production",
        base_dir=None,
    )

    assert resolved == Path("/var/ato-packages")
    assert resolved.as_posix() == "/var/ato-packages"


def test_onprem_production_accepts_nonexistent_storage_path(
    production_native_path: None,
) -> None:
    resolved = _resolve_storage_data_path(
        "/var/ato-packages",
        runtime_profile="onprem_production",
        base_dir=None,
    )

    assert resolved == Path("/var/ato-packages")


def test_onprem_production_rejects_symlink_storage_path(
    monkeypatch: pytest.MonkeyPatch,
    production_native_path: None,
) -> None:
    def _is_symlink(self: Path) -> bool:
        return self.as_posix() == "/var/ato-packages"

    monkeypatch.setattr(Path, "is_symlink", _is_symlink)

    with pytest.raises(RuntimeConfigPathError, match="must not be a symlink"):
        _resolve_storage_data_path(
            "/var/ato-packages",
            runtime_profile="onprem_production",
            base_dir=None,
        )


def test_onprem_production_rejects_symlink_parent_component(
    monkeypatch: pytest.MonkeyPatch,
    production_native_path: None,
) -> None:
    def _is_symlink(self: Path) -> bool:
        return self.as_posix() == "/var"

    monkeypatch.setattr(Path, "is_symlink", _is_symlink)

    with pytest.raises(RuntimeConfigPathError, match="symlink component"):
        _resolve_storage_data_path(
            "/var/ato-packages",
            runtime_profile="onprem_production",
            base_dir=None,
        )


def test_dev_local_storage_path_resolution_unchanged(tmp_path: Path) -> None:
    resolved = _resolve_storage_data_path(
        "/data/ato-storage",
        runtime_profile="dev_local",
        base_dir=tmp_path,
    )

    assert resolved == (tmp_path / "data" / "ato-storage").resolve()


def test_load_loopback_development_fixture_passes_semantic_validation(
    tmp_path: Path,
) -> None:
    document = json.loads(LOOPBACK_DEV_FIXTURE_PATH.read_text(encoding="utf-8"))
    config = load_runtime_config_from_dict(document, base_dir=tmp_path)

    assert config.runtime_profile == "dev_local"
    assert config.document["ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS"] is True


def test_dev_local_accepts_mock_endpoint_profile_when_text_model_unconfigured(
    tmp_path: Path,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_dev_document(TEXT_MODEL_ENDPOINT_PROFILE="mock"),
        base_dir=tmp_path,
    )

    assert config.document["TEXT_MODEL_ENDPOINT_PROFILE"] == "mock"


def test_configured_text_model_requires_explicit_endpoint_profile(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigValidationError, match="TEXT_MODEL_ENDPOINT_PROFILE"):
        load_runtime_config_from_dict(
            _minimal_dev_document(
                TEXT_MODEL_ENDPOINT_URL="https://api.openai.com/v1",
                TEXT_MODEL_NAME="gpt-4o-mini",
            ),
            base_dir=tmp_path,
        )


def test_configured_text_model_rejects_mock_endpoint_profile(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigValidationError, match="mock"):
        load_runtime_config_from_dict(
            _minimal_dev_document(
                TEXT_MODEL_ENDPOINT_URL="https://api.openai.com/v1",
                TEXT_MODEL_NAME="gpt-4o-mini",
                TEXT_MODEL_ENDPOINT_PROFILE="mock",
            ),
            base_dir=tmp_path,
        )


def test_onprem_rejects_mock_text_endpoint_profile() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="mock"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(TEXT_MODEL_ENDPOINT_PROFILE="mock")
        )


def test_onprem_rejects_mock_vision_endpoint_profile() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="must not be mock"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(VISION_MODEL_ENDPOINT_PROFILE="mock")
        )


def test_rejects_model_endpoint_url_userinfo() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="userinfo"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                TEXT_MODEL_ENDPOINT_URL="https://user:pass@models.customer.internal/v1"
            )
        )


def test_rejects_model_endpoint_not_in_allowlist() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="MODEL_ENDPOINT_ALLOWLIST"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                TEXT_MODEL_ENDPOINT_URL="https://other.customer.internal/v1"
            )
        )


def test_allowlist_matches_explicit_https_port(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_onprem_document(
            TEXT_MODEL_ENDPOINT_URL="https://models.customer.internal:443/v1",
            VISION_MODEL_ENDPOINT_URL="https://models.customer.internal:443/v1",
        )
    )

    assert config.document["TEXT_MODEL_ENDPOINT_URL"].endswith(":443/v1")


def test_allowlist_matches_hostname_case_insensitively(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_onprem_document(
            TEXT_MODEL_ENDPOINT_URL="https://Models.Customer.Internal/v1",
            VISION_MODEL_ENDPOINT_URL="https://Models.Customer.Internal/v1",
            MODEL_ENDPOINT_ALLOWLIST=[{"host": "models.customer.internal", "port": 443}],
        )
    )

    assert "Models.Customer.Internal" in config.document["TEXT_MODEL_ENDPOINT_URL"]


def test_onprem_vision_enabled_requires_credential_reference() -> None:
    document = _minimal_onprem_document(VISION_MODEL_ENABLED=True)
    document.pop("VISION_MODEL_CREDENTIAL_REFERENCE", None)

    with pytest.raises(
        RuntimeConfigValidationError,
        match="VISION_MODEL_CREDENTIAL_REFERENCE",
    ):
        load_runtime_config_from_dict(document)


def test_onprem_vision_enabled_accepts_credential_reference(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_onprem_document(
            VISION_MODEL_ENABLED=True,
            VISION_MODEL_CREDENTIAL_REFERENCE={
                "source": "systemd_credential",
                "identifier": "vision-model-api-key",
            },
        )
    )

    assert config.vision_model_enabled is True


def test_rejects_text_max_output_tokens_above_context_window() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="TEXT_MODEL_MAX_OUTPUT_TOKENS"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                TEXT_MODEL_CONTEXT_TOKENS=1024,
                TEXT_MODEL_MAX_OUTPUT_TOKENS=2048,
            )
        )


def test_rejects_http_model_endpoint_without_loopback_opt_in(tmp_path: Path) -> None:
    document = json.loads(LOOPBACK_DEV_FIXTURE_PATH.read_text(encoding="utf-8"))
    document.pop("ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS", None)

    with pytest.raises(RuntimeConfigValidationError, match="ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS"):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def test_rejects_http_model_endpoint_with_non_internal_profile(tmp_path: Path) -> None:
    document = json.loads(LOOPBACK_DEV_FIXTURE_PATH.read_text(encoding="utf-8"))
    document["TEXT_MODEL_ENDPOINT_PROFILE"] = "external_openai"

    with pytest.raises(RuntimeConfigValidationError, match="internal_openai_compatible"):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def _loopback_dev_document(**overrides: Any) -> dict[str, Any]:
    document = json.loads(LOOPBACK_DEV_FIXTURE_PATH.read_text(encoding="utf-8"))
    document.update(overrides)
    return document


def test_rejects_remote_http_model_endpoint_despite_loopback_opt_in(
    tmp_path: Path,
) -> None:
    document = _loopback_dev_document(
        TEXT_MODEL_ENDPOINT_URL="http://models.customer.internal/v1",
        MODEL_ENDPOINT_ALLOWLIST=[{"host": "models.customer.internal", "port": 80}],
    )

    with pytest.raises(RuntimeConfigValidationError, match="TEXT_MODEL_ENDPOINT_URL"):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def test_rejects_localhost_http_model_endpoint_despite_loopback_opt_in(
    tmp_path: Path,
) -> None:
    document = _loopback_dev_document(
        TEXT_MODEL_ENDPOINT_URL="http://localhost:8000/v1",
        MODEL_ENDPOINT_ALLOWLIST=[{"host": "localhost", "port": 8000}],
    )

    with pytest.raises(RuntimeConfigValidationError, match="literal loopback IP"):
        load_runtime_config_from_dict(document, base_dir=tmp_path)


def test_accepts_ipv6_loopback_http_endpoint_with_canonical_allowlist_match(
    tmp_path: Path,
) -> None:
    config = load_runtime_config_from_dict(
        _loopback_dev_document(
            TEXT_MODEL_ENDPOINT_URL="http://[::1]:8000/v1",
            MODEL_ENDPOINT_ALLOWLIST=[{"host": "::1", "port": 8000}],
        ),
        base_dir=tmp_path,
    )

    assert config.document["TEXT_MODEL_ENDPOINT_URL"] == "http://[::1]:8000/v1"


def test_rejects_model_endpoint_url_with_malformed_port() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="valid port"):
        _parse_model_endpoint_url(
            "TEXT_MODEL_ENDPOINT_URL",
            "https://models.customer.internal:not-a-port/v1",
        )


def test_rejects_model_endpoint_url_with_out_of_range_port(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigValidationError, match="valid port"):
        load_runtime_config_from_dict(
            _loopback_dev_document(TEXT_MODEL_ENDPOINT_URL="http://127.0.0.1:99999/v1"),
            base_dir=tmp_path,
        )


def test_rejects_model_endpoint_url_with_query_string() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="query string"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                TEXT_MODEL_ENDPOINT_URL="https://models.customer.internal/v1?api_key=secret"
            )
        )


def test_rejects_model_endpoint_url_with_fragment() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="fragment"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                TEXT_MODEL_ENDPOINT_URL="https://models.customer.internal/v1#section"
            )
        )


def test_accepts_model_endpoint_url_with_normal_path(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_onprem_document(
            TEXT_MODEL_ENDPOINT_URL="https://models.customer.internal/v1/chat",
            VISION_MODEL_ENDPOINT_URL="https://models.customer.internal/v1/chat",
        )
    )

    assert config.document["TEXT_MODEL_ENDPOINT_URL"].endswith("/v1/chat")


def test_onprem_external_text_profile_requires_credential_reference() -> None:
    document = _minimal_onprem_document(TEXT_MODEL_ENDPOINT_PROFILE="external_openai")
    document.pop("TEXT_MODEL_CREDENTIAL_REFERENCE", None)

    with pytest.raises(RuntimeConfigValidationError, match="TEXT_MODEL_CREDENTIAL_REFERENCE"):
        load_runtime_config_from_dict(document)


def test_onprem_external_text_profile_accepts_credential_reference(
    production_native_path: None,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_onprem_document(
            TEXT_MODEL_ENDPOINT_PROFILE="external_openai",
            TEXT_MODEL_CREDENTIAL_REFERENCE={
                "source": "systemd_credential",
                "identifier": "text-model-api-key",
            },
        )
    )

    assert config.document["TEXT_MODEL_ENDPOINT_PROFILE"] == "external_openai"


def test_onprem_internal_text_profile_accepts_credentialless_config(
    production_native_path: None,
) -> None:
    document = _minimal_onprem_document(TEXT_MODEL_ENDPOINT_PROFILE="internal_openai_compatible")
    document.pop("TEXT_MODEL_CREDENTIAL_REFERENCE", None)

    config = load_runtime_config_from_dict(document)

    assert "TEXT_MODEL_CREDENTIAL_REFERENCE" not in config.document


def test_rejects_malformed_ipv4_literal_in_allowlist() -> None:
    with pytest.raises(RuntimeConfigValidationError, match="valid IP literal"):
        load_runtime_config_from_dict(
            _minimal_onprem_document(
                MODEL_ENDPOINT_ALLOWLIST=[{"host": "999.999.999.999", "port": 443}]
            )
        )


def test_bedrock_provider_requires_aws_region(tmp_path: Path) -> None:
    with pytest.raises(RuntimeConfigValidationError, match="AWS_REGION"):
        load_runtime_config_from_dict(
            _minimal_dev_document(
                TEXT_MODEL_PROVIDER="aws_bedrock",
                TEXT_MODEL_NAME="anthropic.claude-3-haiku-20240307-v1:0",
            ),
            base_dir=tmp_path,
        )


def test_bedrock_provider_skips_text_endpoint_allowlist_validation(
    tmp_path: Path,
) -> None:
    config = load_runtime_config_from_dict(
        _minimal_dev_document(
            TEXT_MODEL_PROVIDER="aws_bedrock",
            AWS_REGION="us-east-1",
            TEXT_MODEL_NAME="anthropic.claude-3-haiku-20240307-v1:0",
            TEXT_MODEL_MAX_OUTPUT_TOKENS=256,
            TEXT_MODEL_TIMEOUT_SECONDS=30,
            TEXT_MODEL_ENDPOINT_PROFILE="internal_openai_compatible",
        ),
        base_dir=tmp_path,
    )

    assert config.text_model_provider == "aws_bedrock"
    assert "TEXT_MODEL_ENDPOINT_URL" not in config.document


def test_onprem_bedrock_accepts_config_without_text_endpoint_url(
    production_native_path: None,
) -> None:
    document = _minimal_onprem_document(
        TEXT_MODEL_PROVIDER="aws_bedrock",
        AWS_REGION="us-gov-west-1",
        TEXT_MODEL_NAME="anthropic.claude-3-haiku-20240307-v1:0",
    )
    document.pop("TEXT_MODEL_ENDPOINT_URL", None)

    config = load_runtime_config_from_dict(document)

    assert config.text_model_provider == "aws_bedrock"
    assert config.document["AWS_REGION"] == "us-gov-west-1"


def test_resolve_runtime_audit_hmac_key_reads_root_owned_file_reference(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_bytes = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
    key_file.write_bytes(key_bytes)
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
            "source": "root_owned_file",
            "path": str(key_file.resolve()),
        },
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    assert resolve_runtime_audit_hmac_key(config) == key_bytes


def test_resolve_runtime_audit_hmac_key_requires_credential_reference(
    tmp_path: Path,
) -> None:
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    with pytest.raises(RuntimeConfigError, match="AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE"):
        resolve_runtime_audit_hmac_key(config)


def test_resolve_runtime_audit_hmac_key_rejects_short_key(tmp_path: Path) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_file.write_bytes(b"short")
    document = {
        "schema_version": "1.0.0",
        "runtime_profile": "dev_local",
        "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
            "source": "root_owned_file",
            "path": str(key_file.resolve()),
        },
    }
    config = RuntimeConfig(
        runtime_profile="dev_local",
        storage_data_path=tmp_path,
        document=document,
    )

    with pytest.raises(RuntimeConfigError, match="at least 32 bytes"):
        resolve_runtime_audit_hmac_key(config)


def test_resolve_runtime_audit_hmac_key_enables_metadata_enforcement_for_onprem(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "audit-hmac-key"
    key_bytes = b"k" * MIN_AUDIT_HMAC_KEY_BYTES
    key_file.write_bytes(key_bytes)
    document = _minimal_onprem_document()
    reference = {
        "source": "root_owned_file",
        "path": str(key_file.resolve()),
    }
    document["AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE"] = reference
    config = RuntimeConfig(
        runtime_profile="onprem_production",
        storage_data_path=Path("/var/ato-packages"),
        document=document,
    )

    with patch(
        "ato_service.runtime_config.resolve_secret_bytes_from_credential_reference",
        return_value=key_bytes,
    ) as resolve_mock:
        assert resolve_runtime_audit_hmac_key(config) == key_bytes
        resolve_mock.assert_called_once_with(
            reference,
            enforce_root_owned_file_metadata=True,
        )
