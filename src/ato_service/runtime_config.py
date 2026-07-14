"""Load and validate non-secret runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from ato_service.audit import AuditUnavailableError, require_audit_hmac_key
from ato_service.credentials import (
    CredentialResolutionError,
    resolve_secret_bytes_from_credential_reference,
)
from ato_service.db.dsn import (
    DatabaseDsnError,
    resolve_database_dsn_from_credential_reference,
)
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from jsonschema.exceptions import SchemaError

_DEFAULT_DEV_STORAGE_PATH = "/data/ato-storage"
_SCHEMA_ABSOLUTE_PATH = re.compile(r"^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")
_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:password|passwd|secret|api[_-]?key|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer)(?:$|_)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"),
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
)
_CREDENTIAL_REFERENCE_KEYS = frozenset(
    {
        "DATABASE_DSN_CREDENTIAL_REFERENCE",
        "TEXT_MODEL_CREDENTIAL_REFERENCE",
        "VISION_MODEL_CREDENTIAL_REFERENCE",
        "OIDC_CLIENT_CREDENTIAL_REFERENCE",
        "SAML_PROXY_AUTH_CREDENTIAL_REFERENCE",
        "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE",
        "BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE",
    }
)
_SAFE_CONFIGURATION_KEYS = frozenset(
    {
        "LOCAL_PASSWORD_AUTH_ENABLED",
    }
)
_FORMAT_CHECKER = FormatChecker()
_DEFAULT_ENDPOINT_PORTS = {"http": 80, "https": 443}
_IPV4_HOST_PATTERN = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_MODEL_ENDPOINT_URL_FIELDS = (
    "TEXT_MODEL_ENDPOINT_URL",
    "VISION_MODEL_ENDPOINT_URL",
)
_MODEL_ENDPOINT_PROFILE_FIELDS = (
    "TEXT_MODEL_ENDPOINT_PROFILE",
    "VISION_MODEL_ENDPOINT_PROFILE",
)
_TEXT_MODEL_PROVIDERS = frozenset({"openai_compatible", "aws_bedrock"})


class RuntimeConfigError(ValueError):
    """Base error for runtime configuration loading."""


class RuntimeConfigValidationError(RuntimeConfigError):
    """Raised when runtime configuration fails JSON Schema validation."""


class RuntimeConfigSecretError(RuntimeConfigError):
    """Raised when runtime configuration contains obvious secret material."""


class RuntimeConfigPathError(RuntimeConfigError):
    """Raised when runtime configuration contains an invalid storage path."""


_DEFAULT_MAX_MODEL_CALLS_PER_RUN = 120
_DEFAULT_MAX_PACKAGE_BYTES = 2_147_483_648
_DEFAULT_MAX_SINGLE_FILE_BYTES = 104_857_600
_DEFAULT_MAX_FILES_PER_REVISION = 500

_DOMAIN_MAX_MODEL_CALLS_PER_RUN = 120
_DOMAIN_MAX_SINGLE_FILE_BYTES = 104_857_600
_DOMAIN_MAX_FILES_PER_REVISION = 500


_DEFAULT_APPROVAL_EXPIRY_DAYS = 7


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Immutable configured limits resolved from runtime configuration."""

    max_model_calls_per_run: int
    max_package_bytes: int
    max_single_file_bytes: int
    max_files_per_revision: int
    approval_expiry_days: int


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    runtime_profile: str
    storage_data_path: Path
    document: dict[str, Any]

    @property
    def limits(self) -> RuntimeLimits:
        """Return validated configured limits with published defaults applied."""
        return _resolve_runtime_limits(self.document)

    @property
    def extraction_limits(self):
        """Return validated extraction limits with published defaults applied."""
        from ato_service.extraction.limits import resolve_extraction_limits

        return resolve_extraction_limits(self.document)

    @property
    def vision_model_enabled(self) -> bool:
        """Return whether vision model use is enabled; absent values are false."""
        value = self.document.get("VISION_MODEL_ENABLED")
        if value is None:
            return False
        if not isinstance(value, bool):
            raise RuntimeConfigValidationError("VISION_MODEL_ENABLED must be a boolean")
        return value

    @property
    def text_model_provider(self) -> str:
        """Return configured text-model provider; absent values use openai_compatible."""
        provider = self.document.get("TEXT_MODEL_PROVIDER", "openai_compatible")
        if provider not in _TEXT_MODEL_PROVIDERS:
            raise RuntimeConfigValidationError(
                "TEXT_MODEL_PROVIDER must be openai_compatible or aws_bedrock"
            )
        return provider

    @property
    def installation_customer_enterprise_id(self) -> str:
        """Return the single customer enterprise served by this installation."""
        from ato_service.installation_boundary import (
            resolve_installation_customer_enterprise_id,
        )

        return resolve_installation_customer_enterprise_id(self.document)


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise RuntimeConfigError("Could not locate project root (pyproject.toml not found)")


