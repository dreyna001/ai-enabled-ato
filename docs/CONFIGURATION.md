# Runtime Configuration

**Status:** API scaffold plus bounded development synthetic-intake documentation  
**Applies to:** Current `ato_service` API and `dev_local` synthetic-intake processes; not a production release claim  
**Normative contract:** [`docs/contracts/runtime-config.schema.json`](contracts/runtime-config.schema.json)

This document explains how non-secret settings and secret references are loaded today. It does not claim RHEL validation, portal/worker deployment, auth implementation, production/customer extraction, model hosting, backup automation, or full P7 completion.

## Canonical paths

| Setting | Production path | Dev override |
| --- | --- | --- |
| Runtime JSON | `/etc/ato-analyzer/runtime-config.json` | `deployment/config/runtime-config.dev_local.json` |
| Redacted template | `deployment/config/runtime-config.onprem.example.json` | same file (reference only) |
| Database DSN secret | `/etc/ato-analyzer/credentials/database-dsn` via systemd `LoadCredential=database-dsn` | `ATO_DATABASE_DSN_FILE` (dev only) |
| Package storage | `/var/ato-packages` (`STORAGE_DATA_PATH` in production JSON) | project-relative path under dev profile |
| Application tree | `/opt/ato-analyzer` | repository root |

Production selects the runtime JSON through `ATO_RUNTIME_CONFIG_PATH=/etc/ato-analyzer/runtime-config.json` (pinned in [`deployment/systemd/ato-api.service`](../deployment/systemd/ato-api.service)). There is no `config.env` and no shell-sourced env file for application settings.

## Precedence

1. **Config file path:** CLI `--config` overrides `ATO_RUNTIME_CONFIG_PATH`. If neither is set, startup fails.
2. **Settings:** Validated JSON fields are the source of truth for runtime behavior. Environment variables do not override individual JSON keys.
3. **Secret bytes:** Credential references in JSON (`*_CREDENTIAL_REFERENCE`, production `DATABASE_DSN_CREDENTIAL_REFERENCE`) resolve through protected mechanisms (systemd credentials or root-owned files). Dev may use `ATO_DATABASE_DSN_FILE` instead of a production DSN reference.
4. **Bind address:** `ATO_HOST` and `ATO_PORT` are service bootstrap knobs only. Production systemd pins loopback `127.0.0.1:8000`; nginx is the intended external listener.

Schema validation uses Draft 2020-12 with closed production profiles. See [`docs/contracts/README.md`](contracts/README.md) for fixture-based contract tests.

## Runtime profiles

**`dev_local`** — minimal JSON for local API verification and the only profile accepted by the bounded synthetic JSON intake worker. Only `schema_version`, `runtime_profile`, and `STORAGE_DATA_PATH` are required for schema validity. The worker additionally fails closed unless `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE` resolves because every lifecycle transition requires an atomic audit append. Optional model, identity, and backup fields are absent unless you add them. `VISION_MODEL_ENABLED` defaults to effective `false` when absent.

**`onprem_production`** — full non-secret contract. Schema requires text-model settings, explicit `VISION_MODEL_ENABLED`, identity, storage limits, audit references, malware-scanner declarations, backup declarations, and extraction safety limits (`MAX_PDF_PAGES_PER_FILE`, `MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE`, `MAX_ZIP_*`, `MAX_XML_*`). Secret values stay in credential files, not in JSON.

The pure extractor library (`ato_service.extraction`) reads these limits through `RuntimeConfig.extraction_limits`. Limit failures are explicit; content is never silently truncated.

| Extraction limit | Default |
| --- | ---: |
| `MAX_PDF_PAGES_PER_FILE` | 200 |
| `MAX_EXTRACTED_TEXT_CHARACTERS_PER_FILE` | 2,000,000 |
| `MAX_ZIP_MEMBERS_PER_ARCHIVE` | 500 |
| `MAX_ZIP_UNCOMPRESSED_BYTES_PER_ARCHIVE` | 104,857,600 (100 MiB; never above the default single-file limit) |
| `MAX_ZIP_DECOMPRESSION_RATIO` | 100 |
| `MAX_XML_DEPTH` | 64 |
| `MAX_XML_ELEMENTS` | 100,000 |
| `MAX_XML_ATTRIBUTES_PER_ELEMENT` | 128 |
| `MAX_XML_TEXT_NODE_CHARACTERS` | 1,048,576 |

