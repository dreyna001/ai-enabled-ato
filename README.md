# ATO Evidence Analysis

Standalone project for the ATO Evidence Analysis Portal. Sibling to `llm_notable_analysis` under `Desktop\Cursor`.

## Docs

### Core docs

| File | Purpose |
| --- | --- |
| [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md) | **Normative** product, security, and implementation contract |
| [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md) | Non-normative product vision and delivery summary |
| [`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md) | User workflow and epic acceptance map |
| [`ATO_PORTAL_DEMO_TALKING_TRACK.md`](ATO_PORTAL_DEMO_TALKING_TRACK.md) | Approved demo language and glossary |
| [`docs/PORTAL_WORKFLOW_GUIDE.md`](docs/PORTAL_WORKFLOW_GUIDE.md) | Portal UI walkthrough, LLM usage, checks, and ATO artifacts by stage |
| [`docs/contracts/README.md`](docs/contracts/README.md) | P-1 machine-contract index and validation rules |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Threat model and required security controls |
| [`docs/AI_EVALUATION_GUIDE.md`](docs/AI_EVALUATION_GUIDE.md) | AI labels, qualification data, metrics, and hard stops |
| [`docs/OPERATIONS_AND_RECOVERY.md`](docs/OPERATIONS_AND_RECOVERY.md) | Operations, durability, backup, restore, and recovery contract |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Runtime JSON config, precedence, capability flags, and local verification |
| [`deployment/README.md`](deployment/README.md) | Portal/API/worker deployment scaffold (systemd, nginx templates, install/smoke) |
| [`docs/contracts/LIFECYCLE_AND_ERRORS.md`](docs/contracts/LIFECYCLE_AND_ERRORS.md) | Legal transitions and stable error taxonomy |
| [`docs/requirements/traceability.yaml`](docs/requirements/traceability.yaml) | Normative requirement ownership and verification status |
| [`docs/requirements/hard-stops.yaml`](docs/requirements/hard-stops.yaml) | Customer and authority inputs that implementation must not infer |
| [`docs/P6_GATE_RECORD.md`](docs/P6_GATE_RECORD.md) | Phase 6 documentation and contract reconciliation gate |

### Plans, operator, and evidence

| File | Purpose |
| --- | --- |
| **Plans** | |
| [`docs/FINAL_PRODUCT_IMPLEMENTATION_PLAN.md`](docs/FINAL_PRODUCT_IMPLEMENTATION_PLAN.md) | Master plan — components, phases, delivered-status reconciliation |
| [`docs/PACKAGE_EDITOR_PLAN.md`](docs/PACKAGE_EDITOR_PLAN.md) | Component A — intake, extraction, draft editor, sealed confirm |
| [`docs/UPLOAD_FIRST_INTAKE_PLAN.md`](docs/UPLOAD_FIRST_INTAKE_PLAN.md) | Upload-first intake, MAP/REDUCE orchestration, single-user mode (approved 2026-07-17) |
| [`docs/THIRD_PARTY_HARDENING_PLAN.md`](docs/THIRD_PARTY_HARDENING_PLAN.md) | OIDC and ClamAV production adapters; optional dependency hardening |
| **Operator / release** | |
| [`docs/CUSTOMER_ONBOARDING.md`](docs/CUSTOMER_ONBOARDING.md) | Customer operator onboarding checklist for on-prem installs |
| [`docs/AIRGAP_PRESTAGE.md`](docs/AIRGAP_PRESTAGE.md) | Airgap dependency prestaging on a connected bastion |
| [`docs/RELEASE_PACKAGING.md`](docs/RELEASE_PACKAGING.md) | Deterministic release archives and offline verification |
| [`docs/WSL_LOCAL_DEPLOY.md`](docs/WSL_LOCAL_DEPLOY.md) | WSL local deploy with production-shaped paths and systemd |
| **Gate records** | |
| [`docs/RELEASE_EVIDENCE_INDEX.md`](docs/RELEASE_EVIDENCE_INDEX.md) | P0–P7 gate records, [`P6 analysis gate`](docs/P6_ANALYSIS_GATE_RECORD.md), contract tests, and missing live evidence |

## Current state

- **Normative target:** FedRAMP 20x Program Class C package preparation plus security-only agency FISMA, one on-prem installation per customer enterprise
- **P-1 / P0 gates:** Recorded in [`docs/P1_GATE_RECORD.md`](docs/P1_GATE_RECORD.md) and [`docs/P0_GATE_RECORD.md`](docs/P0_GATE_RECORD.md); Phase 6 documentation reconciliation recorded in [`docs/P6_GATE_RECORD.md`](docs/P6_GATE_RECORD.md)
- **Alembic head:** `20260717_0013` (defer package revision metadata migration)
- **Delivered stack (code-complete, contract-tested):** `ato_service` API with OIDC-backed server sessions; React/Vite portal; `ato-intake-worker` and `ato-analyzer-worker` long-running workers; full `/api/v1` surface (systems, package revisions, draft editor, intake, deterministic and model-assisted analysis runs, review dispositions, export approval/download, package search and bounded chat); `ato-operator` preflight, migration verify, qualification check, validation drills, audit verify, and search-index rebuild; deployment assets for API, portal nginx, intake/analyzer workers; sealed qualification corpus under `data/qualification/`
- **dev_local substitutes:** synthetic JSON intake path, HS-005 integrity-only malware substitute, and fake scanner/model/IdP boundaries in workflow integration tests — not production customer extraction or live IdP deployment
- **Not claimed:** production release, live RHEL install/upgrade/rollback drills, customer IdP verification (**HS-003**), production malware scanning (**HS-005**), real customer model calls (**HS-004**), AI qualification (**HS-006**), qualified authority review (**HS-001**), or backup-target verification (**HS-008**)
- **Deployment scaffold:** install/upgrade/drain/rollback/smoke scripts, systemd units, and inactive nginx templates under [`deployment/`](deployment/); not proof of RHEL validation or production release
- **Model boundary:** OpenAI-compatible or Bedrock text backends; routing policy evaluates before every model call; customer production data remains blocked by default and by open hard stops

The historical Block 1 developer CLI has been retired. New work belongs in `ato_service` and the frozen contracts only.

P0 core safety work may proceed after the P-1 gate record. HS-001 remains open and blocks authority-dependent implementation and release. Other customer-specific hard stops remain scoped to later phases.

Every future phase must preserve the cross-cutting runtime/deployment contract in [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md) Sections 10.3 and 31: JSON schema/examples, semantic validation, explicit capability dependencies, process-specific credentials, deployment assets, operator docs, traceability, and deterministic tests change together.

## Local setup and service run (PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Have the approved secret-management process provision `C:\secure\ato-dsn.txt`
out of band as a protected UTF-8 file containing only the SQLAlchemy
PostgreSQL DSN. Never commit, echo, or construct the DSN in shell command
history. Point the service at the provisioned file:

```powershell
$env:ATO_DATABASE_DSN_FILE = 'C:\secure\ato-dsn.txt'
$env:ATO_RUNTIME_CONFIG_PATH = 'deployment\config\runtime-config.dev_local.json'
```

Optional overrides:

```powershell
# $env:ATO_AUTHORITY_MANIFEST_PATH = 'docs\contracts\authority-manifest.json'
# $env:ATO_HOST = '127.0.0.1'
# $env:ATO_PORT = '8000'
```

Apply database migrations when a live PostgreSQL instance is available (not exercised by default CI):

```powershell
alembic upgrade head
```

Start the service:

```powershell
ato-service
# equivalent: python -m ato_service
```

Long-running workers (development):

```powershell
ato-intake-worker      # unified intake; dev_local synthetic path by default
ato-analyzer-worker    # deterministic_only and model-assisted runs when configured
```

`ato-synthetic-intake-worker` remains a WSL alias for the unified intake worker.
Workers refuse `onprem_production` until operator acceptance and capability
flags are configured. Production customer extraction remains blocked by **HS-005**.

Health endpoints (unversioned, at the application root):

```text
GET http://127.0.0.1:8000/health/live
GET http://127.0.0.1:8000/health/ready
```

`GET /health/live` reports process liveness only. `GET /health/ready` runs the five published readiness probes (`database`, `storage`, `authority_manifest`, `jobs`, `configuration`). The current pinned authority manifest is `status: draft` while **HS-001** remains open, so a healthy local stack typically reports `authority_manifest: degraded` and returns HTTP `503` with `error_code: reconciliation_required` until qualified authority review closes HS-001 and the manifest is approved.

## Service foundation verification

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/ato_service -m "not integration" -q
```

No live PostgreSQL instance, worker process, or other integration service is required. The selection exercises validated runtime config, content-addressed blob and manifest writes, lifecycle and model-routing policy, synthetic intake orchestration, matrix-coverage validation, limit enforcement, staging reconciliation, session rollback helpers, and the health/Problem API boundary.

Equivalent without the plugin guard:

```powershell
python -m pytest tests/ato_service/ -m "not integration"
```

## P-1 contract verification

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_contracts.py -q
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest -m "not integration" -q
```

The full `-m "not integration"` selection is the P0 exit gate recorded in [`docs/P0_GATE_RECORD.md`](docs/P0_GATE_RECORD.md) and enforced by [`.github/workflows/contracts.yml`](.github/workflows/contracts.yml). Pytest markers: the default `not integration` selection excludes tests marked `integration`. One optional connectivity test in `tests/ato_service/test_db.py` runs only when `ATO_TEST_DATABASE_URL` is set; it is not part of default contract verification and does not prove that live PostgreSQL migrations or Alembic smoke tests ran in CI.

Deployment asset verification (network-free):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_deployment_contract.py -q
```

Configuration precedence, production paths, capability flags, and text LLM setup: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md). Operator install flow: [`deployment/README.md`](deployment/README.md).