@cache
def _runtime_config_schema() -> dict[str, Any]:
    schema_path = _find_project_root() / "docs" / "contracts" / "runtime-config.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


@cache
def _runtime_config_validator() -> Draft202012Validator:
    schema = _runtime_config_schema()
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    try:
        validator.check_schema(schema)
    except SchemaError as error:
        raise RuntimeConfigError("Runtime configuration schema is invalid") from error
    return validator


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    if path:
        return f"{path}: {error.message}"
    return error.message


def _validate_runtime_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise RuntimeConfigValidationError("runtime configuration must be a JSON object")

    validator = _runtime_config_validator()
    errors = sorted(validator.iter_errors(document), key=lambda item: item.path)
    if errors:
        raise RuntimeConfigValidationError(_format_validation_error(errors[0]))

    _validate_runtime_limit_ceilings(document)
    _validate_runtime_semantics(document)


def _looks_like_ip_literal(hostname: str) -> bool:
    return bool(_IPV4_HOST_PATTERN.fullmatch(hostname) or ":" in hostname)


def _canonicalize_host_literal(hostname: str, *, field_context: str) -> str:
    if _looks_like_ip_literal(hostname):
        try:
            return str(ipaddress.ip_address(hostname))
        except ValueError as error:
            raise RuntimeConfigValidationError(
                f"{field_context} must be a valid IP literal or DNS hostname"
            ) from error
    return hostname.lower()