Copy `deployment/config/runtime-config.onprem.example.json` to `/etc/ato-analyzer/runtime-config.json`, redact customer placeholders, and tighten values with the customer authority. The installer never overwrites an existing live config.

## Capability and safety flags

There are no capability bundles or profile presets. Explicit JSON flags are the single source of truth.

Do not add a bundle/preset until at least three implemented optional capabilities create a demonstrated operator need and its precedence, migration, and observability rules are approved. Every new capability flag must be added with its schema, redacted example, semantic dependency validation, operator documentation, traceability, and deterministic tests in the same change.

| Key | Role | Notes |
| --- | --- | --- |
| `VISION_MODEL_ENABLED` | **Optional capability** | Only current optional model capability. Defaults to off when absent in `dev_local`. Required boolean in `onprem_production`. When `true`, schema requires vision endpoint URL, name, context tokens, and profile; production further restricts profile to qualified external/internal OpenAI-compatible values and may require `VISION_MODEL_CREDENTIAL_REFERENCE` and allowlist entries. |
| `TEXT_MODEL_PROVIDER` | **Text LLM backend** | `openai_compatible` (default) uses `TEXT_MODEL_ENDPOINT_URL` plus `TEXT_MODEL_CREDENTIAL_REFERENCE` or dev-only `ATO_TEXT_MODEL_API_KEY` / `ATO_TEXT_MODEL_API_KEY_FILE`. `aws_bedrock` uses `AWS_REGION`, `TEXT_MODEL_NAME` as the Bedrock model ID, and the standard AWS credential chain (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or instance role). Install Bedrock support with `pip install -e ".[bedrock]"`. |
| `TEXT_MODEL_TEMPERATURE` | **Text LLM sampling** | Optional number `0..2`, default `0`. Applied to OpenAI-compatible and Bedrock text clients. |
| `TEXT_MODEL_ENDPOINT_POLICY_APPROVED` | **Routing approval** | Optional boolean, default `false`. Explicit operator approval for the configured text-model endpoint profile on redacted/customer routes. Selecting an endpoint alone does not approve the route. |
| `CUI_MODEL_BOUNDARY_APPROVED` | **CUI routing approval** | Optional boolean, default `false`. Explicit operator approval before CUI-labeled revisions may use the configured text-model endpoint. |
| `ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS` | **Safety exception** | Not a capability. Permits HTTP (not HTTPS) only for literal loopback IP endpoints (`127.0.0.1`, `::1`) on approved internal model profiles. Does not relax host allowlists for non-loopback hosts. |
| `LOCAL_PASSWORD_AUTH_ENABLED` | **Hard stop** | Must remain `false`. Declares that local password auth is forbidden; it is not a feature switch. |
| `MALWARE_SCANNER_*` | **Production scanner contract** | `dev_local` uses an **HS-005** integrity-only substitute (re-verifies stored SHA-256/size; no malware detection, no networking). `onprem_production` resolves the local ClamAV adapter when `MALWARE_SCANNER_ENABLED=true` and transport settings are valid (`MALWARE_SCANNER_TRANSPORT`, socket path or loopback TCP host/port, bounded timeout). Daemon-down and protocol errors fail closed with `scanner_unavailable` / `scanner_timeout`. Operator offline signature updates remain customer-owned; **HS-005** live drill is still required before customer extraction claims. |
| `PROCESS_CAPABILITIES` | **Explicit process flags** | Required object for `onprem_production`. Booleans gate `api`, `intake_worker`, `analyzer_worker`, `portal_static`, `malware_scanning`, `text_model_calls`, `vision_model_calls`, and `oidc_authentication`, with optional `package_search` and `package_chat`. Inactive capabilities are ignored by `ato-operator preflight`; active capabilities fail fast when dependencies are invalid. No bundles or presets. |
| `OIDC_GROUPS_CLAIM` / `OIDC_GROUP_ROLE_MAPPING` | **Identity mapping** | Required when `IDENTITY_PROVIDER_MODE=oidc`. Claim name defaults to `groups`. Role mapping overrides default RBAC group names at startup. |
| `INTERNAL_EGRESS_ALLOWLIST` | **Outbound allowlist** | Required for `onprem_production`. Closed host/port list for IdP, model endpoints, and backup targets. Operator preflight verifies configured endpoints match entries exactly. |
| `AUTHORITY_MANIFEST_FILE_REFERENCE` / `FISMA_TEMPLATE_PACK_FILE_REFERENCE` | **Pinned file references** | Optional absolute path plus `expected_sha256` digest. Operator preflight verifies bytes when configured; HS-001/HS-002 govern qualified review separately. |
| `BACKUP_TARGET_DECLARATION` | **Backup destination contract** | Required when `BACKUP_OFF_HOST_ENABLED=true`. Declares protocol, host, port, and export path; does not perform backup I/O. |
| `BACKUP_*`, `AUDIT_*` backup fields | **Recovery contract** | Declarations document intended recovery posture; they do not enable backup jobs, WAL archiving, or restore automation in this slice. |

