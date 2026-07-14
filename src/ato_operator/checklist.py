"""Operator qualification checklist derived from hard stops and capability topology."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ato_operator.capability_topology import (
    DEFAULT_CONFIG_PATH,
    CapabilityTopologyItem,
    build_capability_topology,
)
from ato_operator.migration_contract import EXPECTED_ALEMBIC_HEAD, resolve_alembic_head
from ato_service.runtime_config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    item_id: str
    category: str
    description: str
    status: str
    evidence: str


_HARD_STOP_STATUS_PATTERN = re.compile(r'"status":\s*"([^"]+)"')
_HARD_STOP_BLOCKED_PATTERN = re.compile(r'"blocked_work":\s*"([^"]+)"')


def _load_hard_stops(project_root: Path) -> list[tuple[str, str, str]]:
    path = project_root / "docs" / "requirements" / "hard-stops.yaml"
    text = path.read_text(encoding="utf-8")
    entries: list[tuple[str, str, str]] = []
    for block in text.split('"hard_stop_id"')[1:]:
        stop_id_match = re.search(r':\s*"([^"]+)"', block)
        if not stop_id_match:
            continue
        stop_id = stop_id_match.group(1)
        status_match = _HARD_STOP_STATUS_PATTERN.search(block)
        blocked_match = _HARD_STOP_BLOCKED_PATTERN.search(block)
        status = status_match.group(1) if status_match else "open"
        blocked = blocked_match.group(1) if blocked_match else stop_id
        entries.append((stop_id, status, blocked))
    return entries


def build_operator_checklist(*, project_root: Path) -> list[ChecklistItem]:
    """Return a deterministic onboarding checklist without closing hard stops."""
    items: list[ChecklistItem] = [
        ChecklistItem(
            item_id="CFG-001",
            category="configuration",
            description="Provision /etc/ato-analyzer/runtime-config.json from onprem example",
            status="required",
            evidence="deployment/config/runtime-config.onprem.example.json",
        ),
        ChecklistItem(
            item_id="CFG-002",
            category="configuration",
            description="Populate PROCESS_CAPABILITIES for active processes only",
            status="required",
            evidence="docs/CONFIGURATION.md",
        ),
        ChecklistItem(
            item_id="CFG-003",
            category="configuration",
            description="Declare INTERNAL_EGRESS_ALLOWLIST for IdP, model, and backup targets",
            status="required",
            evidence="docs/CONFIGURATION.md",
        ),
        ChecklistItem(
            item_id="CFG-004",
            category="credentials",
            description="Provision root-owned credential files or systemd LoadCredential mappings",
            status="required",
            evidence="deployment/README.md",
        ),
        ChecklistItem(
            item_id="OPS-001",
            category="operations",
            description="Run ato-operator validate-config and validate-credentials",
            status="required",
            evidence="ato-operator validate-config --config /etc/ato-analyzer/runtime-config.json",
        ),
        ChecklistItem(
            item_id="OPS-002",
            category="operations",
            description="Run ato-operator preflight before first start",
            status="required",
            evidence="ato-operator preflight --config /etc/ato-analyzer/runtime-config.json",
        ),
        ChecklistItem(
            item_id="OPS-003",
            category="operations",
            description="Apply migrations with ato-operator migrate-db / verify-migrations",
            status="required",
            evidence="ato-operator migrate-db --config /etc/ato-analyzer/runtime-config.json",
        ),
        ChecklistItem(
            item_id="OPS-004",
            category="operations",
            description="Run install.sh --migrate --start --smoke or ato-operator smoke",
            status="required",
            evidence="scripts/smoke_service_chain.sh",
        ),
        ChecklistItem(
            item_id="OPS-005",
            category="operations",
            description="Verify audit chain with ato-operator verify-audit when database is available",
            status="recommended",
            evidence="ato-operator verify-audit --config /etc/ato-analyzer/runtime-config.json",
        ),
        ChecklistItem(
            item_id="AIR-001",
            category="airgap",
            description="Stage offline wheels, authority bytes, and ClamAV signatures before cutover",
            status="required",
            evidence="docs/CONFIGURATION.md (Operator CLI section)",
        ),
    ]

    for stop_id, status, blocked in _load_hard_stops(project_root):
        items.append(
            ChecklistItem(
                item_id=stop_id,
                category="hard_stop",
                description=f"Hard stop {stop_id}: {blocked}",
                status=status,
                evidence="docs/requirements/hard-stops.yaml",
            )
        )
    return items


def build_capability_checklist_report(
    config: RuntimeConfig,
    *,
    project_root: Path,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Return machine-readable per-capability onboarding checklist without secret values."""
    topology = build_capability_topology(config, config_path=config_path)
    head = resolve_alembic_head(project_root=project_root)
    return {
        "schema_version": "1.0.0",
        "runtime_profile": config.runtime_profile,
        "config_path": config_path,
        "expected_migration_head": EXPECTED_ALEMBIC_HEAD,
        "repository_migration_head": head,
        "migration_head_matches_contract": head == EXPECTED_ALEMBIC_HEAD,
        "capabilities": [_capability_item_to_dict(item) for item in topology],
        "hard_stops": [
            {
                "hard_stop_id": stop_id,
                "status": status,
                "blocked_work": blocked,
            }
            for stop_id, status, blocked in _load_hard_stops(project_root)
        ],
        "global_verification_commands": [
            f"ato-operator print-checklist --config {config_path} --json",
            f"ato-operator verify-migrations --config {config_path} --dry-run",
            "sudo bash scripts/install.sh --dry-run",
            "sudo bash scripts/verify_backup_contract.sh",
        ],
        "live_validation_pending": [
            "RHEL 9 systemd install/start on customer host",
            "PostgreSQL TLS and backup/restore drill execution",
            "nginx TLS promotion and edge smoke",
        ],
    }