## Text LLM (OpenAI or Bedrock)

Set `TEXT_MODEL_PROVIDER` in runtime JSON to choose the text-model backend:

| Provider | Use when | Required JSON | Secrets |
| --- | --- | --- | --- |
| `openai_compatible` (default) | Local OpenAI or any OpenAI-compatible endpoint | `TEXT_MODEL_ENDPOINT_URL`, `TEXT_MODEL_NAME` | Dev: `ATO_TEXT_MODEL_API_KEY_FILE`. Prod: `TEXT_MODEL_CREDENTIAL_REFERENCE` |
| `aws_bedrock` | Work environments using AWS Bedrock | `AWS_REGION`, `TEXT_MODEL_NAME` (Bedrock model ID) | Standard AWS credential chain (`AWS_PROFILE`, env keys, or instance role). Install `pip install -e ".[bedrock]"` |

Start from an example config:

```powershell
# OpenAI
Copy-Item deployment\config\runtime-config.dev_local.openai.example.json deployment\config\runtime-config.dev_local.json
$env:ATO_TEXT_MODEL_API_KEY_FILE = 'C:\secure\openai-api-key.txt'

# Bedrock
pip install -e ".[bedrock]"
Copy-Item deployment\config\runtime-config.dev_local.bedrock.example.json deployment\config\runtime-config.dev_local.json
$env:AWS_PROFILE = 'your-profile'
```

Point the service at the config, then call `ato_service.text_llm.build_text_model_client()`. Full steps and a Python example are in [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md#text-llm-openai-or-bedrock).
