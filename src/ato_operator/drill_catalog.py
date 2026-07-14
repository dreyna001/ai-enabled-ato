"""Published customer validation drill catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DrillDefinition:
    drill_id: str
    version: str
    title: str
    description: str
    live_required: bool
    destructive: bool
    related_hard_stops: tuple[str, ...]


DEFAULT_HARD_STOP_IDS = ("HS-003", "HS-005", "HS-008")


DRILL_CATALOG: tuple[DrillDefinition, ...] = (
    DrillDefinition(
        drill_id="smoke-readiness",
        version="1.0.0",
        title="Smoke and readiness",
        description="Loopback liveness/readiness smoke via scripts/smoke_service_chain.sh",
        live_required=True,
        destructive=False,
        related_hard_stops=("HS-001", *DEFAULT_HARD_STOP_IDS),
    ),
    DrillDefinition(
        drill_id="audit-chain-verify",
        version="1.0.0",
        title="Audit chain verification",
        description="Ordered HMAC audit chain verification via ato-operator verify-audit",
        live_required=True,
        destructive=False,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
    DrillDefinition(
        drill_id="clamav-eicar",
        version="1.0.0",
        title="ClamAV EICAR detection",
        description="Live EICAR scan through configured local ClamAV adapter",
        live_required=True,
        destructive=False,
        related_hard_stops=("HS-005", *DEFAULT_HARD_STOP_IDS[1:]),
    ),
    DrillDefinition(
        drill_id="clamav-daemon-down",
        version="1.0.0",
        title="ClamAV daemon unavailable",
        description="Verify fail-closed scanner_unavailable behavior when clamd is down",
        live_required=True,
        destructive=False,
        related_hard_stops=("HS-005", *DEFAULT_HARD_STOP_IDS[1:]),
    ),
    DrillDefinition(
        drill_id="oidc-group-mapping",
        version="1.0.0",
        title="Internal OIDC group mapping",
        description="Validate configured OIDC issuer/JWKS and group mapping contract",
        live_required=True,
        destructive=False,
        related_hard_stops=("HS-003", *DEFAULT_HARD_STOP_IDS[1:]),
    ),
    DrillDefinition(
        drill_id="model-routing-policy-block",
        version="1.0.0",
        title="Model routing policy block",
        description="Deterministic zero-call routing denials for blocked labels",
        live_required=False,
        destructive=False,
        related_hard_stops=("HS-004", *DEFAULT_HARD_STOP_IDS),
    ),
    DrillDefinition(
        drill_id="disk-thresholds",
        version="1.0.0",
        title="Disk threshold warnings",
        description="Storage warning/rejection threshold preflight via ato-operator preflight",
        live_required=False,
        destructive=False,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
    DrillDefinition(
        drill_id="worker-crash-recovery",
        version="1.0.0",
        title="Worker crash and recovery",
        description="Lease expiry and idempotent recovery on a live worker host",
        live_required=True,
        destructive=True,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
    DrillDefinition(
        drill_id="backup-declaration",
        version="1.0.0",
        title="Backup declaration contract",
        description="Fail-safe backup declaration checks via scripts/verify_backup_contract.sh",
        live_required=False,
        destructive=False,
        related_hard_stops=("HS-008", "HS-003", "HS-005"),
    ),
    DrillDefinition(
        drill_id="backup-pitr-restore",
        version="1.0.0",
        title="Backup PITR restore",
        description="Isolated-host PostgreSQL/filesystem restore with audit verification",
        live_required=True,
        destructive=True,
        related_hard_stops=("HS-008", "HS-003", "HS-005"),
    ),
    DrillDefinition(
        drill_id="rhel-install",
        version="1.0.0",
        title="RHEL install contract",
        description="Install script presence and bounded dry-run contract validation",
        live_required=True,
        destructive=False,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
    DrillDefinition(
        drill_id="rhel-upgrade",
        version="1.0.0",
        title="RHEL upgrade contract",
        description="Upgrade script bounded flow validation and optional live upgrade",
        live_required=True,
        destructive=True,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
    DrillDefinition(
        drill_id="rhel-rollback",
        version="1.0.0",
        title="RHEL rollback contract",
        description="Rollback script metadata restore contract; database restore remains separate",
        live_required=True,
        destructive=True,
        related_hard_stops=DEFAULT_HARD_STOP_IDS,
    ),
)

DRILL_BY_ID = {item.drill_id: item for item in DRILL_CATALOG}


def get_drill_definition(drill_id: str) -> DrillDefinition:
    definition = DRILL_BY_ID.get(drill_id)
    if definition is None:
        raise KeyError(drill_id)
    return definition


def list_drill_definitions() -> tuple[DrillDefinition, ...]:
    return DRILL_CATALOG