Text-model endpoints are required for `onprem_production` schema validity but full model-call pipelines remain out of scope for this API-only slice.

## Secrets

- Production DSN: JSON holds `DATABASE_DSN_CREDENTIAL_REFERENCE` with identifier `database-dsn`; systemd loads `/etc/ato-analyzer/credentials/database-dsn`.
- **Currently wired in production:** only `database-dsn` is mapped in [`deployment/systemd/ato-api.service`](../deployment/systemd/ato-api.service). Other credential references in `onprem_production` JSON (model, OIDC, audit HMAC, backup keys) are contract placeholders until matching `LoadCredential` lines and consumers exist.
- **Audit HMAC (P1.1/P1.2):** when `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE` is present, the API and bounded synthetic worker resolve the key bytes through the credential reference only. There is no `ATO_AUDIT_HMAC_*` environment override. `dev_local` may omit the reference for read-only/API startup, but the synthetic worker and audit-dependent API mutations fail closed until it is configured. `onprem_production` requires the reference; missing, unreadable, or short keys fail startup or fail closed on mutating routes that depend on `get_audit_hmac_key`.
- **Package API authentication:** when `IDENTITY_PROVIDER_MODE=oidc` is configured with `PORTAL_PUBLIC_ORIGIN`, the API loads Postgres-backed sessions from the portal cookie and injects `authenticated_principal` on `/api/v1` routes. Mutations still require CSRF + Origin. Without identity configuration, routes remain fail-closed HTTP `401` (**HS-003** / **EP-06** partial).
- Each future process receives only the credential mappings and config projection it consumes; declaring a reference in the shared schema does not authorize loading it into every service.
- Dev DSN: set `ATO_DATABASE_DSN_FILE` to a protected UTF-8 file containing only the SQLAlchemy PostgreSQL URL. Never commit or log DSN contents.
- Dev OpenAI API key: copy `config.local.env.example` to `config.local.env` and set `ATO_TEXT_MODEL_API_KEY` when `TEXT_MODEL_PROVIDER` is `openai_compatible`.
- Bedrock: do not put AWS keys in runtime JSON. Use the normal AWS credential chain and set `AWS_REGION` in runtime JSON.

## Bounded synthetic intake worker (P1.2)

The production operator path is the long-running `ato-intake-worker` process (`deployment/systemd/ato-intake-worker.service`). It continuously drains eligible `dev_local` + `data_origin=synthetic` + all-JSON revisions through `scanning`, `extracting`, and `awaiting_confirmation` using one transaction per transition.

