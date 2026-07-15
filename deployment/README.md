# Portal and API Deployment Scaffold

**Status:** Operator packaging for the `ato_service` API, authenticated portal, intake worker, and analyzer worker  
**Not claimed:** Production release, live RHEL 9 validation, production/customer file extraction, model hosting, backup automation, or P7 completion

This directory holds install assets and redacted configuration examples. Behavior is contract-tested in [`tests/test_deployment_contract.py`](../tests/test_deployment_contract.py) and [`tests/test_portal_contract.py`](../tests/test_portal_contract.py); passing those tests does not prove a customer host install.

## Assets

| Path | Purpose |
| --- | --- |
| [`config/runtime-config.onprem.example.json`](config/runtime-config.onprem.example.json) | Redacted `onprem_production` template (non-secret settings and credential references only) |
| [`config/runtime-config.dev_local.json`](config/runtime-config.dev_local.json) | Minimal dev profile referenced by local docs |
| [`config/runtime-config.dev_local.portal.example.json`](config/runtime-config.dev_local.portal.example.json) | Dev profile with loopback OIDC issuer and portal origin for local portal work |
| [`config/runtime-config.dev_local.e2e.json`](config/runtime-config.dev_local.e2e.json) | Schema-validated dev_local profile for bounded portal Playwright E2E (no secrets; credential paths materialized by `scripts/e2e-stack-start.sh`) |
| [`config/runtime-config.dev_local.openai.example.json`](config/runtime-config.dev_local.openai.example.json) | Dev example for OpenAI-compatible text LLM calls |
| [`config/runtime-config.dev_local.bedrock.example.json`](config/runtime-config.dev_local.bedrock.example.json) | Dev/work example for AWS Bedrock text LLM calls |
| [`systemd/ato-api.service`](systemd/ato-api.service) | Unprivileged API unit; pins config path, loopback bind, and API-consumed database/audit credentials |
| [`systemd/ato-intake-worker.service`](systemd/ato-intake-worker.service) | Long-running `ato-intake-worker` process (inactive until explicitly enabled) |
| [`systemd/ato-analyzer-worker.service`](systemd/ato-analyzer-worker.service) | Deterministic analyzer worker unit (inactive until operator enablement) |
| [`nginx/ato-api.conf`](nginx/ato-api.conf) | Inactive TLS edge template for health-only API exposure |
| [`nginx/ato-portal.conf`](nginx/ato-portal.conf) | Inactive TLS edge template serving the built React portal and proxying `/api/` |
| [`../portal/`](../portal/) | React/Vite portal source; build with `npm run build` before packaging |
| [`../scripts/install.sh`](../scripts/install.sh) | Root installer: layout, package copy, portal bundle, systemd/nginx assets |
| [`../scripts/upgrade.sh`](../scripts/upgrade.sh) | Bounded upgrade: drain workers, refresh package, migrate, restart API |
| [`../scripts/drain_workers.sh`](../scripts/drain_workers.sh) | Graceful worker stop before maintenance |
| [`../scripts/rollback.sh`](../scripts/rollback.sh) | Restore last install snapshot metadata (not database schema) |
| [`../scripts/verify_backup_contract.sh`](../scripts/verify_backup_contract.sh) | Fail-safe backup declaration checks (**HS-008**) |
| [`../scripts/prestage_airgap_deps.sh`](../scripts/prestage_airgap_deps.sh) | Offline wheel staging for airgap installs |
| [`../scripts/build_release.sh`](../scripts/build_release.sh) | Deterministic versioned release archive from allowlist |
| [`../scripts/verify_release.sh`](../scripts/verify_release.sh) | Offline archive verification (checksums, allowlist, schema) |
| [`../scripts/smoke_service_chain.sh`](../scripts/smoke_service_chain.sh) | Loopback (optional nginx) health smoke |
| [`../docs/RELEASE_PACKAGING.md`](../docs/RELEASE_PACKAGING.md) | Connected build and airgap verification guide |
| [`../docs/release/`](../docs/release/) | Version/config/credential/lifecycle/checklist/evidence indexes |
| [`../docs/contracts/validation-drill-record.schema.json`](../docs/contracts/validation-drill-record.schema.json) | Immutable customer validation drill record contract |
| [`../src/ato_operator/drill_handlers.py`](../src/ato_operator/drill_handlers.py) | `ato-operator run-drill`, `list-drills`, `validate-drill-record`, `write-drill-record` |
| [`../docs/CUSTOMER_ONBOARDING.md`](../docs/CUSTOMER_ONBOARDING.md) | Customer onboarding checklist |
| [`../docs/AIRGAP_PRESTAGE.md`](../docs/AIRGAP_PRESTAGE.md) | Airgap dependency prestage guide |

