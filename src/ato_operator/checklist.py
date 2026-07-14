"""Operator qualification checklist derived from hard stops and Phase 1 gates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


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
            evidence="docs/AIRGAP_ONBOARDING.md",
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


def format_checklist(items: list[ChecklistItem]) -> str:
    lines = ["ATO operator checklist", ""]
    for item in items:
        lines.append(f"[{item.item_id}] ({item.category}) {item.description}")
        lines.append(f"  status: {item.status}")
        lines.append(f"  evidence: {item.evidence}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["ChecklistItem", "build_operator_checklist", "format_checklist"]