def _is_literal_loopback_host(canonical_host: str) -> bool:
    try:
        address = ipaddress.ip_address(canonical_host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return address.is_loopback
    return address == ipaddress.IPv6Address("::1")


def _parse_url_port(field_name: str, parsed: Any, scheme: str) -> int:
    try:
        explicit_port = parsed.port
    except ValueError as error:
        raise RuntimeConfigValidationError(
            f"{field_name} must use a valid port"
        ) from error

    port = explicit_port if explicit_port is not None else _DEFAULT_ENDPOINT_PORTS[scheme]
    if port < 1 or port > 65535:
        raise RuntimeConfigValidationError(f"{field_name} must use a valid port")
    return port


def _parse_model_endpoint_url(field_name: str, raw_url: Any) -> tuple[str, int]:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise RuntimeConfigValidationError(f"{field_name} must be a non-empty URL string")

    parsed = urlsplit(raw_url.strip())
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeConfigValidationError(f"{field_name} must not contain URL userinfo")
    if parsed.query:
        raise RuntimeConfigValidationError(f"{field_name} must not contain a URL query string")
    if parsed.fragment:
        raise RuntimeConfigValidationError(f"{field_name} must not contain a URL fragment")
    if parsed.scheme not in _DEFAULT_ENDPOINT_PORTS:
        raise RuntimeConfigValidationError(
            f"{field_name} must use an http or https URL scheme"
        )
    if not parsed.hostname:
        raise RuntimeConfigValidationError(f"{field_name} must include a host")

    host = _canonicalize_host_literal(parsed.hostname, field_context=field_name)
    port = _parse_url_port(field_name, parsed, parsed.scheme)
    return host, port


def _build_allowlist_index(
    allowlist: Any,
) -> frozenset[tuple[str, int]]:
    if not isinstance(allowlist, list) or not allowlist:
        raise RuntimeConfigValidationError(
            "MODEL_ENDPOINT_ALLOWLIST must be a non-empty array"
        )

    entries: set[tuple[str, int]] = set()
    for index, entry in enumerate(allowlist):
        if not isinstance(entry, dict):
            raise RuntimeConfigValidationError(
                f"MODEL_ENDPOINT_ALLOWLIST[{index}] must be an object"
            )
        host = entry.get("host")
        port = entry.get("port")
        if not isinstance(host, str) or not host.strip():
            raise RuntimeConfigValidationError(
                f"MODEL_ENDPOINT_ALLOWLIST[{index}].host must be a non-empty string"
            )
        if isinstance(port, bool) or not isinstance(port, int):
            raise RuntimeConfigValidationError(
                f"MODEL_ENDPOINT_ALLOWLIST[{index}].port must be an integer"
            )
        if port < 1 or port > 65535:
            raise RuntimeConfigValidationError(
                f"MODEL_ENDPOINT_ALLOWLIST[{index}].port must be between 1 and 65535"
            )
        normalized_host = _canonicalize_host_literal(
            host.strip(),
            field_context=f"MODEL_ENDPOINT_ALLOWLIST[{index}].host",
        )
        entries.add((normalized_host, port))

    return frozenset(entries)


def _configured_model_endpoint_urls(document: dict[str, Any]) -> list[tuple[str, str]]:
    runtime_profile = document.get("runtime_profile")
    configured: list[tuple[str, str]] = []

    text_url = document.get("TEXT_MODEL_ENDPOINT_URL")
    if isinstance(text_url, str) and document.get(
        "TEXT_MODEL_PROVIDER", "openai_compatible"
    ) != "aws_bedrock":
        configured.append(("TEXT_MODEL_ENDPOINT_URL", text_url))

    vision_url = document.get("VISION_MODEL_ENDPOINT_URL")
    if isinstance(vision_url, str) and (
        document.get("VISION_MODEL_ENABLED") is True
        or runtime_profile == "onprem_production"
    ):
        configured.append(("VISION_MODEL_ENDPOINT_URL", vision_url))

    return configured


def _validate_endpoint_allowlist(document: dict[str, Any]) -> None:
    runtime_profile = document.get("runtime_profile")
    allowlist = document.get("MODEL_ENDPOINT_ALLOWLIST")
    endpoint_urls = _configured_model_endpoint_urls(document)

    if runtime_profile != "onprem_production" and allowlist is None:
        return
    if not endpoint_urls:
        return

    allowlist_index = _build_allowlist_index(allowlist)
    for field_name, raw_url in endpoint_urls:
        host, port = _parse_model_endpoint_url(field_name, raw_url)
        if (host, port) not in allowlist_index:
            raise RuntimeConfigValidationError(
                f"{field_name} host and port must exactly match a "
                "MODEL_ENDPOINT_ALLOWLIST entry"
            )


def _validate_production_endpoint_profiles(document: dict[str, Any]) -> None:
    if document.get("runtime_profile") != "onprem_production":
        return

    for field_name in _MODEL_ENDPOINT_PROFILE_FIELDS:
        profile = document.get(field_name)
        if profile == "mock":
            raise RuntimeConfigValidationError(
                f"{field_name} must not be mock for onprem_production"
            )


def _validate_vision_dependencies(document: dict[str, Any]) -> None:
    if document.get("VISION_MODEL_ENABLED") is not True:
        return

    required_fields = (
        "VISION_MODEL_ENDPOINT_URL",
        "VISION_MODEL_NAME",
        "VISION_MODEL_CONTEXT_TOKENS",
        "VISION_MODEL_ENDPOINT_PROFILE",
    )
    for field_name in required_fields:
        if field_name not in document:
            raise RuntimeConfigValidationError(
                f"{field_name} is required when VISION_MODEL_ENABLED is true"
            )

    if document.get("runtime_profile") == "onprem_production":
        credential = document.get("VISION_MODEL_CREDENTIAL_REFERENCE")
        if not _is_credential_reference(credential):
            raise RuntimeConfigValidationError(
                "VISION_MODEL_CREDENTIAL_REFERENCE is required when "
                "VISION_MODEL_ENABLED is true for onprem_production"
            )


def _validate_model_token_limits(document: dict[str, Any]) -> None:
    text_context = document.get("TEXT_MODEL_CONTEXT_TOKENS")
    text_max_output = document.get("TEXT_MODEL_MAX_OUTPUT_TOKENS")
    if (
        isinstance(text_context, int)
        and not isinstance(text_context, bool)
        and isinstance(text_max_output, int)
        and not isinstance(text_max_output, bool)
        and text_max_output > text_context
    ):
        raise RuntimeConfigValidationError(
            "TEXT_MODEL_MAX_OUTPUT_TOKENS must not exceed TEXT_MODEL_CONTEXT_TOKENS"
        )


def _validate_loopback_http_consistency(document: dict[str, Any]) -> None:
    loopback_opt_in = document.get("ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS") is True

    for field_name in _MODEL_ENDPOINT_URL_FIELDS:
        raw_url = document.get(field_name)
        if not isinstance(raw_url, str) or not raw_url.startswith("http://"):
            continue

        profile_field = field_name.replace("_ENDPOINT_URL", "_ENDPOINT_PROFILE")
        profile = document.get(profile_field)
        if not loopback_opt_in:
            raise RuntimeConfigValidationError(
                f"{field_name} uses http and requires "
                "ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS"
            )
        if profile != "internal_openai_compatible":
            raise RuntimeConfigValidationError(
                f"{profile_field} must be internal_openai_compatible when "
                f"{field_name} uses http"
            )

        host, _ = _parse_model_endpoint_url(field_name, raw_url)
        if not _is_literal_loopback_host(host):
            raise RuntimeConfigValidationError(
                f"{field_name} must use a literal loopback IP address "
                "(IPv4 127.0.0.0/8 or IPv6 ::1), not a DNS hostname"
            )


def _validate_text_model_credentials(document: dict[str, Any]) -> None:
    if document.get("runtime_profile") != "onprem_production":
        return
    if document.get("TEXT_MODEL_PROVIDER", "openai_compatible") == "aws_bedrock":
        return
    if document.get("TEXT_MODEL_ENDPOINT_PROFILE") != "external_openai":
        return

    credential = document.get("TEXT_MODEL_CREDENTIAL_REFERENCE")
    if not _is_credential_reference(credential):
        raise RuntimeConfigValidationError(
            "TEXT_MODEL_CREDENTIAL_REFERENCE is required when "
            "TEXT_MODEL_ENDPOINT_PROFILE is external_openai for onprem_production"
        )


def _text_model_is_configured(document: dict[str, Any]) -> bool:
    provider = document.get("TEXT_MODEL_PROVIDER", "openai_compatible")
    if "TEXT_MODEL_NAME" not in document:
        return False
    if provider == "aws_bedrock":
        region = document.get("AWS_REGION")
        return isinstance(region, str) and bool(region.strip())
    endpoint_url = document.get("TEXT_MODEL_ENDPOINT_URL")
    return isinstance(endpoint_url, str) and bool(endpoint_url.strip())


def _validate_text_model_endpoint_profile(document: dict[str, Any]) -> None:
    if not _text_model_is_configured(document):
        return
    profile = document.get("TEXT_MODEL_ENDPOINT_PROFILE")
    if not isinstance(profile, str) or not profile.strip():
        raise RuntimeConfigValidationError(
            "TEXT_MODEL_ENDPOINT_PROFILE is required when text model is configured"
        )
    if profile == "mock":
        raise RuntimeConfigValidationError(
            "TEXT_MODEL_ENDPOINT_PROFILE must not be mock when text model is configured"
        )


def _validate_text_model_provider(document: dict[str, Any]) -> None:
    provider = document.get("TEXT_MODEL_PROVIDER", "openai_compatible")
    if provider not in _TEXT_MODEL_PROVIDERS:
        raise RuntimeConfigValidationError(
            "TEXT_MODEL_PROVIDER must be openai_compatible or aws_bedrock"
        )
    if provider != "aws_bedrock":
        return

    region = document.get("AWS_REGION")
    if not isinstance(region, str) or not region.strip():
        raise RuntimeConfigValidationError(
            "AWS_REGION is required when TEXT_MODEL_PROVIDER is aws_bedrock"
        )


def _validate_runtime_semantics(document: dict[str, Any]) -> None:
    _validate_text_model_provider(document)
    _validate_text_model_endpoint_profile(document)
    _validate_production_endpoint_profiles(document)
    _validate_vision_dependencies(document)
    _validate_text_model_credentials(document)
    _validate_model_token_limits(document)
    _validate_loopback_http_consistency(document)
    _validate_endpoint_allowlist(document)
    _validate_process_capability_consistency(document)
    _validate_internal_egress_allowlist(document)


def _validate_process_capability_consistency(document: dict[str, Any]) -> None:
    from ato_service.process_capabilities import resolve_process_capabilities

    try:
        capabilities = resolve_process_capabilities(document)
    except RuntimeConfigValidationError:
        raise
    if capabilities is None:
        return

    if capabilities.malware_scanning and document.get("MALWARE_SCANNER_ENABLED") is not True:
        raise RuntimeConfigValidationError(
            "MALWARE_SCANNER_ENABLED must be true when PROCESS_CAPABILITIES.malware_scanning is true"
        )
    if capabilities.vision_model_calls and document.get("VISION_MODEL_ENABLED") is not True:
        raise RuntimeConfigValidationError(
            "VISION_MODEL_ENABLED must be true when PROCESS_CAPABILITIES.vision_model_calls is true"
        )
    if capabilities.oidc_authentication and document.get("IDENTITY_PROVIDER_MODE") != "oidc":
        raise RuntimeConfigValidationError(
            "IDENTITY_PROVIDER_MODE must be oidc when PROCESS_CAPABILITIES.oidc_authentication is true"
        )
    if capabilities.text_model_calls and document.get("runtime_profile") == "onprem_production":
        if document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is not True:
            raise RuntimeConfigValidationError(
                "TEXT_MODEL_ENDPOINT_POLICY_APPROVED must be true when "
                "PROCESS_CAPABILITIES.text_model_calls is true for onprem_production"
            )


def _validate_internal_egress_allowlist(document: dict[str, Any]) -> None:
    if document.get("runtime_profile") != "onprem_production":
        return
    allowlist = document.get("INTERNAL_EGRESS_ALLOWLIST")
    if not isinstance(allowlist, list) or not allowlist:
        raise RuntimeConfigValidationError(
            "INTERNAL_EGRESS_ALLOWLIST must be a non-empty array for onprem_production"
        )
    _build_allowlist_index(allowlist)


def _positive_limit_from_document(
    document: dict[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    raw = document.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RuntimeConfigValidationError(f"{key} must be a positive integer")
    if raw < 1:
        raise RuntimeConfigValidationError(f"{key} must be a positive integer")
    return raw


def _validate_runtime_limit_ceilings(document: dict[str, Any]) -> None:
    max_model_calls = _positive_limit_from_document(
        document,
        "MAX_MODEL_CALLS_PER_RUN",
        default=_DEFAULT_MAX_MODEL_CALLS_PER_RUN,
    )
    if max_model_calls > _DOMAIN_MAX_MODEL_CALLS_PER_RUN:
        raise RuntimeConfigValidationError(
            "MAX_MODEL_CALLS_PER_RUN exceeds domain maximum of "
            f"{_DOMAIN_MAX_MODEL_CALLS_PER_RUN}"
        )

    max_single_file_bytes = _positive_limit_from_document(
        document,
        "MAX_SINGLE_FILE_BYTES",
        default=_DEFAULT_MAX_SINGLE_FILE_BYTES,
    )
    if max_single_file_bytes > _DOMAIN_MAX_SINGLE_FILE_BYTES:
        raise RuntimeConfigValidationError(
            "MAX_SINGLE_FILE_BYTES exceeds content-manifest maximum of "
            f"{_DOMAIN_MAX_SINGLE_FILE_BYTES}"
        )

    max_files_per_revision = _positive_limit_from_document(
        document,
        "MAX_FILES_PER_REVISION",
        default=_DEFAULT_MAX_FILES_PER_REVISION,
    )
    if max_files_per_revision > _DOMAIN_MAX_FILES_PER_REVISION:
        raise RuntimeConfigValidationError(
            "MAX_FILES_PER_REVISION exceeds content-manifest maximum of "
            f"{_DOMAIN_MAX_FILES_PER_REVISION}"
        )


def _resolve_runtime_limits(document: dict[str, Any]) -> RuntimeLimits:
    _validate_runtime_limit_ceilings(document)
    return RuntimeLimits(
        max_model_calls_per_run=_positive_limit_from_document(
            document,
            "MAX_MODEL_CALLS_PER_RUN",
            default=_DEFAULT_MAX_MODEL_CALLS_PER_RUN,
        ),
        max_package_bytes=_positive_limit_from_document(
            document,
            "MAX_PACKAGE_BYTES",
            default=_DEFAULT_MAX_PACKAGE_BYTES,
        ),
        max_single_file_bytes=_positive_limit_from_document(
            document,
            "MAX_SINGLE_FILE_BYTES",
            default=_DEFAULT_MAX_SINGLE_FILE_BYTES,
        ),
        max_files_per_revision=_positive_limit_from_document(
            document,
            "MAX_FILES_PER_REVISION",
            default=_DEFAULT_MAX_FILES_PER_REVISION,
        ),
        approval_expiry_days=_positive_limit_from_document(
            document,
            "APPROVAL_EXPIRY_DAYS",
            default=_DEFAULT_APPROVAL_EXPIRY_DAYS,
        ),
    )


def _is_credential_reference(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    source = value.get("source")
    if source == "systemd_credential":
        return isinstance(value.get("identifier"), str)
    if source == "root_owned_file":
        return isinstance(value.get("path"), str)
    return False


def _scan_for_secrets(value: Any, *, key_path: str = "") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise RuntimeConfigSecretError("runtime configuration keys must be strings")
            nested_path = f"{key_path}.{key}" if key_path else key
            if key in _CREDENTIAL_REFERENCE_KEYS:
                if not _is_credential_reference(nested):
                    raise RuntimeConfigSecretError(
                        f"{nested_path} must be a credential reference object"
                    )
                continue
            if key in _SAFE_CONFIGURATION_KEYS:
                _scan_for_secrets(nested, key_path=nested_path)
                continue
            if _SECRET_KEY_PATTERN.search(key):
                raise RuntimeConfigSecretError(
                    f"{nested_path} uses a secret-bearing field name"
                )
            _scan_for_secrets(nested, key_path=nested_path)
        return

    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_for_secrets(nested, key_path=f"{key_path}[{index}]")
        return

    if isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                raise RuntimeConfigSecretError(
                    f"{key_path or '<root>'} contains secret-like material"
                )


def _normalize_storage_path(raw_path: str) -> str:
    normalized = raw_path.strip()
    if not normalized:
        raise RuntimeConfigPathError("STORAGE_DATA_PATH must not be empty")
    if not _SCHEMA_ABSOLUTE_PATH.fullmatch(normalized):
        raise RuntimeConfigPathError("STORAGE_DATA_PATH must match the schema absolutePath pattern")
    return normalized


def _validate_production_storage_path(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeConfigPathError("STORAGE_DATA_PATH must not be a symlink")

    for component in path.parents:
        if component.is_symlink():
            raise RuntimeConfigPathError(
                "STORAGE_DATA_PATH must not traverse a symlink component"
            )
        if component == component.anchor:
            break


def _resolve_storage_data_path(
    raw_path: str,
    *,
    runtime_profile: str,
    base_dir: Path | None,
) -> Path:
    normalized = _normalize_storage_path(raw_path)

    if runtime_profile == "onprem_production":
        path = Path(normalized)
        if not path.is_absolute():
            raise RuntimeConfigPathError(
                "onprem_production requires an absolute native STORAGE_DATA_PATH"
            )
        _validate_production_storage_path(path)
        return path

    if base_dir is None:
        raise RuntimeConfigPathError(
            "dev_local requires base_dir to resolve STORAGE_DATA_PATH"
        )

    logical_segments = Path(normalized.lstrip("/")).parts
    if not logical_segments or any(segment in {".", ".."} for segment in logical_segments):
        raise RuntimeConfigPathError(
            "dev_local STORAGE_DATA_PATH must resolve within base_dir"
        )

    resolved = (base_dir.joinpath(*logical_segments)).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError as error:
        raise RuntimeConfigPathError(
            "dev_local STORAGE_DATA_PATH must stay within base_dir"
        ) from error
    return resolved


def load_runtime_config_from_dict(
    document: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> RuntimeConfig:
    """Validate a runtime configuration document and resolve storage paths."""
    _validate_runtime_document(document)
    _scan_for_secrets(document)

    runtime_profile = document["runtime_profile"]
    raw_storage_path = document.get("STORAGE_DATA_PATH", _DEFAULT_DEV_STORAGE_PATH)
    if not isinstance(raw_storage_path, str):
        raise RuntimeConfigPathError("STORAGE_DATA_PATH must be a string")

    storage_data_path = _resolve_storage_data_path(
        raw_storage_path,
        runtime_profile=runtime_profile,
        base_dir=base_dir,
    )

    from ato_service.package_rbac import configure_package_role_groups

    configure_package_role_groups(document)

    return RuntimeConfig(
        runtime_profile=runtime_profile,
        storage_data_path=storage_data_path,
        document=document,
    )


def load_runtime_config(
    config_path: Path | str,
    *,
    base_dir: Path | None = None,
) -> RuntimeConfig:
    """Load runtime configuration from a JSON file."""
    path = Path(config_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeConfigValidationError(
            f"{path}: invalid JSON: {error.msg}"
        ) from error

    if not isinstance(raw, dict):
        raise RuntimeConfigValidationError(f"{path}: runtime configuration must be a JSON object")

    if base_dir is not None:
        resolved_base: Path | None = base_dir.resolve()
    elif raw.get("runtime_profile") == "dev_local":
        resolved_base = _find_project_root(path.parent)
    else:
        resolved_base = None

    return load_runtime_config_from_dict(raw, base_dir=resolved_base)


def resolve_runtime_database_dsn(config: RuntimeConfig) -> str:
    """Resolve the PostgreSQL DSN referenced by runtime configuration."""
    reference = config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE")
    if not isinstance(reference, dict):
        raise RuntimeConfigError(
            "DATABASE_DSN_CREDENTIAL_REFERENCE is required to resolve the database DSN"
        )
    enforce_root_owned_file_metadata = config.runtime_profile == "onprem_production"
    try:
        return resolve_database_dsn_from_credential_reference(
            reference,
            enforce_root_owned_file_metadata=enforce_root_owned_file_metadata,
        )
    except DatabaseDsnError as error:
        raise RuntimeConfigError(str(error)) from error


def resolve_runtime_audit_hmac_key(config: RuntimeConfig) -> bytes:
    """Resolve and validate the audit HMAC key referenced by runtime configuration."""
    reference = config.document.get("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE")
    if not isinstance(reference, dict):
        raise RuntimeConfigError(
            "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE is required to resolve the audit HMAC key"
        )
    enforce_root_owned_file_metadata = config.runtime_profile == "onprem_production"
    try:
        key_bytes = resolve_secret_bytes_from_credential_reference(
            reference,
            enforce_root_owned_file_metadata=enforce_root_owned_file_metadata,
        )
        return require_audit_hmac_key(key_bytes)
    except (CredentialResolutionError, AuditUnavailableError) as error:
        raise RuntimeConfigError(str(error)) from error