There is no model sidecar or timer in this slice. Worker systemd units ship **disabled** for implemented runtime acceptance; live production worker activation remains a later release gate.

## Host layout

```text
/etc/ato-analyzer/runtime-config.json          # customer production JSON (never overwritten by installer)
/etc/ato-analyzer/credentials/database-dsn     # root-owned DSN file (never overwritten)
/etc/ato-analyzer/credentials/audit-hmac-key   # root-owned audit key (never overwritten)
/etc/ato-analyzer/credentials/oidc-client-secret # root-owned OIDC client secret (never overwritten)
/opt/ato-analyzer/                             # application venv, package, alembic.ini, migrations/, contracts
/opt/ato-analyzer/portal/dist                  # built React portal static bundle
/var/ato-packages/                             # mutable package storage
/var/ato-packages/_tmp/                        # package staging scratch (service-writable)
/var/lib/ato/release/                          # install snapshot markers for rollback metadata
/var/lib/ato/validation-drill-records/         # optional immutable customer validation drill records
/etc/nginx/conf.d/ato-api.conf.example       # copied once; inactive until TLS promotion
/etc/nginx/conf.d/ato-portal.conf.example    # copied once; inactive until TLS promotion
```

Configuration is JSON-only. Do not introduce `config.env` or shell-source application settings on the host.

## Install flow

Run from the repository root on a RHEL 9-compatible host as root.

```bash
# 0. Build portal static bundle on a connected staging host
cd portal && npm ci && npm run build && cd ..

# 1. Install files and host layout only
sudo bash scripts/install.sh

# 2. Provision production config and secrets out of band (installer does not create these)
sudo install -o root -g ato -m 640 /path/to/customer/runtime-config.json /etc/ato-analyzer/runtime-config.json
sudo install -o root -g root -m 600 /path/to/dsn.txt /etc/ato-analyzer/credentials/database-dsn
sudo install -o root -g root -m 600 /path/to/audit-hmac-key /etc/ato-analyzer/credentials/audit-hmac-key
sudo install -o root -g root -m 600 /path/to/oidc-client-secret /etc/ato-analyzer/credentials/oidc-client-secret

# 3. Production-readiness: migrate, start, and smoke in one invocation
sudo bash scripts/install.sh --migrate --start --smoke
```

Provisioning means placing customer-approved configuration and secret values on the host through the approved secret-management or secure file-transfer process. The installer creates the directory layout but intentionally does not generate or overwrite production values:

| File | Required value and source |
| --- | --- |
| `runtime-config.json` | Non-secret customer settings derived from `deployment/config/runtime-config.onprem.example.json`; keep credential references in the JSON, never secret values. |
| `database-dsn` | Full SQLAlchemy PostgreSQL DSN issued by the customer database administrator or secrets platform. |
| `audit-hmac-key` | High-entropy application secret generated and retained by the approved secrets platform. |
| `oidc-client-secret` | Client secret issued when the ATO API/portal client is registered with the customer identity provider. |

Do not commit, print, or place these values in shell history. Verify ownership and modes before startup with `sudo stat -c '%U:%G %a %n' /etc/ato-analyzer/runtime-config.json /etc/ato-analyzer/credentials/*`; the expected modes are `root:ato 640` for runtime config and `root:root 600` for credentials.

Step 3 is the release gate. It reinstalls package bytes, runs `alembic upgrade head`, validates config and DSN, starts `ato-api.service`, and runs `scripts/smoke_service_chain.sh`. With the current draft authority manifest (HS-001 open), readiness returns HTTP **503** and this command fails unless the manifest is approved.

To smoke an already running API without reinstalling:

```bash
bash scripts/smoke_service_chain.sh
```

Installer flags (see `install.sh --help`):

| Flag | Effect |
| --- | --- |
| *(default)* | Copy app tree (including `alembic.ini` and `migrations/`), portal bundle when built, create directories, install systemd/nginx templates; **no** migrate, **no** start, **no** smoke |
| `--migrate` | Run `alembic upgrade head` from `/opt/ato-analyzer` using `ATO_DATABASE_DSN_FILE` |
| `--start` | Validate runtime config and DSN format, then enable and start `ato-api.service` |
| `--smoke` | Run `scripts/smoke_service_chain.sh` after install (**requires `--start` in the same invocation**) |
| `--skip-systemd` | Skip unit install |
| `--skip-nginx` | Skip nginx template install |

The installer never overwrites an existing `/etc/ato-analyzer/runtime-config.json`, credential file, or nginx example file. Worker units are installed but left disabled.

## Upgrade, drain, rollback, and backup contract

