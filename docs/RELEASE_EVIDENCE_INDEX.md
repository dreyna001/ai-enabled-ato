# Release Evidence Index

**Status:** Phase 6 integration and release gate (2026-07-14)  
**Repository tip:** `cursor/phase-6-integration-and-release-gate-f4f1` (integrated from `feature/complete-ato-product` @ `5dea50c` plus quality-gate, documentation-reconciliation, and release-packaging workstreams)  
**Alembic head:** `20260717_0012` (`migrations/versions/20260717_0012_package_search_index.py`)

This index links automated contract evidence, qualification assets, drill schemas, CI jobs, migration head, and release-package verification. It does **not** substitute for live PostgreSQL drills on customer hosts, Playwright runs against a managed stack, RHEL install/upgrade/rollback validation, or customer/authority evidence. Open hard stops remain in [`requirements/hard-stops.yaml`](requirements/hard-stops.yaml).

## Evidence classification

| Label | Meaning |
| --- | --- |
| **PASS (code)** | Deterministic repository tests pass without live customer infrastructure |
| **PASS (CI)** | GitHub Actions job runs the evidence in default CI |
| **environment-not-run** | Evidence exists but was not executed in this reconciliation environment |
| **customer-gated** | Blocked by an open hard stop; mocks and dry-run drills do not close it |
| **blocked** | Requirement explicitly blocked until external input exists |

## Contract and regression tests

| Evidence | Path | CI job | Classification |
| --- | --- | --- | --- |
| Machine contracts (schemas, OpenAPI, traceability, hard stops) | [`tests/test_contracts.py`](../tests/test_contracts.py) | `contracts` → `Run contract tests` | PASS (CI) |
| Non-integration regression gate | `python -m pytest -m "not integration"` | `contracts` → `Run non-integration tests` | PASS (CI) |
| Deployment asset contracts | [`tests/test_deployment_contract.py`](../tests/test_deployment_contract.py) | included in non-integration gate | PASS (CI) |
| Portal/nginx asset contracts | [`tests/test_portal_contract.py`](../tests/test_portal_contract.py) | included in non-integration gate | PASS (CI) |
| Playwright E2E asset contracts | [`tests/test_e2e_contract.py`](../tests/test_e2e_contract.py) | included in non-integration gate | PASS (code) |
| Playwright mocked rendering/authz | [`portal/e2e/security/rendering-authz.spec.ts`](../portal/e2e/security/rendering-authz.spec.ts) | `portal-playwright-mocked` | PASS (code) |
| Release packaging (focused) | [`tests/test_release_packaging.py`](../tests/test_release_packaging.py) | included in non-integration gate | PASS (code) |
| Service unit/integration (optional) | [`tests/ato_service/test_workflow_e2e_integration.py`](../tests/ato_service/test_workflow_e2e_integration.py), [`test_workflow_recovery_integration.py`](../tests/ato_service/test_workflow_recovery_integration.py) | `integration-postgres` | PASS (CI) when `ATO_TEST_DATABASE_URL` set |
| PostgreSQL connectivity probe | [`tests/ato_service/test_db.py`](../tests/ato_service/test_db.py) | optional local/CI | environment-not-run when URL absent |

**Generated-at-build (Phase 6 integration gate, Python 3.12):**

| Gate | Result |
| --- | --- |
| Focused contract/operator/release suite | **219 passed** |
| Non-integration regression (`-m "not integration"`) | **1619 passed**, 1 skipped, 20 deselected |
| Ruff (`ruff check .`) | **0 errors** |
| Alembic heads | **single head `20260717_0012`** |
| Portal vitest | **22 passed** |
| Portal production build | **PASS** |
| Playwright mocked rendering/authz | **6 passed** |
| Integration collection without `ATO_TEST_DATABASE_URL` | **20 collected**, all skipped at runtime |
| Shell syntax (deployment contract) | **10 passed** |

Historical doc-reconciliation record (unchanged gate record [`P6_GATE_RECORD.md`](P6_GATE_RECORD.md)): **1585 passed**, 1 skipped, 20 deselected at documentation-reconciliation tip.

## Qualification and evaluation manifests

