"""Process topology projection from validated runtime JSON capability flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ato_service.process_capabilities import ProcessCapabilities, resolve_process_capabilities
from ato_service.runtime_config import (
    RuntimeConfig,
    _build_allowlist_index,
    _configured_model_endpoint_urls,
    _is_credential_reference,
    _parse_model_endpoint_url,
)

SYSTEMD_UNITS = {
    "api": "ato-api.service",
    "intake_worker": "ato-intake-worker.service",
    "analyzer_worker": "ato-analyzer-worker.service",
    "portal_static": "nginx.service (ato-portal.conf.example)",
}

SYSTEMD_LOAD_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "ato-api.service": ("database-dsn", "audit-hmac-key", "oidc-client-secret"),
    "ato-intake-worker.service": ("database-dsn", "audit-hmac-key"),
    "ato-analyzer-worker.service": ("database-dsn", "audit-hmac-key"),
}

OPTIONAL_LOAD_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "ato-api.service": ("text-model-api-key", "vision-model-api-key", "backup-encryption-key"),
    "ato-analyzer-worker.service": ("text-model-api-key", "vision-model-api-key"),
}

CAPABILITY_HARD_STOPS: dict[str, tuple[str, ...]] = {
    "api": ("HS-001", "HS-008"),
    "intake_worker": ("HS-005", "HS-008"),
    "analyzer_worker": ("HS-004", "HS-005", "HS-006", "HS-008"),
    "portal_static": ("HS-003", "HS-008"),
    "malware_scanning": ("HS-005", "HS-008"),
    "text_model_calls": ("HS-004", "HS-006", "HS-008"),
    "vision_model_calls": ("HS-004", "HS-006", "HS-008"),
    "oidc_authentication": ("HS-003", "HS-008"),
    "package_search": ("HS-008", "HS-009"),
    "package_chat": ("HS-004", "HS-006", "HS-008", "HS-009"),
}

DEFAULT_CONFIG_PATH = "/etc/ato-analyzer/runtime-config.json"


@dataclass(frozen=True, slots=True)
class CredentialRequirement:
    config_field: str
    identifier: str
    systemd_units: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityTopologyItem:
    capability: str
    enabled: bool
    process: str | None
    systemd_unit: str | None
    credentials: tuple[CredentialRequirement, ...]
    endpoints: tuple[str, ...]
    allowlists: tuple[str, ...]
    hard_stops: tuple[str, ...]
    verification_commands: tuple[str, ...]
    notes: str


def _credential_identifier(reference: object) -> str | None:
    if not _is_credential_reference(reference):
        return None
    assert isinstance(reference, dict)
    identifier = reference.get("identifier")
    if isinstance(identifier, str) and identifier.strip():
        return identifier.strip()
    path = reference.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def _format_allowlist_entries(document: dict[str, Any], field_name: str) -> tuple[str, ...]:
    raw = document.get(field_name)
    if not isinstance(raw, list) or not raw:
        return ()
    try:
        index = _build_allowlist_index(raw)
    except Exception:
        return ()
    return tuple(f"{host}:{port}" for host, port in sorted(index))


def _format_endpoint_entries(document: dict[str, Any]) -> tuple[str, ...]:
    entries: list[str] = []
    issuer = document.get("OIDC_ISSUER_URL")
    if isinstance(issuer, str) and issuer.strip():
        parsed = urlsplit(issuer.strip())
        if parsed.hostname:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            entries.append(f"OIDC_ISSUER_URL={parsed.hostname}:{port}")
    for field_name, raw_url in _configured_model_endpoint_urls(document):
        host, port = _parse_model_endpoint_url(field_name, raw_url)
        entries.append(f"{field_name}={host}:{port}")
    declaration = document.get("BACKUP_TARGET_DECLARATION")
    if isinstance(declaration, dict):
        host = declaration.get("host")
        port = declaration.get("port")
        protocol = declaration.get("protocol")
        if isinstance(host, str) and isinstance(port, int) and not isinstance(port, bool):
            prefix = protocol if isinstance(protocol, str) and protocol else "backup"
            entries.append(f"BACKUP_TARGET_DECLARATION={prefix}://{host.strip()}:{port}")
    portal_origin = document.get("PORTAL_PUBLIC_ORIGIN")
    if isinstance(portal_origin, str) and portal_origin.strip():
        parsed = urlsplit(portal_origin.strip())
        if parsed.hostname:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            entries.append(f"PORTAL_PUBLIC_ORIGIN={parsed.hostname}:{port}")
    return tuple(sorted(set(entries)))


def _verification_command(command: str, *, config_path: str) -> str:
    if "--config" in command:
        return command.replace(DEFAULT_CONFIG_PATH, config_path)
    return command


def _base_verification_commands(config_path: str) -> tuple[str, ...]:
    return (
        _verification_command(
            f"ato-operator validate-config --config {DEFAULT_CONFIG_PATH}",
            config_path=config_path,
        ),
        _verification_command(
            f"ato-operator validate-credentials --config {DEFAULT_CONFIG_PATH}",
            config_path=config_path,
        ),
        _verification_command(
            f"ato-operator preflight --config {DEFAULT_CONFIG_PATH}",
            config_path=config_path,
        ),
        _verification_command(
            f"ato-operator verify-migrations --config {DEFAULT_CONFIG_PATH} --dry-run",
            config_path=config_path,
        ),
    )


def _collect_capability_credentials(
    capability: str,
    *,
    document: dict[str, Any],
    capabilities: ProcessCapabilities,
) -> tuple[CredentialRequirement, ...]:
    required: list[CredentialRequirement] = []

    def add(field_name: str, identifier: str, units: tuple[str, ...]) -> None:
        required.append(
            CredentialRequirement(
                config_field=field_name,
                identifier=identifier,
                systemd_units=units,
            )
        )

    if capability in {"api", "intake_worker", "analyzer_worker", "package_search", "package_chat"}:
        identifier = _credential_identifier(document.get("DATABASE_DSN_CREDENTIAL_REFERENCE"))
        if identifier:
            units = (
                (SYSTEMD_UNITS["api"],)
                if capability in {"api", "package_search", "package_chat"}
                else (SYSTEMD_UNITS.get(capability, SYSTEMD_UNITS["api"]),)
            )
            add("DATABASE_DSN_CREDENTIAL_REFERENCE", identifier, units)

    if capability in {"api", "intake_worker", "analyzer_worker"}:
        identifier = _credential_identifier(document.get("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE"))
        if identifier:
            unit = SYSTEMD_UNITS.get(capability)
            if unit is not None:
                add("AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE", identifier, (unit,))

    if capability in {"api", "oidc_authentication", "portal_static"} and capabilities.oidc_authentication:
        identifier = _credential_identifier(document.get("OIDC_CLIENT_CREDENTIAL_REFERENCE"))
        if identifier:
            add("OIDC_CLIENT_CREDENTIAL_REFERENCE", identifier, (SYSTEMD_UNITS["api"],))

    if capability in {"text_model_calls", "package_chat"} and capabilities.text_model_calls:
        if document.get("TEXT_MODEL_PROVIDER", "openai_compatible") != "aws_bedrock":
            if document.get("TEXT_MODEL_ENDPOINT_PROFILE") == "external_openai":
                identifier = _credential_identifier(document.get("TEXT_MODEL_CREDENTIAL_REFERENCE"))
                if identifier:
                    add(
                        "TEXT_MODEL_CREDENTIAL_REFERENCE",
                        identifier,
                        (SYSTEMD_UNITS["api"], SYSTEMD_UNITS["analyzer_worker"]),
                    )

    if capability == "vision_model_calls" and capabilities.vision_model_calls:
        identifier = _credential_identifier(document.get("VISION_MODEL_CREDENTIAL_REFERENCE"))
        if identifier:
            add(
                "VISION_MODEL_CREDENTIAL_REFERENCE",
                identifier,
                (SYSTEMD_UNITS["analyzer_worker"],),
            )

    if capability == "api" and document.get("BACKUP_OFF_HOST_ENABLED") is True:
        identifier = _credential_identifier(
            document.get("BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE")
        )
        if identifier:
            add("BACKUP_ENCRYPTION_KEY_CREDENTIAL_REFERENCE", identifier, ())

    return tuple(required)


def _capability_notes(
    capability: str,
    *,
    enabled: bool,
    credentials: tuple[CredentialRequirement, ...],
) -> str:
    if not enabled:
        return "Capability inactive; related systemd unit should remain disabled."
    if capability == "portal_static":
        return (
            "Promote deployment/nginx/ato-portal.conf.example after TLS provisioning; "
            "nginx is not started by install.sh."
        )
    if capability == "malware_scanning":
        return "Scanner is customer-provided; intake worker invokes configured transport."
    if capability == "intake_worker":
        return "Enable with systemctl only after acceptance tests; drain before maintenance."
    if capability == "analyzer_worker":
        return "Enable with systemctl only after acceptance tests; drain before maintenance."
    missing_mappings = [
        cred.identifier
        for cred in credentials
        if cred.systemd_units
        and any(
            cred.identifier not in SYSTEMD_LOAD_CREDENTIALS.get(unit, ())
            and cred.identifier in OPTIONAL_LOAD_CREDENTIALS.get(unit, ())
            for unit in cred.systemd_units
        )
    ]
    if missing_mappings:
        return (
            "Add LoadCredential mappings for optional identifiers before enabling model capabilities: "
            + ", ".join(sorted(set(missing_mappings)))
        )
    return "Active capability; run verification commands before production claims."


def build_capability_topology(
    config: RuntimeConfig,
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> list[CapabilityTopologyItem]:
    """Return per-capability onboarding topology without secret values."""
    document = config.document
    capabilities = resolve_process_capabilities(document)
    if capabilities is None:
        return []

    endpoint_entries = _format_endpoint_entries(document)
    internal_allowlist = _format_allowlist_entries(document, "INTERNAL_EGRESS_ALLOWLIST")
    model_allowlist = _format_allowlist_entries(document, "MODEL_ENDPOINT_ALLOWLIST")
    base_commands = _base_verification_commands(config_path)

    capability_flags = {
        "api": capabilities.api,
        "intake_worker": capabilities.intake_worker,
        "analyzer_worker": capabilities.analyzer_worker,
        "portal_static": capabilities.portal_static,
        "malware_scanning": capabilities.malware_scanning,
        "text_model_calls": capabilities.text_model_calls,
        "vision_model_calls": capabilities.vision_model_calls,
        "oidc_authentication": capabilities.oidc_authentication,
        "package_search": capabilities.package_search,
        "package_chat": capabilities.package_chat,
    }

    items: list[CapabilityTopologyItem] = []
    for capability, enabled in capability_flags.items():
        process = None
        unit = None
        if capability in SYSTEMD_UNITS:
            unit = SYSTEMD_UNITS[capability]
            if capability == "portal_static":
                process = "nginx static portal + API proxy"
            elif capability == "api":
                process = "ato-service"
            elif capability == "intake_worker":
                process = "ato-intake-worker"
            elif capability == "analyzer_worker":
                process = "ato-analyzer-worker"

        credentials = _collect_capability_credentials(
            capability,
            document=document,
            capabilities=capabilities,
        )
        allowlists: list[str] = []
        if enabled and document.get("runtime_profile") == "onprem_production":
            if internal_allowlist:
                allowlists.append(
                    "INTERNAL_EGRESS_ALLOWLIST: " + ", ".join(internal_allowlist)
                )
            if capability in {"text_model_calls", "vision_model_calls", "package_chat"} and model_allowlist:
                allowlists.append("MODEL_ENDPOINT_ALLOWLIST: " + ", ".join(model_allowlist))

        verification_commands = list(base_commands)
        if enabled:
            if capability in {"api", "portal_static"}:
                verification_commands.append(
                    _verification_command(
                        f"ato-operator smoke --config {DEFAULT_CONFIG_PATH}",
                        config_path=config_path,
                    )
                )
            if capability == "intake_worker":
                verification_commands.append("sudo systemctl status ato-intake-worker.service")
            if capability == "analyzer_worker":
                verification_commands.append("sudo systemctl status ato-analyzer-worker.service")
            if capability in {"package_search", "package_chat"} and enabled:
                verification_commands.append(
                    "ato-operator rebuild-search-index --config "
                    f"{config_path} <package_revision_id>"
                )
            if document.get("BACKUP_OFF_HOST_ENABLED") is True:
                verification_commands.append("sudo bash scripts/verify_backup_contract.sh")

        hard_stops = CAPABILITY_HARD_STOPS.get(capability, ())
        endpoints = endpoint_entries if enabled and capability in {
            "oidc_authentication",
            "text_model_calls",
            "vision_model_calls",
            "portal_static",
            "api",
        } else ()

        items.append(
            CapabilityTopologyItem(
                capability=capability,
                enabled=enabled,
                process=process,
                systemd_unit=unit,
                credentials=credentials,
                endpoints=endpoints,
                allowlists=tuple(allowlists),
                hard_stops=hard_stops,
                verification_commands=tuple(dict.fromkeys(verification_commands)),
                notes=_capability_notes(capability, enabled=enabled, credentials=credentials),
            )
        )
    return items


__all__ = [
    "CapabilityTopologyItem",
    "CredentialRequirement",
    "build_capability_topology",
]