def _capability_item_to_dict(item: CapabilityTopologyItem) -> dict[str, Any]:
    payload = asdict(item)
    payload["credentials"] = [
        {
            "config_field": cred.config_field,
            "identifier": cred.identifier,
            "systemd_units": list(cred.systemd_units),
        }
        for cred in item.credentials
    ]
    return payload


def format_checklist(items: list[ChecklistItem]) -> str:
    lines = ["ATO operator checklist", ""]
    for item in items:
        lines.append(f"[{item.item_id}] ({item.category}) {item.description}")
        lines.append(f"  status: {item.status}")
        lines.append(f"  evidence: {item.evidence}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_capability_checklist(report: dict[str, Any]) -> str:
    lines = [
        "ATO per-capability onboarding checklist",
        "",
        f"runtime_profile: {report['runtime_profile']}",
        f"config_path: {report['config_path']}",
        (
            "migration_head: "
            f"{report['repository_migration_head']} "
            f"(expected {report['expected_migration_head']})"
        ),
        "",
    ]
    for item in report["capabilities"]:
        state = "enabled" if item["enabled"] else "disabled"
        lines.append(f"[{item['capability']}] {state}")
        if item["process"]:
            lines.append(f"  process: {item['process']}")
        if item["systemd_unit"]:
            lines.append(f"  systemd_unit: {item['systemd_unit']}")
        if item["credentials"]:
            lines.append("  credentials:")
            for cred in item["credentials"]:
                units = ", ".join(cred["systemd_units"]) or "(operator-provisioned)"
                lines.append(
                    f"    - {cred['config_field']} -> {cred['identifier']} ({units})"
                )
        if item["endpoints"]:
            lines.append("  endpoints:")
            for endpoint in item["endpoints"]:
                lines.append(f"    - {endpoint}")
        if item["allowlists"]:
            lines.append("  allowlists:")
            for allowlist in item["allowlists"]:
                lines.append(f"    - {allowlist}")
        if item["hard_stops"]:
            lines.append(f"  hard_stops: {', '.join(item['hard_stops'])}")
        if item["verification_commands"]:
            lines.append("  verification_commands:")
            for command in item["verification_commands"]:
                lines.append(f"    - {command}")
        lines.append(f"  notes: {item['notes']}")
        lines.append("")

    lines.append("Global verification commands:")
    for command in report["global_verification_commands"]:
        lines.append(f"  - {command}")
    lines.append("")
    lines.append("Live validation explicitly pending:")
    for blocker in report["live_validation_pending"]:
        lines.append(f"  - {blocker}")
    lines.append("")
    lines.append("Open hard stops:")
    for entry in report["hard_stops"]:
        lines.append(
            f"  - {entry['hard_stop_id']}: {entry['status']} ({entry['blocked_work']})"
        )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ChecklistItem",
    "build_capability_checklist_report",
    "build_operator_checklist",
    "format_capability_checklist",
    "format_checklist",
]