| Asset | Path | Validator | Classification |
| --- | --- | --- | --- |
| Sealed qualification corpus | [`data/qualification/manifest.json`](../data/qualification/manifest.json) | [`tests/test_contracts.py::test_qualification_manifest_validates_and_matches_local_bytes`](../tests/test_contracts.py), [`tests/ato_operator/test_qualification_check.py`](../tests/ato_operator/test_qualification_check.py) | PASS (code); does not close HS-001..009 |
| Qualification operator CLI | `ato-operator qualification-check` | [`tests/ato_operator/test_cli.py`](../tests/ato_operator/test_cli.py) | PASS (code) |
| AI evaluation record schema | [`docs/contracts/ai-evaluation-record.schema.json`](contracts/ai-evaluation-record.schema.json) | [`tests/ato_service/test_ai_evaluation_record.py`](../tests/ato_service/test_ai_evaluation_record.py) | PASS (code); live adjudicated holdout **blocked (HS-006)** |
| Validation drill record schema | [`docs/contracts/validation-drill-record.schema.json`](contracts/validation-drill-record.schema.json) | [`tests/ato_operator/test_drill_records.py`](../tests/ato_operator/test_drill_records.py) | PASS (code) |

## Drill and operator evidence

| Capability | Path | Notes |
| --- | --- | --- |
| Drill catalog and dispatch | [`src/ato_operator/drill_catalog.py`](../src/ato_operator/drill_catalog.py), [`drill_handlers.py`](../src/ato_operator/drill_handlers.py) | Dry-run default; hard-stop claims never close from mocks |
| Drill record persistence | [`src/ato_operator/drill_records.py`](../src/ato_operator/drill_records.py) | Append-only under operator-supplied root |
| Operator preflight/migrate | [`src/ato_operator/cli.py`](../src/ato_operator/cli.py), [`preflight.py`](../src/ato_operator/preflight.py) | `verify-migrations --dry-run` reports head `20260717_0012` |
| Audit chain verify | [`src/ato_operator/audit_verify.py`](../src/ato_operator/audit_verify.py) | Requires live PostgreSQL for full chain walk |

Live customer validation drills on RHEL hosts: **environment-not-run**.

## CI workflows

| Workflow | Path | Jobs |
| --- | --- | --- |
| Contracts + non-integration | [`.github/workflows/contracts.yml`](../.github/workflows/contracts.yml) | `contracts`, `integration-postgres`, `portal-playwright-mocked` |

## Migration head

| Check | Path | Expected head |
| --- | --- | --- |
| Alembic script head | [`alembic.ini`](../alembic.ini) + `migrations/versions/` | `20260717_0012` |
| Head assertion tests | [`tests/ato_service/test_db.py::test_alembic_head_is_package_search_index_migration`](../tests/ato_service/test_db.py) | `20260717_0012` |
| Operator verify (dry-run) | `ato-operator verify-migrations --dry-run` | `20260717_0012` |

## Release package verification

| Step | Command / asset | Classification |
| --- | --- | --- |
| Deterministic archive build | [`scripts/build_release.sh`](../scripts/build_release.sh) | PASS (code); integration dry-run **360 files**, migration head `20260717_0012` |
| Offline archive verify | [`scripts/verify_release.sh`](../scripts/verify_release.sh) | PASS (code); `signature_status: unavailable` (no publication/signing) |
| Install layout (no side effects by default) | [`scripts/install.sh`](../scripts/install.sh) | PASS (code) via deployment-contract tests |
| Portal bundle staging | `portal/dist` after `npm run build` | PASS (code) in integration environment |
| Airgap wheel prestage | [`scripts/prestage_airgap_deps.sh`](../scripts/prestage_airgap_deps.sh) | PASS (code) `--verify-only` with temporary fixture |
| Smoke chain | [`scripts/smoke_service_chain.sh`](../scripts/smoke_service_chain.sh) | environment-not-run without running API |
| Backup contract check | [`scripts/verify_backup_contract.sh`](../scripts/verify_backup_contract.sh) | PASS (code); **customer-gated (HS-008)** for production readiness |
| Playwright managed-stack E2E | [`portal/e2e/`](../portal/e2e/) + [`scripts/e2e-stack-start.sh`](../scripts/e2e-stack-start.sh) | environment-not-run |

## Build-phase gate records

Gate records distinguish **code-complete** automated evidence from **environment-not-run** and **customer-gated** residuals. Append-only historical records in [`P0_GATE_RECORD.md`](P0_GATE_RECORD.md) and [`P1_GATE_RECORD.md`](P1_GATE_RECORD.md) are preserved.

