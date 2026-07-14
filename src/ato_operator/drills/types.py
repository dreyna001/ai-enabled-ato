"""Customer validation drill execution types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ato_operator.drill_records import HardStopClaim
from ato_service.runtime_config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class DrillRunRequest:
    drill_id: str
    config: RuntimeConfig
    project_root: Path
    execution_mode: str
    operator_identifier: str
    approver_identifier: str | None
    isolated_target_confirmed: bool
    smoke_base_url: str | None
    allow_degraded_ready: bool


@dataclass(frozen=True, slots=True)
class DrillRunResult:
    drill_id: str
    drill_version: str
    outcome: str
    hard_stop_claims: tuple[HardStopClaim, ...]
    results: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    fixture_digest: str | None
    exit_code: int
