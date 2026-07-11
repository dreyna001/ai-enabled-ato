# P0 Gate Record (EP-01-core-safety foundation)

**Gate:** P0 deterministic foundation / `EP-01-core-safety` exit evidence  
**Outcome:** PASS for deterministic helpers and regression coverage  
**Recorded:** 2026-07-10  
**Normative source:** Section 31 P0 of [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md)

This record closes the P0 deterministic foundation gate. It does not claim production workers, job leases, CRUD mutation routes, audit writes, automatic pipeline wiring, qualified authority review, or AI qualification.

## Scope

In scope for this gate:

- Deterministic helpers for model routing/gateway policy ordering, blob and manifest replay, corruption detection, partial-write cleanup, stale staging reconciliation, session rollback, configured limit enforcement, matrix exact-completeness validation, and typed HTTP Problem mappings
- Representative regression tests named in [`docs/requirements/traceability.yaml`](requirements/traceability.yaml) requirement `P0-010`
- CI enforcement via [`.github/workflows/contracts.yml`](../.github/workflows/contracts.yml)

Out of scope (tracked by other partial requirements, not claimed here):

- Real workers, job lease expiry, retry, and worker crash recovery (`P0-005` partial; P1)
- Production `/api/v1` mutation routes and audit writes (`P0-004` partial)
- Automatic analysis-pipeline calls to matrix coverage (`R-009` partial)
- Production worker/model callsites (`R-006`, `P0-013` partial)
- Malicious archive, XML, Office, and SVG intake (`P0-003` partial; P3)
- Quarantine handling and broader production route taxonomy (`P0-009` partial)
- Manifest concurrent-writer TOCTOU hardening
- Qualified authority review (`HS-001` remains open)

## Evidence

| Artifact | Path |
| --- | --- |
| Traceability (P0-010 implemented) | `docs/requirements/traceability.yaml` |
| CI gate workflow | `.github/workflows/contracts.yml` |
| Model gateway and routing | `src/ato_service/model_gateway.py`, `src/ato_service/model_routing.py` |
| Content manifests and reconciliation | `src/ato_service/content_manifests.py`, `src/ato_service/storage_reconciliation.py` |
| Matrix coverage validator | `src/ato_service/matrix_coverage.py` |
| Lifecycle and Problem taxonomy | `src/ato_service/lifecycle_transitions.py`, `src/ato_service/problems.py` |
| Blob storage and limits | `src/ato_service/blobs.py`, `src/ato_service/runtime_config.py` |

## Verification

### Service-focused regression (recorded)

Run from the repository root:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/ato_service -m "not integration" -q
```

**Recorded result:** `415 passed, 10 skipped, 1 deselected, 1 warning` on Python 3.12. The warning is a third-party `python-dateutil` deprecation warning. No integration services are required.

### Contract traceability gate (recorded)

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_contracts.py -q
```

**Recorded result:** `15 passed, 1 warning in 2.28s` on Python 3.12. The warning is a third-party `python-dateutil` deprecation warning.

### Full P0 exit gate (recorded)

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest -m "not integration" -q
```

**Recorded result:** `430 passed, 10 skipped, 1 deselected, 1 warning` on Python 3.12. The warning is the same third-party `python-dateutil` deprecation warning. CI enforces this selection in `.github/workflows/contracts.yml`.

## Residuals after P0

1. **HS-001 stays open.** The pinned authority manifest remains `status: draft`; authority-dependent implementation and release stay blocked.
2. **Job and worker durability** (lease, retry, crash recovery) remain planned for P1 under `P0-005` partial status.
3. **Production API mutation and audit** routes are not implemented; illegal-transition coverage is helper and taxonomy tests only.
4. **Automatic wiring** of gateway policy, configured limits, and matrix coverage into analysis workers is not claimed.
5. **Concurrent manifest writers** may still race at the filesystem layer; TOCTOU hardening is future work.
6. **No external approvals are claimed.** This gate records internal repository evidence only.

## Post-gate runtime/deployment baseline synchronization

**Recorded:** 2026-07-10 (append-only; does not reopen or replace the P0 gate above)

Section 31 now records the validated runtime-config and API-only deployment-contract baseline as part of P0. Evidence includes `docs/CONFIGURATION.md`, `deployment/README.md`, `deployment/systemd/ato-api.service`, `deployment/nginx/ato-api.conf`, `scripts/install.sh`, `scripts/smoke_service_chain.sh`, and `tests/test_deployment_contract.py`.

This synchronization does not claim a worker, portal, production identity, live RHEL install, PostgreSQL migration smoke, TLS promotion, backup/restore drill, or P7 completion.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_contracts.py tests/test_deployment_contract.py -q
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest -m "not integration" -q
```

**Recorded results:** `68 passed, 1 warning` for the focused contract gate; `529 passed, 10 skipped, 1 deselected, 1 warning` for the full non-integration gate on Python 3.12.