```bash
sudo bash scripts/drain_workers.sh
sudo bash scripts/upgrade.sh
sudo bash scripts/rollback.sh
sudo bash scripts/verify_backup_contract.sh
```

`upgrade.sh` first requires `verify_backup_contract.sh --pre-upgrade` to pass, records whether `ato-api.service` is active, drains workers, and invokes `install.sh` to refresh application bytes. By default it runs `alembic upgrade head` and restarts the API only when the API was active before the upgrade. It preserves runtime configuration and credentials, does not enable workers, and does not activate nginx.

Useful upgrade modes:

```bash
sudo bash scripts/upgrade.sh --dry-run      # validate the upgrade contract without host mutations
sudo bash scripts/upgrade.sh                # migrate and restart an API that was already active
sudo bash scripts/upgrade.sh --smoke        # also run the post-restart smoke chain
sudo bash scripts/upgrade.sh --no-migrate   # refresh package bytes without schema migration
sudo bash scripts/upgrade.sh --no-restart   # refresh/migrate without restarting the API
```

Backup verification reads `BACKUP_*` JSON declarations and fails safely when customer target or key ownership is not verified (**HS-008**). No backup vendor is selected by the product. A failed upgrade does not imply database rollback; `rollback.sh` restores install snapshot metadata and package state only, not database schema.

## systemd credentials

The shipped `ato-api.service` wires `database-dsn`, `audit-hmac-key`, and `oidc-client-secret`, the credentials consumed by the current API process when OIDC authentication is enabled. `ato-intake-worker.service` and `ato-analyzer-worker.service` consume database and audit credentials for their implemented runtime paths. Text-model and backup credential references remain declarations for later consumers; add matching `LoadCredential` mappings only when those processes or capabilities exist.

## nginx and TLS

`ato-api.conf` listens on `443` with placeholder certificates and proxies **only** `/health/live` and `/health/ready` to loopback `127.0.0.1:8000`. All other paths return `404`. Client-supplied identity headers are stripped at the edge.

`ato-portal.conf` serves the built portal from `/opt/ato-analyzer/portal/dist`, proxies `/api/` to the loopback API, and exposes the same health endpoints. Replace `server_name`, certificate paths, and validate `nginx -t` before reload. Install copies templates to `/etc/nginx/conf.d/*.conf.example`; rename or symlink before enabling a site.

## Smoke and readiness

Production smoke expects `GET /health/ready` to return HTTP **200** with exactly the five published readiness checks (`database`, `storage`, `authority_manifest`, `jobs`, `configuration`) all `ok`. Liveness must return exactly `{"status":"ok","checks":{"process":"ok"}}`.

While HS-001 keeps the authority manifest at `draft`, readiness correctly returns HTTP **503** with `error_code: reconciliation_required`, `instance: /health/ready`, and an `application/problem+json` body. For temporary operator checks only (not a release gate), accept degraded readiness:

```bash
ALLOW_DEGRADED_READY=true bash scripts/smoke_service_chain.sh
```

That path exits **0** but ends with `completed with degraded readiness; not release-ready`.

Optional edge check:

```bash
NGINX_BASE_URL=https://ato-api.customer.internal bash scripts/smoke_service_chain.sh
```

## WSL local deploy (developer only)

For a contained Linux environment on Windows with the same `/opt`, `/etc`, and
`/var` layout plus systemd, use [`docs/WSL_LOCAL_DEPLOY.md`](../docs/WSL_LOCAL_DEPLOY.md).
That path installs only what exists today:

- `ato-api.service` (WSL variant using `dev_local` runtime JSON under `/opt/ato-analyzer`)
- `ato-synthetic-intake-worker.service` + timer for P1.2 synthetic JSON intake

It does not claim RHEL validation, production release, nginx TLS edge, OIDC, or
customer extraction.

```bash
sudo bash scripts/wsl-local-deploy.sh
```

## Verification (repository)

Network-free deployment asset checks:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_deployment_contract.py tests/test_portal_contract.py -q
```

Full non-integration gate (includes contracts and service foundation):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest -m "not integration" -q
```

Bash syntax check (when bash is available) is included in `test_deployment_contract.py`.

## Configuration reference

See [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) for precedence, capability flags, dev vs production profiles, OIDC/session settings, and text LLM provider setup (`TEXT_MODEL_PROVIDER` for OpenAI-compatible or AWS Bedrock).

Customer onboarding: [`docs/CUSTOMER_ONBOARDING.md`](../docs/CUSTOMER_ONBOARDING.md). Airgap prestaging: [`docs/AIRGAP_PRESTAGE.md`](../docs/AIRGAP_PRESTAGE.md).