| Build phase | Gate record | Automated evidence | Residual blockers |
| --- | --- | --- | --- |
| P-1 / EP-00 | [`P1_GATE_RECORD.md`](P1_GATE_RECORD.md) | PASS (recorded) | HS-001..010 recorded |
| P0 / EP-01 foundation | [`P0_GATE_RECORD.md`](P0_GATE_RECORD.md) | PASS (recorded) | Workers/API partial at P0 time; superseded by later addenda |
| P2 FedRAMP 20x Class C | [`P2_GATE_RECORD.md`](P2_GATE_RECORD.md) | PASS (code) | HS-001 official qualification; live semantic E2E **environment-not-run** without DB |
| P3 Secure intake | [`P3_GATE_RECORD.md`](P3_GATE_RECORD.md) | PASS (code) dev_local extraction | **customer-gated (HS-005)** production extraction |
| P4 Draft artifacts | [`P4_GATE_RECORD.md`](P4_GATE_RECORD.md) | PASS (code) generators + schema | HS-001, HS-002, HS-009 release claims |
| P5 Portal / review / export | [`P5_GATE_RECORD.md`](P5_GATE_RECORD.md) | PASS (code) API + portal assets | Playwright live **environment-not-run**; **customer-gated (HS-003)** IdP |
| P6 Advanced analysis / assistant | [`P6_ANALYSIS_GATE_RECORD.md`](P6_ANALYSIS_GATE_RECORD.md) | PASS (code) search/chat/refusal | **blocked (HS-006)** AI qualification |
| P6 Doc reconciliation | [`P6_GATE_RECORD.md`](P6_GATE_RECORD.md) | PASS (historical) | Does not close build P6 AI gates |
| P6 Integration / release gate | this index (generated-at-build) | PASS (code) automated gates above | Live RHEL/systemd/nginx/TLS/backup/customer IdP/scanner/model/authority drills **environment-not-run** or **customer-gated** |
| P7 On-prem release | [`P7_GATE_RECORD.md`](P7_GATE_RECORD.md) | PASS (code) deployment contracts | Live RHEL drills **environment-not-run**; HS-005, HS-008 |

## Hard stops (never closed from mocks)

| ID | Status | Evidence paths |
| --- | --- | --- |
| HS-001 | open | [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md), [`docs/contracts/authority-manifest.json`](contracts/authority-manifest.json) |
| HS-002 | open | [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md) |
| HS-003 | open | CONFIGURATION, OPERATIONS, deployment README |
| HS-004 | open | CONFIGURATION, THREAT_MODEL, deployment README |
| HS-005 | open | CONFIGURATION, THREAT_MODEL, OPERATIONS |
| HS-006 | open | AI_EVALUATION_GUIDE |
| HS-007 | out_of_scope | ATO_TECHNICAL_SPEC |
| HS-008 | open | OPERATIONS, CONFIGURATION, deployment README |
| HS-009 | open | domain schema, ATO_TECHNICAL_SPEC |
| HS-010 | using_default | OPERATIONS |

## Standard commands (canonical)

```bash
# Contract gate (network-free)
python -m pip install -e ".[dev]"
python -m pytest tests/test_contracts.py -q
python -m pytest -m "not integration" -q

# Optional PostgreSQL workflow integration
export ATO_TEST_DATABASE_URL='postgresql+asyncpg://ato:ato@localhost:5432/ato_test'
python -m alembic upgrade head
python -m pytest tests/ato_service/test_workflow_e2e_integration.py tests/ato_service/test_workflow_recovery_integration.py -m integration -q

# Operator verification
ato-operator verify-migrations --config deployment/config/runtime-config.dev_local.json --dry-run
ato-operator qualification-check --config deployment/config/runtime-config.dev_local.json
```

## Missing live evidence (explicit)

1. Customer RHEL 9 install, upgrade, rollback, backup, restore, and incident drills on target hosts.
2. Customer IdP integration with verified group mapping (**HS-003**).
3. Production malware scanner operation and verification (**HS-005**).
4. Customer-approved model endpoint policy and live model calls on customer data (**HS-004**).
5. Adjudicated AI qualification holdout and immutable passing evaluation record (**HS-006**).
6. Qualified authority human review (**HS-001**).
7. Customer backup target and key ownership verification (**HS-008**).
8. Playwright browser E2E against a managed local stack (assets and contracts exist; live run optional).
