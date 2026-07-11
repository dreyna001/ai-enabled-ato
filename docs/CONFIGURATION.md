# Runtime Configuration

**Status:** API-only scaffold documentation  
**Applies to:** Current `ato_service` API process; not a production release claim  
**Normative contract:** [`docs/contracts/runtime-config.schema.json`](contracts/runtime-config.schema.json)

This document explains how non-secret settings and secret references are loaded today. It does not claim RHEL validation, portal/worker deployment, auth implementation, extraction, model hosting, backup automation, or full P7 completion.

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

**`dev_local`** — minimal JSON for local API verification. Only `schema_version`, `runtime_profile`, and `STORAGE_DATA_PATH` are required. Optional model, identity, and backup fields are absent unless you add them. `VISION_MODEL_ENABLED` defaults to effective `false` when absent.

**`onprem_production`** — full non-secret contract. Schema requires text-model settings, explicit `VISION_MODEL_ENABLED`, identity, storage limits, audit references, malware-scanner declarations, and backup declarations. Secret values stay in credential files, not in JSON.

Copy `deployment/config/runtime-config.onprem.example.json` to `/etc/ato-analyzer/runtime-config.json`, redact customer placeholders, and tighten values with the customer authority. The installer never overwrites an existing live config.

## Capability and safety flags

There are no capability bundles or profile presets. Explicit JSON flags are the single source of truth.

Do not add a bundle/preset until at least three implemented optional capabilities create a demonstrated operator need and its precedence, migration, and observability rules are approved. Every new capability flag must be added with its schema, redacted example, semantic dependency validation, operator documentation, traceability, and deterministic tests in the same change.

| Key | Role | Notes |
| --- | --- | --- |
| `VISION_MODEL_ENABLED` | **Optional capability** | Only current optional model capability. Defaults to off when absent in `dev_local`. Required boolean in `onprem_production`. When `true`, schema requires vision endpoint URL, name, context tokens, and profile; production further restricts profile to qualified external/internal OpenAI-compatible values and may require `VISION_MODEL_CREDENTIAL_REFERENCE` and allowlist entries. |
| `TEXT_MODEL_PROVIDER` | **Text LLM backend** | `openai_compatible` (default) uses `TEXT_MODEL_ENDPOINT_URL` plus `TEXT_MODEL_CREDENTIAL_REFERENCE` or dev-only `ATO_TEXT_MODEL_API_KEY_FILE`. `aws_bedrock` uses `AWS_REGION`, `TEXT_MODEL_NAME` as the Bedrock model ID, and the standard AWS credential chain (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or instance role). Install Bedrock support with `pip install -e ".[bedrock]"`. |
| `ALLOW_LOOPBACK_HTTP_INTERNAL_ENDPOINTS` | **Safety exception** | Not a capability. Permits HTTP (not HTTPS) only for literal loopback IP endpoints (`127.0.0.1`, `::1`) on approved internal model profiles. Does not relax host allowlists for non-loopback hosts. |
| `LOCAL_PASSWORD_AUTH_ENABLED` | **Hard stop** | Must remain `false`. Declares that local password auth is forbidden; it is not a feature switch. |
| `MALWARE_SCANNER_*` | **Future production contract** | Declarations in JSON do not implement scanning, extraction, or scanner integration in the current API-only slice. |
| `BACKUP_*`, `AUDIT_*` backup fields | **Future production contract** | Declarations document intended recovery posture; they do not enable backup jobs, WAL archiving, or restore automation today. |

Text-model endpoints are required for `onprem_production` schema validity but full model-call pipelines remain out of scope for this API-only slice.

## Secrets

- Production DSN: JSON holds `DATABASE_DSN_CREDENTIAL_REFERENCE` with identifier `database-dsn`; systemd loads `/etc/ato-analyzer/credentials/database-dsn`.
- **Currently wired in production:** only `database-dsn` is mapped in [`deployment/systemd/ato-api.service`](../deployment/systemd/ato-api.service). Other credential references in `onprem_production` JSON (model, OIDC, audit HMAC, backup keys) are contract placeholders until matching `LoadCredential` lines and consumers exist.
- **Audit HMAC (P1.1):** when `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE` is present, the API process resolves the key bytes at startup through the credential reference only. There is no `ATO_AUDIT_HMAC_*` environment override. `dev_local` may omit the reference (audit appends are unavailable until configured). `onprem_production` requires the reference; missing, unreadable, or short keys fail startup or fail closed on mutating routes that depend on `get_audit_hmac_key`.
- **Package API authentication:** `/api/v1` package routes return HTTP `401` `authentication_required` without an injected authenticated principal (tests may override the dependency). OIDC/session runtime remains future work (**HS-003** / **EP-06**).
- Each future process receives only the credential mappings and config projection it consumes; declaring a reference in the shared schema does not authorize loading it into every service.
- Dev DSN: set `ATO_DATABASE_DSN_FILE` to a protected UTF-8 file containing only the SQLAlchemy PostgreSQL URL. Never commit or log DSN contents.
- Dev OpenAI API key: set `ATO_TEXT_MODEL_API_KEY_FILE` to a protected UTF-8 file containing only the API key when `TEXT_MODEL_PROVIDER` is `openai_compatible`.
- Bedrock: do not put AWS keys in runtime JSON. Use the normal AWS credential chain and set `AWS_REGION` in runtime JSON.

## Text LLM (OpenAI or Bedrock)

`ato_service.text_llm` is the current text-model API-call layer. It does not replace routing policy, run limits, or worker orchestration.

### Choose a provider

Set `TEXT_MODEL_PROVIDER` in runtime JSON:

| Value | Backend | Required settings | Secrets |
| --- | --- | --- | --- |
| `openai_compatible` | OpenAI-compatible HTTP API | `TEXT_MODEL_ENDPOINT_URL`, `TEXT_MODEL_NAME`, timeout/retry limits | `ATO_TEXT_MODEL_API_KEY_FILE` for local dev, or `TEXT_MODEL_CREDENTIAL_REFERENCE` in production |
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
$env:ATO_TEXT_MODEL_API_KEY_FILE = 'C:\secure\openai-api-key.txt'
```

The API key file must contain only the key bytes. Never commit or log it.

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

## Related docs

- [`deployment/README.md`](../deployment/README.md) — API-only install assets and operator flow
- [`docs/OPERATIONS_AND_RECOVERY.md`](OPERATIONS_AND_RECOVERY.md) — full P-1 operations target
- [`README.md`](../README.md) — repository entry and current implementation scope
