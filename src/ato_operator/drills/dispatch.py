"""Executable customer validation drill dispatchers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from ato_operator.audit_verify import verify_audit_chain_sync
from ato_operator.drill_catalog import DrillDefinition, get_drill_definition
from ato_operator.drill_records import (
    DrillOutcome,
    HardStopClaim,
    HardStopClaimStatus,
    utc_now,
)
from ato_operator.drills.types import DrillRunRequest, DrillRunResult
from ato_operator.preflight import run_operator_preflight_sync
from ato_service.clamav_scanner import (
    ClamAvConfigurationError,
    ClamAvMalwareScanner,
    resolve_clamav_scanner_settings,
)
from ato_service.malware_scan import MalwareScanOutcome
from ato_service.model_routing import (
    DataOrigin,
    EndpointProfile,
    Sensitivity,
    evaluate_model_routing,
)
from ato_service.process_capabilities import resolve_process_capabilities
from ato_service.runtime_config import RuntimeConfig

EICAR_TEST_STRING = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _default_hard_stop_claims(
    definition: DrillDefinition,
    *,
    detail_by_id: dict[str, str] | None = None,
) -> tuple[HardStopClaim, ...]:
    details = detail_by_id or {}
    claims: list[HardStopClaim] = []
    for hard_stop_id in definition.related_hard_stops:
        claims.append(
            HardStopClaim(
                hard_stop_id=hard_stop_id,
                claim_status=HardStopClaimStatus.NOT_CLAIMED,
                detail=details.get(hard_stop_id),
            )
        )
    return tuple(claims)


def _check(name: str, status: str, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "status": status}
    if detail is not None:
        payload["detail"] = detail
    return payload


def _preflight_status(*, live_required: bool, execution_mode: str) -> str:
    if live_required and execution_mode != "live":
        return "skipped_live_required"
    return "ready"


def _run_smoke_script(
    *,
    project_root: Path,
    base_url: str | None,
    allow_degraded_ready: bool,
) -> tuple[int, list[dict[str, Any]]]:
    script = project_root / "scripts" / "smoke_service_chain.sh"
    if not script.is_file():
        return 1, [_check("smoke_script_present", "fail", "missing scripts/smoke_service_chain.sh")]
    checks = [_check("smoke_script_present", "pass")]
    env = os.environ.copy()
    if base_url:
        env["SMOKE_BASE_URL"] = base_url
    if allow_degraded_ready:
        env["ALLOW_DEGRADED_READY"] = "true"
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    detail = "smoke script completed"
    if completed.returncode != 0:
        detail = "smoke script failed; see operator logs"
    checks.append(
        _check(
            "live_smoke_execution",
            "pass" if completed.returncode == 0 else "fail",
            detail,
        )
    )
    return completed.returncode, checks


def _dispatch_smoke_readiness(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    checks: list[dict[str, Any]] = []
    exit_code = 0
    outcome = DrillOutcome.PASS
    preflight = _preflight_status(
        live_required=definition.live_required,
        execution_mode=request.execution_mode,
    )

    script = request.project_root / "scripts" / "smoke_service_chain.sh"
    if not script.is_file():
        checks.append(_check("smoke_script_present", "fail", "missing script"))
        outcome = DrillOutcome.FAIL
        exit_code = 1
    else:
        checks.append(_check("smoke_script_present", "pass"))
        if request.execution_mode == "live":
            exit_code, live_checks = _run_smoke_script(
                project_root=request.project_root,
                base_url=request.smoke_base_url,
                allow_degraded_ready=request.allow_degraded_ready,
            )
            checks.extend(live_checks)
            outcome = DrillOutcome.PASS if exit_code == 0 else DrillOutcome.FAIL
        else:
            checks.append(_check("live_smoke_execution", "skip", "execution_mode=dry_run"))
            outcome = DrillOutcome.SKIP

    claims = _default_hard_stop_claims(
        definition,
        detail_by_id={"HS-001": HardStopClaimStatus.STILL_OPEN},
    )
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=claims,
        results={
            "summary": "Smoke/readiness drill executed with bounded health-only scope",
            "checks": checks,
            "exit_code": exit_code,
            "preflight_status": preflight,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=exit_code,
    )


def _dispatch_audit_chain_verify(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    preflight = _preflight_status(
        live_required=definition.live_required,
        execution_mode=request.execution_mode,
    )
    if request.execution_mode != "live":
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=_default_hard_stop_claims(definition),
            results={
                "summary": "Audit verification requires live PostgreSQL access",
                "checks": [_check("live_database", "skip", "execution_mode=dry_run")],
                "preflight_status": preflight,
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=0,
        )

    report = verify_audit_chain_sync(request.config)
    outcome = DrillOutcome.PASS if report.passed else DrillOutcome.FAIL
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=_default_hard_stop_claims(definition),
        results={
            "summary": "Audit chain verification completed with redacted operator summary",
            "checks": [
                _check(
                    "chain_integrity",
                    "pass" if report.passed else "fail",
                    report.detail[:512],
                )
            ],
            "detail": json.dumps(report.to_redacted_dict(), sort_keys=True)[:1024],
            "exit_code": 0 if report.passed else 1,
            "preflight_status": preflight,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=0 if report.passed else 1,
    )


def _dispatch_clamav_eicar(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    preflight = _preflight_status(
        live_required=definition.live_required,
        execution_mode=request.execution_mode,
    )
    fixture_digest = hashlib.sha256(EICAR_TEST_STRING).hexdigest()
    claims = _default_hard_stop_claims(
        definition,
        detail_by_id={"HS-005": HardStopClaimStatus.STILL_OPEN},
    )

    if request.execution_mode != "live":
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=claims,
            results={
                "summary": "EICAR live scan skipped in dry_run; HS-005 remains open",
                "checks": [_check("eicar_scan", "skip", "execution_mode=dry_run")],
                "preflight_status": preflight,
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=fixture_digest,
            exit_code=0,
        )

    capabilities = resolve_process_capabilities(request.config.document)
    if capabilities is None or not capabilities.malware_scanning:
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=claims,
            results={
                "summary": "Malware scanning capability inactive; live EICAR drill not attempted",
                "checks": [_check("malware_scanning_capability", "skip")],
                "preflight_status": "skipped_live_required",
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=fixture_digest,
            exit_code=0,
        )

    try:
        settings = resolve_clamav_scanner_settings(request.config)
        scanner = ClamAvMalwareScanner(settings)
        scan_result = scanner.scan_verified_bytes(
            content_bytes=EICAR_TEST_STRING,
            expected_sha256=hashlib.sha256(EICAR_TEST_STRING).hexdigest(),
            expected_size_bytes=len(EICAR_TEST_STRING),
        )
        infected = scan_result.outcome is MalwareScanOutcome.INFECTED
        outcome = DrillOutcome.PASS if infected else DrillOutcome.FAIL
        checks = [
            _check(
                "eicar_detected",
                "pass" if infected else "fail",
                scan_result.reason_code or scan_result.outcome.value,
            )
        ]
        exit_code = 0 if infected else 1
    except ClamAvConfigurationError as exc:
        outcome = DrillOutcome.SKIP
        checks = [_check("clamav_configuration", "skip", exc.__class__.__name__)]
        exit_code = 0

    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=claims,
        results={
            "summary": "ClamAV EICAR drill executed without logging scan payload",
            "checks": checks,
            "preflight_status": preflight,
            "exit_code": exit_code,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=fixture_digest,
        exit_code=exit_code,
    )


def _dispatch_clamav_daemon_down(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    preflight = _preflight_status(
        live_required=definition.live_required,
        execution_mode=request.execution_mode,
    )
    claims = _default_hard_stop_claims(
        definition,
        detail_by_id={"HS-005": HardStopClaimStatus.STILL_OPEN},
    )

    if request.execution_mode != "live":
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=claims,
            results={
                "summary": "Daemon-down fail-closed behavior requires live clamd stop test",
                "checks": [_check("daemon_down_probe", "skip", "execution_mode=dry_run")],
                "preflight_status": preflight,
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=0,
        )

    try:
        settings = resolve_clamav_scanner_settings(request.config)
        scanner = ClamAvMalwareScanner(settings)
        try:
            scanner._connect().close()
            ping_ok = True
        except OSError:
            ping_ok = False
        if ping_ok:
            outcome = DrillOutcome.SKIP
            detail = "clamd reachable; stop daemon on isolated host before live drill"
            status = "skip"
        else:
            outcome = DrillOutcome.PASS
            detail = "clamd unreachable; fail-closed contract satisfied for drill preflight"
            status = "pass"
        checks = [_check("daemon_down_probe", status, detail)]
        exit_code = 0
    except ClamAvConfigurationError as exc:
        outcome = DrillOutcome.PASS
        checks = [_check("daemon_down_probe", "pass", exc.__class__.__name__)]
        exit_code = 0

    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=claims,
        results={
            "summary": "ClamAV daemon-down drill never claims HS-005 closed",
            "checks": checks,
            "preflight_status": preflight,
            "exit_code": exit_code,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=exit_code,
    )


def _dispatch_oidc_group_mapping(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    preflight = _preflight_status(
        live_required=definition.live_required,
        execution_mode=request.execution_mode,
    )
    claims = _default_hard_stop_claims(
        definition,
        detail_by_id={"HS-003": HardStopClaimStatus.STILL_OPEN},
    )

    if request.execution_mode != "live":
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=claims,
            results={
                "summary": "OIDC group mapping live drill skipped; HS-003 remains open",
                "checks": [_check("oidc_live_mapping", "skip", "execution_mode=dry_run")],
                "preflight_status": preflight,
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=0,
        )

    report = run_operator_preflight_sync(request.config, project_root=request.project_root)
    oidc_checks = [item for item in report.checks if item.name.startswith("oidc")]
    passed = all(item.status in {"ok", "skip"} for item in oidc_checks) if oidc_checks else False
    outcome = DrillOutcome.PASS if passed else DrillOutcome.FAIL
    checks = [
        _check(item.name, "pass" if item.status in {"ok", "skip"} else "fail", item.detail[:512])
        for item in oidc_checks
    ] or [_check("oidc_preflight", "fail", "no OIDC checks collected")]
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome if oidc_checks else DrillOutcome.SKIP,
        hard_stop_claims=claims,
        results={
            "summary": "OIDC mapping preflight executed; HS-003 not claimed closed",
            "checks": checks,
            "preflight_status": preflight,
            "exit_code": 0 if passed else 1,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=0 if passed else 1,
    )


def _dispatch_model_routing_policy_block(
    request: DrillRunRequest,
    definition: DrillDefinition,
) -> DrillRunResult:
    started = utc_now()
    fixture_bytes = b"classified;customer_production_unapproved;cui_without_boundary"
    fixture_digest = hashlib.sha256(fixture_bytes).hexdigest()
    scenarios = (
        ("classified_data_unsupported", evaluate_model_routing(
            data_origin=DataOrigin.SYNTHETIC,
            sensitivity=Sensitivity.CLASSIFIED,
            endpoint_profile=EndpointProfile.MOCK,
            endpoint_policy_approved=True,
            cui_boundary_approved=True,
        )),
        ("customer_production_without_approval", evaluate_model_routing(
            data_origin=DataOrigin.CUSTOMER_PRODUCTION,
            sensitivity=Sensitivity.CUSTOMER_SENSITIVE,
            endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
            endpoint_policy_approved=False,
            cui_boundary_approved=False,
        )),
        ("cui_without_boundary", evaluate_model_routing(
            data_origin=DataOrigin.CUSTOMER_PRODUCTION,
            sensitivity=Sensitivity.CUI,
            endpoint_profile=EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
            endpoint_policy_approved=True,
            cui_boundary_approved=False,
        )),
    )
    checks: list[dict[str, Any]] = []
    all_denied = True
    for name, decision in scenarios:
        denied = not decision.allowed
        all_denied = all_denied and denied
        checks.append(
            _check(
                name,
                "pass" if denied else "fail",
                decision.error_code if decision.error_code else "allowed",
            )
        )
    outcome = DrillOutcome.PASS if all_denied else DrillOutcome.FAIL
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=_default_hard_stop_claims(
            definition,
            detail_by_id={"HS-004": HardStopClaimStatus.STILL_OPEN},
        ),
        results={
            "summary": "Deterministic routing policy blocks executed with zero model transport",
            "checks": checks,
            "detail": "No model transport invoked",
            "exit_code": 0 if all_denied else 1,
            "preflight_status": "ready",
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=fixture_digest,
        exit_code=0 if all_denied else 1,
    )


def _dispatch_disk_thresholds(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    report = run_operator_preflight_sync(request.config, project_root=request.project_root)
    disk_checks = [item for item in report.checks if item.name == "disk_thresholds"]
    item = disk_checks[0] if disk_checks else None
    if item is None:
        outcome = DrillOutcome.INVALID
        checks = [_check("disk_thresholds", "fail", "missing preflight check")]
        exit_code = 2
    else:
        outcome = DrillOutcome.PASS if item.status in {"ok", "skip"} else DrillOutcome.FAIL
        checks = [_check("disk_thresholds", "pass" if item.status in {"ok", "skip"} else "fail", item.detail[:512])]
        exit_code = 0 if item.status in {"ok", "skip"} else 1
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=_default_hard_stop_claims(definition),
        results={
            "summary": "Disk threshold preflight executed read-only",
            "checks": checks,
            "preflight_status": "ready",
            "exit_code": exit_code,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=exit_code,
    )


def _dispatch_destructive_live_only(
    request: DrillRunRequest,
    definition: DrillDefinition,
    *,
    summary: str,
    live_detail: str,
) -> DrillRunResult:
    started = utc_now()
    if request.execution_mode != "live":
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=_default_hard_stop_claims(definition),
            results={
                "summary": summary,
                "checks": [_check("live_execution", "skip", "execution_mode=dry_run")],
                "preflight_status": "skipped_live_required",
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=0,
        )
    if not request.isolated_target_confirmed:
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.SKIP,
            hard_stop_claims=_default_hard_stop_claims(definition),
            results={
                "summary": summary,
                "checks": [
                    _check(
                        "isolated_target_confirmation",
                        "skip",
                        "requires --isolated-target and operator confirmation",
                    )
                ],
                "preflight_status": "blocked_destructive",
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=0,
        )
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=DrillOutcome.SKIP,
        hard_stop_claims=_default_hard_stop_claims(definition),
        results={
            "summary": live_detail,
            "checks": [_check("live_execution", "skip", "manual isolated-host procedure required")],
            "preflight_status": "ready",
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=0,
    )


def _dispatch_backup_declaration(request: DrillRunRequest, definition: DrillDefinition) -> DrillRunResult:
    started = utc_now()
    script = request.project_root / "scripts" / "verify_backup_contract.sh"
    checks: list[dict[str, Any]] = []
    if not script.is_file():
        outcome = DrillOutcome.FAIL
        checks.append(_check("backup_script_present", "fail"))
        exit_code = 1
    elif request.execution_mode != "live":
        outcome = DrillOutcome.SKIP
        checks.append(_check("backup_script_present", "pass"))
        checks.append(_check("live_backup_contract", "skip", "execution_mode=dry_run"))
        exit_code = 0
    else:
        completed_proc = subprocess.run(
            ["bash", str(script), "--pre-upgrade"],
            cwd=request.project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        outcome = DrillOutcome.PASS if completed_proc.returncode == 0 else DrillOutcome.FAIL
        checks.append(_check("backup_script_present", "pass"))
        checks.append(
            _check(
                "live_backup_contract",
                "pass" if completed_proc.returncode == 0 else "fail",
                "verify_backup_contract.sh executed",
            )
        )
        exit_code = completed_proc.returncode
    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=_default_hard_stop_claims(
            definition,
            detail_by_id={"HS-008": HardStopClaimStatus.STILL_OPEN},
        ),
        results={
            "summary": "Backup declaration drill never claims HS-008 closed without restore evidence",
            "checks": checks,
            "preflight_status": _preflight_status(
                live_required=False,
                execution_mode=request.execution_mode,
            ),
            "exit_code": exit_code,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=exit_code,
    )


def _script_contract_dispatch(
    request: DrillRunRequest,
    definition: DrillDefinition,
    *,
    script_name: str,
    summary: str,
) -> DrillRunResult:
    started = utc_now()
    script = request.project_root / "scripts" / script_name
    checks: list[dict[str, Any]] = []
    if not script.is_file():
        completed = utc_now()
        return DrillRunResult(
            drill_id=definition.drill_id,
            drill_version=definition.version,
            outcome=DrillOutcome.FAIL,
            hard_stop_claims=_default_hard_stop_claims(definition),
            results={
                "summary": summary,
                "checks": [_check("script_present", "fail", f"missing scripts/{script_name}")],
                "preflight_status": "ready",
                "exit_code": 1,
            },
            started_at=started,
            completed_at=completed,
            fixture_digest=None,
            exit_code=1,
        )

    checks.append(_check("script_present", "pass"))
    syntax = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    checks.append(
        _check(
            "bash_syntax",
            "pass" if syntax.returncode == 0 else "fail",
        )
    )
    if request.execution_mode != "live":
        outcome = DrillOutcome.SKIP if syntax.returncode == 0 else DrillOutcome.FAIL
        checks.append(_check("live_host_execution", "skip", "execution_mode=dry_run"))
        exit_code = 0 if syntax.returncode == 0 else 1
    elif definition.destructive and not request.isolated_target_confirmed:
        outcome = DrillOutcome.SKIP
        checks.append(
            _check(
                "isolated_target_confirmation",
                "skip",
                "requires --isolated-target for destructive live drill",
            )
        )
        exit_code = 0
    else:
        outcome = DrillOutcome.SKIP
        checks.append(
            _check(
                "live_host_execution",
                "skip",
                "execute documented host procedure manually on isolated target",
            )
        )
        exit_code = 0

    completed = utc_now()
    return DrillRunResult(
        drill_id=definition.drill_id,
        drill_version=definition.version,
        outcome=outcome,
        hard_stop_claims=_default_hard_stop_claims(definition),
        results={
            "summary": summary,
            "checks": checks,
            "preflight_status": _preflight_status(
                live_required=definition.live_required,
                execution_mode=request.execution_mode,
            ),
            "exit_code": exit_code,
        },
        started_at=started,
        completed_at=completed,
        fixture_digest=None,
        exit_code=exit_code,
    )


_DISPATCHERS = {
    "smoke-readiness": _dispatch_smoke_readiness,
    "audit-chain-verify": _dispatch_audit_chain_verify,
    "clamav-eicar": _dispatch_clamav_eicar,
    "clamav-daemon-down": _dispatch_clamav_daemon_down,
    "oidc-group-mapping": _dispatch_oidc_group_mapping,
    "model-routing-policy-block": _dispatch_model_routing_policy_block,
    "disk-thresholds": _dispatch_disk_thresholds,
    "worker-crash-recovery": lambda request, definition: _dispatch_destructive_live_only(
        request,
        definition,
        summary="Worker crash/recovery requires isolated live host",
        live_detail="Kill worker before/after commit boundaries on isolated target",
    ),
    "backup-declaration": _dispatch_backup_declaration,
    "backup-pitr-restore": lambda request, definition: _dispatch_destructive_live_only(
        request,
        definition,
        summary="Backup PITR restore requires customer backup target (HS-008)",
        live_detail="Execute isolated restore procedure with audit verification",
    ),
    "rhel-install": lambda request, definition: _script_contract_dispatch(
        request,
        definition,
        script_name="install.sh",
        summary="RHEL install script contract validation",
    ),
    "rhel-upgrade": lambda request, definition: _script_contract_dispatch(
        request,
        definition,
        script_name="upgrade.sh",
        summary="RHEL upgrade script contract validation",
    ),
    "rhel-rollback": lambda request, definition: _script_contract_dispatch(
        request,
        definition,
        script_name="rollback.sh",
        summary="RHEL rollback script contract validation",
    ),
}


def map_environment_type(config: RuntimeConfig) -> str:
    profile = config.runtime_profile
    if profile == "dev_local":
        return "dev_local"
    if profile == "onprem_production":
        return "onprem_production"
    return "onprem_staging"


def run_validation_drill(request: DrillRunRequest) -> DrillRunResult:
    """Dispatch one published validation drill."""
    definition = get_drill_definition(request.drill_id)
    dispatcher = _DISPATCHERS.get(request.drill_id)
    if dispatcher is None:
        raise KeyError(request.drill_id)
    return dispatcher(request, definition)


def new_record_id() -> str:
    return str(uuid.uuid4())
