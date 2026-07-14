# P7 Gate Record (Complete on-prem release)

**Gate:** Section 31 P7 — Complete on-prem release  
**Outcome:** TEMPLATE — deployment contracts PASS (code); live-host drills environment-not-run  
**Recorded:** 2026-07-14  
**Classification:** PASS (code) | environment-not-run (live RHEL) | customer-gated (HS-005, HS-008)

## Automated evidence (PASS code)

| Area | Path |
| --- | --- |
| Install/upgrade/drain/rollback scripts | `scripts/install.sh`, `upgrade.sh`, `drain_workers.sh`, `rollback.sh` |
| systemd/nginx assets | `deployment/systemd/`, `deployment/nginx/` |
| Deployment contract tests | `tests/test_deployment_contract.py` |
| Validation drill dispatch | `src/ato_operator/drill_handlers.py`, `tests/ato_operator/test_drill_dispatch.py` |
| Operator docs | `deployment/README.md`, `docs/CUSTOMER_ONBOARDING.md`, `docs/AIRGAP_PRESTAGE.md` |
| Migration head | `20260717_0012` via `ato-operator verify-migrations --dry-run` |

```bash
python -m pytest tests/test_deployment_contract.py tests/ato_operator/test_drill_dispatch.py tests/ato_operator/test_drill_records.py -m "not integration" -q
```

## environment-not-run (required before production release claims)

| Drill | Command / procedure |
| --- | --- |
| RHEL install + migrate + start + smoke | `scripts/install.sh --migrate --start --smoke` on target host |
| Upgrade + drain | `scripts/upgrade.sh`, `scripts/drain_workers.sh` |
| Rollback metadata | `scripts/rollback.sh` |
| Backup contract | `scripts/verify_backup_contract.sh` |
| Live validation drills with records | `ato-operator run-drill … --write-record` |
| Audit chain on live DB | `ato-operator verify-audit` |

## customer-gated

- Production malware scanner (**HS-005**)
- Customer backup target and key ownership (**HS-008**)
- Production readiness while **HS-001**, **HS-003**, **HS-004** remain open for claimed scope

## Record template for live drill evidence

When a live drill completes on a customer or lab host, append an immutable record under `/var/lib/ato/validation-drill-records/` per [`validation-drill-record.schema.json`](contracts/validation-drill-record.schema.json). Set `hard_stop_claims` explicitly; never mark a hard stop `closed` without customer/authority evidence paths listed in [`requirements/hard-stops.yaml`](requirements/hard-stops.yaml).
