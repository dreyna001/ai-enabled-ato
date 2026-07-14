"""Tests for validation drill dispatchers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_operator.drill_catalog import list_drill_definitions
from ato_operator.drills.dispatch import run_validation_drill
from ato_operator.drills.types import DrillRunRequest
from ato_service.runtime_config import load_runtime_config_from_dict

ROOT = Path(__file__).resolve().parents[2]
DEV_CONFIG = ROOT / "deployment" / "config" / "runtime-config.dev_local.json"


@pytest.fixture
def dev_config(tmp_path: Path) -> object:
    document = json.loads(DEV_CONFIG.read_text(encoding="utf-8"))
    return load_runtime_config_from_dict(document, base_dir=tmp_path)


def _request(
    drill_id: str,
    config: object,
    *,
    live: bool = False,
    isolated_target: bool = False,
) -> DrillRunRequest:
    return DrillRunRequest(
        drill_id=drill_id,
        config=config,
        project_root=ROOT,
        execution_mode="live" if live else "dry_run",
        operator_identifier="operator@example.local",
        approver_identifier=None,
        isolated_target_confirmed=isolated_target,
        smoke_base_url=None,
        allow_degraded_ready=False,
    )


def test_catalog_lists_documented_drills() -> None:
    drill_ids = {item.drill_id for item in list_drill_definitions()}
    assert "smoke-readiness" in drill_ids
    assert "backup-pitr-restore" in drill_ids
    assert len(drill_ids) >= 13


def test_model_routing_drill_passes_deterministically(dev_config: object) -> None:
    result = run_validation_drill(_request("model-routing-policy-block", dev_config))
    assert result.outcome == "pass"
    assert result.exit_code == 0
    assert all(claim.claim_status != "verified_closed" for claim in result.hard_stop_claims)


def test_smoke_drill_dry_run_skips_live_probe(dev_config: object) -> None:
    result = run_validation_drill(_request("smoke-readiness", dev_config))
    assert result.outcome == "skip"
    assert any(check["name"] == "live_smoke_execution" and check["status"] == "skip" for check in result.results["checks"])


def test_unsupported_drill_id_raises(dev_config: object) -> None:
    with pytest.raises(KeyError):
        run_validation_drill(_request("not-a-real-drill", dev_config))


def test_destructive_live_drill_requires_isolated_target(dev_config: object) -> None:
    result = run_validation_drill(
        _request("worker-crash-recovery", dev_config, live=True, isolated_target=False)
    )
    assert result.outcome == "skip"
    assert result.results["preflight_status"] == "blocked_destructive"


def test_hard_stop_claims_never_close_hs003_in_dry_run(dev_config: object) -> None:
    result = run_validation_drill(_request("oidc-group-mapping", dev_config))
    hs003 = next(item for item in result.hard_stop_claims if item.hard_stop_id == "HS-003")
    assert hs003.claim_status in {"not_claimed", "still_open", "blocked"}


def test_disk_thresholds_drill_runs_read_only_preflight(dev_config: object) -> None:
    result = run_validation_drill(_request("disk-thresholds", dev_config))
    assert result.outcome in {"pass", "fail", "skip", "invalid"}
    assert "disk_thresholds" in json.dumps(result.results)
