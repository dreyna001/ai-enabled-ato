"""Deterministic, capability-aware operator preflight checks."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.clamav_scanner import (
    ClamAvConfigurationError,
    ClamAvTransport,
    resolve_clamav_scanner_settings,
)
from ato_service.credentials import (
    CredentialResolutionError,
    resolve_secret_bytes_from_credential_reference,
)
from ato_service.db.dsn import DatabaseDsnError, require_database_dsn_from_env
from ato_service.db.session import probe_database_connectivity
from ato_service.process_capabilities import ProcessCapabilities, resolve_process_capabilities
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    _build_allowlist_index,
    _configured_model_endpoint_urls,
    _is_credential_reference,
    _parse_model_endpoint_url,
    resolve_runtime_database_dsn,
)

PREFLIGHT_TIMEOUT_SECONDS = 10.0
OIDC_DISCOVERY_TIMEOUT_SECONDS = 10.0
CLAMAV_PING_TIMEOUT_SECONDS = 5.0

_CHECK_OK = "ok"
_CHECK_FAIL = "fail"
_CHECK_SKIP = "skip"


@dataclass(frozen=True, slots=True)
class PreflightCheckResult:
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class OperatorPreflightReport:
    checks: tuple[PreflightCheckResult, ...]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": item.name, "status": item.status, "detail": item.detail}
                for item in self.checks
            ],
        }


def _fail(name: str, detail: str) -> PreflightCheckResult:
    return PreflightCheckResult(name=name, status=_CHECK_FAIL, detail=detail)


def _ok(name: str, detail: str = "ok") -> PreflightCheckResult:
    return PreflightCheckResult(name=name, status=_CHECK_OK, detail=detail)


def _skip(name: str, detail: str) -> PreflightCheckResult:
    return PreflightCheckResult(name=name, status=_CHECK_SKIP, detail=detail)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_authority_manifest_path(
    config: RuntimeConfig,
    *,
    project_root: Path,
) -> Path:
    reference = config.document.get("AUTHORITY_MANIFEST_FILE_REFERENCE")
    if isinstance(reference, dict):
        path_raw = reference.get("path")
        if isinstance(path_raw, str) and path_raw.strip():
            return Path(path_raw.strip())
    env_override = os.environ.get("ATO_AUTHORITY_MANIFEST_PATH")
    if isinstance(env_override, str) and env_override.strip():
        return Path(env_override.strip())
    return project_root / "docs" / "contracts" / "authority-manifest.json"


def _verify_file_reference(
    *,
    label: str,
    reference: dict[str, Any] | None,
) -> PreflightCheckResult:
    if reference is None:
        return _skip(label, "not configured")
    if not isinstance(reference, dict):
        return _fail(label, "reference must be an object")
    path_raw = reference.get("path")
    digest_raw = reference.get("expected_sha256")
    if not isinstance(path_raw, str) or not path_raw.strip():
        return _fail(label, "path is required")
    if not isinstance(digest_raw, str) or len(digest_raw) != 64:
        return _fail(label, "expected_sha256 must be a 64-character hex digest")
    path = Path(path_raw.strip())
    if not path.is_file():
        return _fail(label, f"file not found: {path}")
    actual = _hash_file(path)
    if actual != digest_raw.lower():
        return _fail(label, "digest mismatch")
    return _ok(label, f"digest verified ({path.name})")


def _check_disk_thresholds(config: RuntimeConfig) -> PreflightCheckResult:
    storage_root = config.storage_data_path
    if not storage_root.is_dir():
        return _fail("disk_thresholds", "storage path is not a directory")
    usage = shutil.disk_usage(storage_root)
    if usage.total <= 0:
        return _fail("disk_thresholds", "storage filesystem reported zero capacity")
    used_percent = (usage.used / usage.total) * 100.0
    warning = config.document.get("STORAGE_WARNING_PERCENT", 80)
    rejection = config.document.get("STORAGE_REJECTION_PERCENT", 90)
    if used_percent >= rejection:
        return _fail(
            "disk_thresholds",
            f"storage use {used_percent:.1f}% meets rejection threshold {rejection}%",
        )
    if used_percent >= warning:
        return PreflightCheckResult(
            name="disk_thresholds",
            status=_CHECK_OK,
            detail=f"warning: storage use {used_percent:.1f}% exceeds {warning}%",
        )
    return _ok("disk_thresholds", f"storage use {used_percent:.1f}%")


def _check_storage_write(config: RuntimeConfig) -> PreflightCheckResult:
    storage_root = config.storage_data_path
    temp_dir = storage_root / "_tmp"
    probe_path: Path | None = None
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        probe_path = temp_dir / f"preflight-{secrets.token_hex(8)}"
        with probe_path.open("xb") as probe_file:
            probe_file.write(b"x")
            probe_file.flush()
            os.fsync(probe_file.fileno())
        return _ok("storage_write")
    except (OSError, PermissionError) as exc:
        return _fail("storage_write", f"write probe failed: {exc.__class__.__name__}")
    finally:
        if probe_path is not None and probe_path.exists():
            try:
                probe_path.unlink()
            except OSError:
                pass


def _credential_reference_label(reference: dict[str, Any]) -> str:
    source = reference.get("source")
    if source == "systemd_credential":
        identifier = reference.get("identifier")
        return f"systemd:{identifier}" if isinstance(identifier, str) else "systemd:<invalid>"
    if source == "root_owned_file":
        path = reference.get("path")
        return f"file:{path}" if isinstance(path, str) else "file:<invalid>"
    return "credential:<invalid>"


def _check_credential_reference(
    *,
    name: str,
    reference: object,
    enforce_root: bool,
) -> PreflightCheckResult:
    if not _is_credential_reference(reference):
        return _fail(name, "credential reference is missing or malformed")
    assert isinstance(reference, dict)
    label = _credential_reference_label(reference)
    try:
        secret_bytes = resolve_secret_bytes_from_credential_reference(
            reference,
            enforce_root_owned_file_metadata=enforce_root,
        )
    except CredentialResolutionError as exc:
        return _fail(name, f"{label}: {exc}")
    return _ok(name, f"{label}: {len(secret_bytes)} bytes readable")


def _collect_required_credentials(
    config: RuntimeConfig,
    capabilities: ProcessCapabilities | None,
) -> list[tuple[str, str]]:
    document = config.document
    required: list[tuple[str, str]] = []
    caps = capabilities
    if caps is None or caps.requires_database():
        required.append(("DATABASE_DSN_CREDENTIAL_REFERENCE", "database_dsn"))
    if caps is None or caps.requires_audit_credentials():
        required.append(("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE", "audit_hmac_key"))
    if caps is not None and caps.oidc_authentication:
        required.append(("OIDC_CLIENT_CREDENTIAL_REFERENCE", "oidc_client_secret"))
    if caps is not None and caps.text_model_calls:
        if document.get("TEXT_MODEL_PROVIDER", "openai_compatible") != "aws_bedrock":
            if document.get("TEXT_MODEL_ENDPOINT_PROFILE") == "external_openai":
                required.append(("TEXT_MODEL_CREDENTIAL_REFERENCE", "text_model_api_key"))
    if caps is not None and caps.vision_model_calls:
        required.append(("VISION_MODEL_CREDENTIAL_REFERENCE", "vision_model_api_key"))
    if document.get("BACKUP_OFF_HOST_ENABLED") is True:
        required.append(("BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE", "backup_encryption_key"))
    return required


def _check_backup_declaration(config: RuntimeConfig) -> PreflightCheckResult:
    document = config.document
    if document.get("BACKUP_OFF_HOST_ENABLED") is not True:
        return _skip("backup_declaration", "off-host backup disabled")
    declaration = document.get("BACKUP_TARGET_DECLARATION")
    if not isinstance(declaration, dict):
        return _fail("backup_declaration", "BACKUP_TARGET_DECLARATION is required")
    protocol = declaration.get("protocol")
    host = declaration.get("host")
    port = declaration.get("port")
    export_path = declaration.get("export_path")
    if not isinstance(protocol, str) or not protocol:
        return _fail("backup_declaration", "protocol is required")
    if not isinstance(host, str) or not host.strip():
        return _fail("backup_declaration", "host is required")
    if isinstance(port, bool) or not isinstance(port, int):
        return _fail("backup_declaration", "port must be an integer")
    if not isinstance(export_path, str) or not export_path.startswith("/"):
        return _fail("backup_declaration", "export_path must be an absolute path")
    allowlist = document.get("INTERNAL_EGRESS_ALLOWLIST")
    if isinstance(allowlist, list):
        try:
            index = _build_allowlist_index(allowlist)
            host_key = host.strip().lower()
            if (host_key, port) not in index:
                return _fail(
                    "backup_declaration",
                    "backup target host:port must appear in INTERNAL_EGRESS_ALLOWLIST",
                )
        except RuntimeConfigError as exc:
            return _fail("backup_declaration", str(exc))
    return _ok("backup_declaration", f"{protocol}://{host}:{port}{export_path}")


def _check_internal_egress_allowlist(config: RuntimeConfig) -> PreflightCheckResult:
    allowlist = config.document.get("INTERNAL_EGRESS_ALLOWLIST")
    if not isinstance(allowlist, list) or not allowlist:
        return _fail("internal_egress_allowlist", "INTERNAL_EGRESS_ALLOWLIST is required")
    try:
        index = _build_allowlist_index(allowlist)
    except RuntimeConfigError as exc:
        return _fail("internal_egress_allowlist", str(exc))

    missing: list[str] = []
    issuer = config.document.get("OIDC_ISSUER_URL")
    if isinstance(issuer, str) and issuer.strip():
        host, port = _parse_model_endpoint_url("OIDC_ISSUER_URL", issuer)
        if (host, port) not in index:
            missing.append(f"OIDC issuer {host}:{port}")

    for field_name, raw_url in _configured_model_endpoint_urls(config.document):
        host, port = _parse_model_endpoint_url(field_name, raw_url)
        if (host, port) not in index:
            missing.append(f"{field_name} {host}:{port}")

    declaration = config.document.get("BACKUP_TARGET_DECLARATION")
    if isinstance(declaration, dict):
        host = declaration.get("host")
        port = declaration.get("port")
        if isinstance(host, str) and isinstance(port, int) and not isinstance(port, bool):
            host_key = host.strip().lower()
            if (host_key, port) not in index:
                missing.append(f"backup target {host_key}:{port}")

    if missing:
        return _fail(
            "internal_egress_allowlist",
            "missing allowlist entries: " + ", ".join(sorted(missing)),
        )
    return _ok("internal_egress_allowlist", f"{len(index)} entries")


def _check_model_policy(config: RuntimeConfig, capabilities: ProcessCapabilities | None) -> PreflightCheckResult:
    if capabilities is not None and not (
        capabilities.text_model_calls or capabilities.vision_model_calls
    ):
        return _skip("model_endpoint_policy", "model capabilities inactive")
    document = config.document
    if capabilities is not None and capabilities.text_model_calls:
        if document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is not True:
            return _fail(
                "model_endpoint_policy",
                "TEXT_MODEL_ENDPOINT_POLICY_APPROVED must be true when text_model_calls is active",
            )
    endpoint_urls = _configured_model_endpoint_urls(document)
    if not endpoint_urls:
        return _skip("model_endpoint_policy", "no model endpoints configured")
    try:
        allowlist = document.get("MODEL_ENDPOINT_ALLOWLIST")
        allow_index = _build_allowlist_index(allowlist)
        for field_name, raw_url in endpoint_urls:
            host, port = _parse_model_endpoint_url(field_name, raw_url)
            if (host, port) not in allow_index:
                return _fail(
                    "model_endpoint_policy",
                    f"{field_name} host:port not in MODEL_ENDPOINT_ALLOWLIST",
                )
    except RuntimeConfigError as exc:
        return _fail("model_endpoint_policy", str(exc))
    return _ok("model_endpoint_policy")


def _clamav_ping(settings_host: str | None, settings_port: int | None, socket_path: Path | None, transport: ClamAvTransport, timeout: float) -> None:
    if transport == ClamAvTransport.UNIX_SOCKET:
        assert socket_path is not None
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(str(socket_path))
    else:
        assert settings_host is not None and settings_port is not None
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect((settings_host, settings_port))
    try:
        client.sendall(b"zPING\0")
        response = client.recv(64)
    finally:
        client.close()
    if b"PONG" not in response:
        raise OSError("unexpected ClamAV ping response")


def _check_clamav(config: RuntimeConfig) -> PreflightCheckResult:
    try:
        settings = resolve_clamav_scanner_settings(config)
    except ClamAvConfigurationError as exc:
        return _fail("clamav_ping", str(exc))
    try:
        _clamav_ping(
            settings.host,
            settings.port,
            settings.socket_path,
            settings.transport,
            min(settings.timeout_seconds, CLAMAV_PING_TIMEOUT_SECONDS),
        )
    except OSError:
        return _fail("clamav_ping", "scanner unavailable or ping failed")
    except TimeoutError:
        return _fail("clamav_ping", "scanner ping timed out")
    return _ok("clamav_ping", settings.transport.value)


def _check_oidc_group_mapping(config: RuntimeConfig) -> PreflightCheckResult:
    mapping = config.document.get("OIDC_GROUP_ROLE_MAPPING")
    if not isinstance(mapping, dict) or not mapping:
        return _fail("oidc_group_mapping", "OIDC_GROUP_ROLE_MAPPING is required")
    empty_roles = [
        role
        for role, groups in mapping.items()
        if not isinstance(groups, list) or not groups
    ]
    if empty_roles:
        return _fail(
            "oidc_group_mapping",
            "roles with empty group lists: " + ", ".join(sorted(empty_roles)),
        )
    return _ok("oidc_group_mapping", f"{len(mapping)} roles configured")


def _check_portal_origin_https(config: RuntimeConfig) -> PreflightCheckResult:
    origin = config.document.get("PORTAL_PUBLIC_ORIGIN")
    if not isinstance(origin, str) or not origin.strip():
        return _fail("portal_origin_https", "PORTAL_PUBLIC_ORIGIN is required")
    if not origin.strip().lower().startswith("https://"):
        return _fail("portal_origin_https", "PORTAL_PUBLIC_ORIGIN must use https in production")
    return _ok("portal_origin_https", urlsplit(origin).netloc)


def _check_nginx_tls_certificate_paths(
    config: RuntimeConfig,
    *,
    project_root: Path,
) -> PreflightCheckResult:
    cert_path = config.document.get("NGINX_TLS_CERTIFICATE_PATH")
    key_path = config.document.get("NGINX_TLS_CERTIFICATE_KEY_PATH")
    if cert_path is None and key_path is None:
        portal_template = project_root / "deployment" / "nginx" / "ato-portal.conf"
        if not portal_template.is_file():
            return _skip("nginx_tls_certificates", "portal nginx template unavailable")
        text = portal_template.read_text(encoding="utf-8")
        for directive in ("ssl_certificate ", "ssl_certificate_key "):
            if directive not in text:
                return _fail("nginx_tls_certificates", f"missing {directive.strip()} directive")
        return _ok(
            "nginx_tls_certificates",
            "template declares absolute certificate paths; promote after customer TLS provisioning",
        )
    if not isinstance(cert_path, str) or not cert_path.startswith("/"):
        return _fail("nginx_tls_certificates", "NGINX_TLS_CERTIFICATE_PATH must be an absolute path")
    if not isinstance(key_path, str) or not key_path.startswith("/"):
        return _fail("nginx_tls_certificates", "NGINX_TLS_CERTIFICATE_KEY_PATH must be an absolute path")
    cert = Path(cert_path)
    key = Path(key_path)
    if not cert.is_file():
        return _fail("nginx_tls_certificates", f"certificate file not found: {cert}")
    if not key.is_file():
        return _fail("nginx_tls_certificates", f"certificate key file not found: {key}")
    return _ok("nginx_tls_certificates", cert.name)


def _check_oidc_discovery(config: RuntimeConfig) -> PreflightCheckResult:
    issuer = config.document.get("OIDC_ISSUER_URL")
    if not isinstance(issuer, str) or not issuer.strip():
        return _fail("oidc_discovery", "OIDC_ISSUER_URL is required")
    discovery_url = urljoin(issuer.rstrip("/") + "/", ".well-known/openid-configuration")
    try:
        with httpx.Client(timeout=OIDC_DISCOVERY_TIMEOUT_SECONDS) as client:
            discovery = client.get(discovery_url, headers={"Accept": "application/json"})
            if discovery.status_code != 200:
                return _fail("oidc_discovery", f"discovery HTTP {discovery.status_code}")
            document = discovery.json()
            if not isinstance(document, dict):
                return _fail("oidc_discovery", "discovery document is not an object")
            jwks_uri = document.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri.strip():
                return _fail("oidc_discovery", "jwks_uri missing from discovery document")
            jwks = client.get(jwks_uri.strip(), headers={"Accept": "application/json"})
            if jwks.status_code != 200:
                return _fail("oidc_discovery", f"JWKS HTTP {jwks.status_code}")
            keys = jwks.json().get("keys")
            if not isinstance(keys, list) or not keys:
                return _fail("oidc_discovery", "JWKS keys array is empty")
    except httpx.HTTPError as exc:
        return _fail("oidc_discovery", f"HTTP error: {exc.__class__.__name__}")
    return _ok("oidc_discovery", urlsplit(discovery_url).netloc)


async def _check_database_async(config: RuntimeConfig) -> PreflightCheckResult:
    try:
        if config.runtime_profile == "dev_local" and not isinstance(
            config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE"), dict
        ):
            dsn = require_database_dsn_from_env()
        else:
            dsn = resolve_runtime_database_dsn(config)
    except (RuntimeConfigError, DatabaseDsnError) as exc:
        return _fail("database_connection", str(exc))

    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        await probe_database_connectivity(engine)
    except Exception:
        return _fail("database_connection", "connectivity probe failed")
    finally:
        await engine.dispose()
    return _ok("database_connection")


def _check_migration_head(*, project_root: Path) -> PreflightCheckResult:
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.is_file():
        return _fail("migration_head", f"alembic.ini not found under {project_root}")
    try:
        script = ScriptDirectory.from_config(Config(str(alembic_ini)))
        head = script.get_current_head()
    except Exception as exc:
        return _fail("migration_head", f"alembic script resolution failed: {exc.__class__.__name__}")
    if not head:
        return _fail("migration_head", "no alembic head revision")
    return _ok("migration_head", head)


def _check_authority_manifest(
    config: RuntimeConfig,
    *,
    project_root: Path,
) -> PreflightCheckResult:
    manifest_path = _resolve_authority_manifest_path(config, project_root=project_root)
    try:
        manifest = verify_authority_manifest(manifest_path, project_root=project_root)
    except AuthorityManifestVerificationError as exc:
        return _fail("authority_manifest", str(exc))
    status = manifest.get("status")
    if status != "approved":
        return PreflightCheckResult(
            name="authority_manifest",
            status=_CHECK_OK,
            detail=f"digest valid; status={status} (HS-001 may remain open)",
        )
    return _ok("authority_manifest", "approved")


async def run_operator_preflight(
    config: RuntimeConfig,
    *,
    project_root: Path,
) -> OperatorPreflightReport:
    """Run capability-aware preflight checks with bounded timeouts."""
    document = config.document
    capabilities = resolve_process_capabilities(document)
    enforce_root = config.runtime_profile == "onprem_production"
    checks: list[PreflightCheckResult] = []

    if capabilities is None or capabilities.requires_database():
        checks.append(await _check_database_async(config))
        checks.append(_check_migration_head(project_root=project_root))
    else:
        checks.append(_skip("database_connection", "database capability inactive"))
        checks.append(_skip("migration_head", "database capability inactive"))

    if capabilities is None or capabilities.requires_storage():
        checks.append(_check_storage_write(config))
        checks.append(_check_disk_thresholds(config))
    else:
        checks.append(_skip("storage_write", "storage capability inactive"))
        checks.append(_skip("disk_thresholds", "storage capability inactive"))

    for field_name, check_name in _collect_required_credentials(config, capabilities):
        checks.append(
            _check_credential_reference(
                name=check_name,
                reference=document.get(field_name),
                enforce_root=enforce_root,
            )
        )

    if capabilities is not None and capabilities.malware_scanning:
        checks.append(_check_clamav(config))
    else:
        checks.append(_skip("clamav_ping", "malware_scanning capability inactive"))

    if capabilities is not None and capabilities.oidc_authentication:
        checks.append(_check_oidc_discovery(config))
        if document.get("runtime_profile") == "onprem_production":
            checks.append(_check_oidc_group_mapping(config))
            checks.append(_check_portal_origin_https(config))
    else:
        checks.append(_skip("oidc_discovery", "oidc_authentication capability inactive"))

    if document.get("runtime_profile") == "onprem_production":
        checks.append(_check_internal_egress_allowlist(config))
        checks.append(_check_model_policy(config, capabilities))
        checks.append(_check_backup_declaration(config))
        if capabilities is not None and capabilities.portal_static:
            checks.append(
                _check_nginx_tls_certificate_paths(config, project_root=project_root)
            )
        else:
            checks.append(_skip("nginx_tls_certificates", "portal_static capability inactive"))
        checks.append(
            _verify_file_reference(
                label="authority_manifest_reference",
                reference=document.get("AUTHORITY_MANIFEST_FILE_REFERENCE"),
            )
        )
        checks.append(
            _verify_file_reference(
                label="fisma_template_pack_reference",
                reference=document.get("FISMA_TEMPLATE_PACK_FILE_REFERENCE"),
            )
        )
    else:
        checks.append(_skip("internal_egress_allowlist", "dev_local profile"))
        checks.append(_skip("backup_declaration", "dev_local profile"))

    checks.append(
        _check_authority_manifest(config, project_root=project_root),
    )

    passed = all(item.status in {_CHECK_OK, _CHECK_SKIP} for item in checks)
    return OperatorPreflightReport(checks=tuple(checks), passed=passed)


def run_operator_preflight_sync(
    config: RuntimeConfig,
    *,
    project_root: Path,
) -> OperatorPreflightReport:
    """Synchronous wrapper for operator preflight."""
    return asyncio.run(run_operator_preflight(config, project_root=project_root))


__all__ = [
    "OperatorPreflightReport",
    "PreflightCheckResult",
    "run_operator_preflight",
    "run_operator_preflight_sync",
]
