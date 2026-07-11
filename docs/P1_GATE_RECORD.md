# P-1 Gate Record (EP-00-contracts)

**Gate:** P-1 / `EP-00-contracts`  
**Outcome:** PASS — internal contract publication and recording complete  
**Recorded:** 2026-07-10  
**Normative source:** Section 34 of [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md)

This record closes the P-1 publication gate. It does not claim qualified authority review, customer approvals, production readiness, or AI qualification.

## Evidence

| Artifact | Path |
| --- | --- |
| Normative specification | `ATO_TECHNICAL_SPEC.md` |
| Contract index | `docs/contracts/README.md` |
| Internal JSON Schemas | `docs/contracts/*.schema.json` |
| Authority manifest (pinned bytes) | `docs/contracts/authority-manifest.json` |
| OpenAPI 3.1 contract | `docs/contracts/openapi.json` |
| Lifecycle and error taxonomy | `docs/contracts/LIFECYCLE_AND_ERRORS.md` |
| Threat model | `docs/THREAT_MODEL.md` |
| AI evaluation guide | `docs/AI_EVALUATION_GUIDE.md` |
| Operations contract | `docs/OPERATIONS_AND_RECOVERY.md` |
| Runtime configuration contract | `docs/CONFIGURATION.md`, `docs/contracts/runtime-config.schema.json` |
| API-only deployment scaffold | `deployment/README.md`, `deployment/`, `scripts/install.sh`, `scripts/smoke_service_chain.sh` |
| Persistent agent rule | `.cursor/rules/ato-runtime-deployment-contract.mdc` |
| Deployment contract tests | `tests/test_deployment_contract.py` |
| Traceability | `docs/requirements/traceability.yaml` |
| Hard-stop register | `docs/requirements/hard-stops.yaml` |

## Verification

Run from the repository root:

The original P-1 command and result are preserved below. Current reruns MUST also execute `tests/test_deployment_contract.py` and the full non-integration gate recorded in the latest post-gate addendum.

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/test_contracts.py -q
```

**Expected result:** all contract tests pass (network-free).

**Recorded result:** `10 passed, 1 warning in 1.40s` on Python 3.12.8. The
warning is a third-party `python-dateutil` deprecation warning.

Optional broader regression check:

```bash
python -m pytest -m "not integration" -q
```

**Recorded result:** `33 passed, 1 deselected, 1 warning in 2.16s` on Python
3.12.8. The deselected test is integration-scoped; the warning is the same
third-party deprecation warning noted above.

## Constraints after P-1

1. **HS-001 stays open.** Vendored authority bytes are pinned and digest-verified, but qualified human authority review is not complete. Authority-dependent implementation and release remain blocked.
2. **Other customer-specific hard stops** (`HS-002` through `HS-009`, and customer overrides for `HS-010`) remain open and scoped to the phases that need them. They are recorded, not resolved.
3. **P0 core safety work may proceed.** Section 34 requires hard stops to be resolved or recorded and authority sources to be pinned and hashed. P-1 satisfies those publication criteria. P0 does not require qualified authority review, customer IdP values, production scanner operation, or AI qualification.
4. **Unresolved lifecycle details do not block P0.** Job `status` enum, `attempt_count` increment timing, `pending_approval -> expired` deadline, and Disposition decision graphs remain explicitly deferred in `docs/contracts/LIFECYCLE_AND_ERRORS.md` Section 6 until a later contract amendment.
5. **No external approvals are claimed.** This gate records internal repository evidence only.

## Next phase

P0 (`EP-01-core-safety`) may start after this record is committed. Do not infer missing contracts or bypass open hard stops for authority-dependent, customer-specific, production, or qualification work.

## Post-gate verification (service foundation docs sync)

**Recorded:** 2026-07-10 (append-only; does not reopen or replace the P-1 gate above)

This records contract/docs synchronization after `ato_service` health/readiness, protected DSN startup, and PostgreSQL foundation landed in steps 1-4. It does not claim live PostgreSQL/Alembic smoke execution in default CI.

```text
python -m pip install -e ".[dev]"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest tests/test_contracts.py -q
```

**Post-gate result:** `15 passed, 1 warning in 2.03s` on Python 3.12.8.
The warning is the existing third-party `python-dateutil` deprecation warning.

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest -m "not integration" -q
```

**Post-gate result:** `238 passed, 1 skipped, 1 deselected, 1 warning in
3.82s` on Python 3.12.8. The deselected test is integration-scoped
(`test_database_connectivity_probe_against_optional_test_database`); the single
skip is an expected model-routing negative-path case. The warning is the
existing third-party `python-dateutil` deprecation warning.

**Intentionally unperformed in this evidence:** live `alembic upgrade head` against customer PostgreSQL, optional `ATO_TEST_DATABASE_URL` connectivity proof, and production operations smoke.

## Post-gate runtime/deployment contract synchronization

**Recorded:** 2026-07-10 (append-only; does not reopen or replace the P-1 gate above)

This addendum records the canonical runtime JSON, capability/secret boundaries, API-only deployment scaffold, cross-cutting phase rule, and deterministic persistence checks added to Section 34 items 11-12. It does not claim live RHEL, PostgreSQL, TLS, backup, recovery, or full P7 validation.

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest tests/test_contracts.py tests/test_deployment_contract.py -q
```

**Post-gate result:** `68 passed, 1 warning in 2.46s` on Python 3.12. The warning is the existing third-party `python-dateutil` deprecation warning.

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest -m "not integration" -q
```

**Post-gate result:** `529 passed, 10 skipped, 1 deselected, 1 warning in 4.71s` on Python 3.12. The deselected test is integration-scoped; the warning is the same third-party deprecation warning.

## Post-gate P1.0 job/attempt contract amendment

**Recorded:** 2026-07-11 (append-only; does not reopen or replace the P-1 gate above)

This addendum records contract closure for the job `status` enum, `attempt_count` increment timing, and reviewed expired-lease recovery semantics in `docs/contracts/LIFECYCLE_AND_ERRORS.md` Section 2.7, `ATO_TECHNICAL_SPEC.md` Section 20, and `docs/OPERATIONS_AND_RECOVERY.md`. It does not claim analyzer worker-loop completion, API mutation routes, or EP-06 approval timers or disposition implementation.

```text
python3 -m pytest tests/test_contracts.py -q
```

**Post-gate result:** `22 passed in 1.39s` on Python 3.12.

EP-06 `pending_approval -> expired` timers and disposition mutation routes remain later implementation work.