`ato-intake-worker` is the long-running development/production-adjacent process. It reads the same schema-validated JSON selected by `--config` or `ATO_RUNTIME_CONFIG_PATH`, resolves the development PostgreSQL DSN through the existing `ATO_DATABASE_DSN_FILE` exception, and resolves audit key bytes only through `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE`.

For a private development config, add a credential reference such as:

```json
{
  "AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE": {
    "source": "root_owned_file",
    "path": "/absolute/private/path/audit-hmac-key"
  }
}
```

Do not commit the private config or key. Run migrations, start the worker, finalize a revision from the portal, and let the worker advance intake asynchronously:

```text
ato-intake-worker --config /absolute/private/path/runtime-config.json
```

The process claims only `data_origin=synthetic` revisions whose artifacts are
all detected and declared `application/json`, and commits one lifecycle
transition per transaction. It refuses `onprem_production`, makes no model or
external scanner call, and has no customer extraction path. **HS-005** remains
open. Synthetic `text/plain` revisions are intentionally not claimed and
remain `scanning` for a later supported worker or explicit operator action.

P1.1 upload now accepts Diff 2-supported MIME types (JSON, XML, PDF, DOCX,
XLSX, SVG, PNG, JPEG, WebP, markdown, text) using `extraction.detect_format`
with declared-type and filename hints; generic ZIP uploads are rejected.
Component A Diff 3 adds `draft_builder` (deterministic draft + provenance),
`malware_scan` (dev-local integrity substitute; production fail-closed), and
`intake` (unified scan/extract orchestration over intake work leases) as library
boundaries consumed by `ato-intake-worker` and the WSL `ato-synthetic-intake-worker`
alias in `dev_local`.

## Deterministic analyzer worker

`ato-analyzer-worker` is the long-running worker for the implemented
`deterministic_only` analysis path. It is gated to `runtime_profile=dev_local`,
confirmed `data_origin=synthetic` revisions, and the pinned synthetic FISMA
profile. It continuously recovers expired leases, claims durable jobs, writes
the exact assessment matrix and artifact manifest, and keeps
`llm_call_count=0`.

It consumes the same validated runtime JSON, database DSN, storage path, and
audit HMAC credential as intake. No model credential is loaded.

```text
ato-analyzer-worker --config /absolute/private/path/runtime-config.json
```

`full` and `targeted` runs, customer-production evidence, scanner integration,
and model calls remain fail-closed and deferred.

## Text LLM (OpenAI or Bedrock)

`ato_service.text_llm` is the current text-model API-call layer. It does not replace routing policy, run limits, or worker orchestration.

### Choose a provider

Set `TEXT_MODEL_PROVIDER` in runtime JSON:

| Value | Backend | Required settings | Secrets |
| --- | --- | --- | --- |
| `openai_compatible` | OpenAI-compatible HTTP API | `TEXT_MODEL_ENDPOINT_URL`, `TEXT_MODEL_NAME`, timeout/retry limits | `config.local.env` (`ATO_TEXT_MODEL_API_KEY`) for local dev, or `TEXT_MODEL_CREDENTIAL_REFERENCE` in production |
| `aws_bedrock` | AWS Bedrock Converse API | `AWS_REGION`, `TEXT_MODEL_NAME` as the Bedrock model ID, timeout/retry limits | AWS credential chain only (`AWS_PROFILE`, standard AWS env vars, or instance role) |

Install Bedrock support when needed:

```powershell
pip install -e ".[bedrock]"
```

### Example configs

| File | Purpose |
| --- | --- |
| [`deployment/config/runtime-config.dev_local.openai.example.json`](../deployment/config/runtime-config.dev_local.openai.example.json) | Local OpenAI-compatible demo |
| [`deployment/config/runtime-config.dev_local.bedrock.example.json`](../deployment/config/runtime-config.dev_local.bedrock.example.json) | Local or work Bedrock demo |

Copy one example to your active dev config, for example:

```powershell
Copy-Item deployment\config\runtime-config.dev_local.openai.example.json deployment\config\runtime-config.dev_local.json
$env:ATO_RUNTIME_CONFIG_PATH = 'deployment\config\runtime-config.dev_local.json'
```

### OpenAI-compatible setup

```powershell
Copy-Item config.local.env.example config.local.env
# Edit config.local.env and set ATO_TEXT_MODEL_API_KEY=your-key
```

The service loads `config.local.env` at startup for dev-only secrets. Never commit or log the key.

### Bedrock setup

```powershell
pip install -e ".[bedrock]"
$env:AWS_PROFILE = 'your-profile'   # or use your normal AWS credentials
```

`AWS_REGION` in runtime JSON must match the Bedrock region you want to call, for example `us-east-1` or `us-gov-west-1`.

### Make a call

```python
from ato_service.runtime_config import load_runtime_config
from ato_service.text_llm import ChatMessage, build_text_model_client

config = load_runtime_config()
client = build_text_model_client(config)
text = client.complete([ChatMessage(role="user", content="Reply with one word: ok")])
```

## Local verification (PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

$env:ATO_DATABASE_DSN_FILE = 'C:\secure\ato-dsn.txt'
$env:ATO_RUNTIME_CONFIG_PATH = 'deployment\config\runtime-config.dev_local.json'

alembic upgrade head   # when PostgreSQL is available; not run in default CI
ato-service
```

Health checks:

```text
GET http://127.0.0.1:8000/health/live
GET http://127.0.0.1:8000/health/ready
```

With the pinned authority manifest still `draft` (HS-001 open), `/health/ready` typically returns HTTP `503` with `authority_manifest: degraded`. That is expected locally; it is not production-ready readiness.

Contract and service tests (no live Postgres required for default selection):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_contracts.py -q
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/ato_service -m "not integration" -q
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_deployment_contract.py -q
```

Optional live DB connectivity: set `ATO_TEST_DATABASE_URL` to run `tests/ato_service/test_db.py` integration cases.

## Operator CLI (`ato-operator`)

Workstream A ships a bounded operator CLI (`pyproject.toml` entrypoint `ato-operator`) for configuration validation and preflight on airgapped hosts. It reads the same schema-validated JSON selected by `--config` or `ATO_RUNTIME_CONFIG_PATH`. Secret values are never printed.

| Command | Purpose |
| --- | --- |
| `validate-config` | JSON Schema + semantic validation |
| `validate-credentials` | Active capability credential reference checks |
| `preflight` | Capability-aware dependency probes (DB, storage, ClamAV, IdP JWKS, allowlists, digests) |
| `migrate-db` | `alembic upgrade head` via `ATO_DATABASE_DSN_FILE` or `root_owned_file` DSN reference |
| `verify-migrations` | Compare alembic head to live DB revision (`--dry-run` skips DB) |
| `smoke` | Delegates to `scripts/smoke_service_chain.sh` |
| `verify-audit` | Ordered HMAC audit chain verification with root/checkpoint summary when PostgreSQL is reachable |
| `expire-approvals` | Transition `pending_approval` and unexported `approved` drafts past `APPROVAL_EXPIRY_DAYS` to `expired` |
| `qualification-check` | Qualification fixture presence only (does not close HS-001..009) |
| `print-checklist` | Operator onboarding checklist including open hard stops |

Example:

```text
ato-operator validate-config --config /etc/ato-analyzer/runtime-config.json
ato-operator preflight --config /etc/ato-analyzer/runtime-config.json
ato-operator verify-migrations --config /etc/ato-analyzer/runtime-config.json --dry-run
```

Inactive `PROCESS_CAPABILITIES` entries are skipped during preflight. Active invalid dependencies fail fast with redacted errors.

Focused tests: `tests/ato_operator/`.

## Related docs

- [`deployment/README.md`](../deployment/README.md) — API-only install assets and operator flow
- [`docs/OPERATIONS_AND_RECOVERY.md`](OPERATIONS_AND_RECOVERY.md) — full P-1 operations target
- [`README.md`](../README.md) — repository entry and current implementation scope
