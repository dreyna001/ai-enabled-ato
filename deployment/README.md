# API-Only Deployment Scaffold

**Status:** Initial operator packaging for the `ato_service` API process only  
**Not claimed:** Production release, live RHEL 9 validation, portal UI, analyzer workers, OIDC auth, production/customer file extraction, model hosting, backup automation, or P7 completion

This directory holds install assets and redacted configuration examples. Behavior is contract-tested in [`tests/test_deployment_contract.py`](../tests/test_deployment_contract.py); passing those tests does not prove a customer host install.

## Assets

| Path | Purpose |
| --- | --- |
| [`config/runtime-config.onprem.example.json`](config/runtime-config.onprem.example.json) | Redacted `onprem_production` template (non-secret settings and credential references only) |
| [`config/runtime-config.dev_local.json`](config/runtime-config.dev_local.json) | Minimal dev profile referenced by local docs |
| [`config/runtime-config.dev_local.openai.example.json`](config/runtime-config.dev_local.openai.example.json) | Dev example for OpenAI-compatible text LLM calls |
| [`config/runtime-config.dev_local.bedrock.example.json`](config/runtime-config.dev_local.bedrock.example.json) | Dev/work example for AWS Bedrock text LLM calls |
| [`systemd/ato-api.service`](systemd/ato-api.service) | Unprivileged API unit; pins config path, loopback bind, and API-consumed database/audit credentials |
| [`nginx/ato-api.conf`](nginx/ato-api.conf) | Inactive TLS edge template; customer must replace host/cert placeholders before enablement |
| [`../scripts/install.sh`](../scripts/install.sh) | Root installer: layout, package copy, systemd/nginx assets |
| [`../scripts/smoke_service_chain.sh`](../scripts/smoke_service_chain.sh) | Loopback (optional nginx) health smoke |

There is no worker unit, portal static root, model sidecar, or timer in this
slice. The application package includes the bounded
`ato-synthetic-intake-worker` drain command for `dev_local` synthetic JSON
fixtures, but the installer does not deploy, configure, start, or credential
that development-only process. It refuses production profiles and does not
close **HS-005**.

## Host layout

```text
/etc/ato-analyzer/runtime-config.json          # customer production JSON (never overwritten by installer)
/etc/ato-analyzer/credentials/database-dsn     # root-owned DSN file (never overwritten)
/etc/ato-analyzer/credentials/audit-hmac-key   # root-owned audit key (never overwritten)
/opt/ato-analyzer/                             # application venv, package, alembic.ini, migrations/, contracts
/var/ato-packages/                             # mutable package storage
/var/ato-packages/_tmp/                        # package staging scratch (service-writable)
/etc/nginx/conf.d/ato-api.conf.example       # copied once; inactive until TLS promotion
```

Configuration is JSON-only. Do not introduce `config.env` or shell-source application settings on the host.

## Install flow

Run from the repository root on a RHEL 9-compatible host as root.

```bash
# 1. Install files and host layout only
sudo bash scripts/install.sh

# 2. Provision production config and secrets out of band (installer does not create these)
sudo install -o root -g ato -m 640 /path/to/customer/runtime-config.json /etc/ato-analyzer/runtime-config.json
sudo install -o root -g root -m 600 /path/to/dsn.txt /etc/ato-analyzer/credentials/database-dsn
sudo install -o root -g root -m 600 /path/to/audit-hmac-key /etc/ato-analyzer/credentials/audit-hmac-key

# 3. Production-readiness: migrate, start, and smoke in one invocation
sudo bash scripts/install.sh --migrate --start --smoke
```

Step 3 is the release gate. It reinstalls package bytes, runs `alembic upgrade head`, validates config and DSN, starts `ato-api.service`, and runs `scripts/smoke_service_chain.sh`. With the current draft authority manifest (HS-001 open), readiness returns HTTP **503** and this command fails unless the manifest is approved.

To smoke an already running API without reinstalling:

```bash
bash scripts/smoke_service_chain.sh
```

Installer flags (see `install.sh --help`):

| Flag | Effect |
| --- | --- |
| *(default)* | Copy app tree (including `alembic.ini` and `migrations/`), create directories, install systemd/nginx templates; **no** migrate, **no** start, **no** smoke |
| `--migrate` | Run `alembic upgrade head` from `/opt/ato-analyzer` using `ATO_DATABASE_DSN_FILE` |
| `--start` | Validate runtime config and DSN format, then enable and start `ato-api.service` |
| `--smoke` | Run `scripts/smoke_service_chain.sh` after install (**requires `--start` in the same invocation**) |
| `--skip-systemd` | Skip unit install |
| `--skip-nginx` | Skip nginx template install |

The installer never overwrites an existing `/etc/ato-analyzer/runtime-config.json`, database DSN credential file, or nginx example file.

Repeated installs copy fresh package bytes and reinstall `ato_service` from `/opt/ato-analyzer` without force-upgrading all dependencies or unconditionally upgrading pip.

## systemd credentials

The shipped `ato-api.service` wires `database-dsn` and `audit-hmac-key`, the two credentials consumed by the current API process. The audit key must contain at least 32 bytes. Text-model, OIDC, and backup credential references remain declarations for later consumers; add matching `LoadCredential` mappings only when those processes or capabilities exist.

## nginx and TLS

The nginx template listens on `443` with placeholder certificates and proxies **only** `/health/live` and `/health/ready` to loopback `127.0.0.1:8000`. All other paths return `404`. Replace `server_name`, certificate paths, and validate `nginx -t` before reload. Install copies the template to `/etc/nginx/conf.d/ato-api.conf.example`; rename or symlink to `ato-api.conf` before enabling the site.

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

## Verification (repository)

Network-free deployment asset checks:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest tests/test_deployment_contract.py -q
```

Full non-integration gate (includes contracts and service foundation):

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; py -3.12 -m pytest -m "not integration" -q
```

Bash syntax check (when bash is available) is included in `test_deployment_contract.py`.

## Configuration reference

See [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) for precedence, capability flags, dev vs production profiles, and text LLM provider setup (`TEXT_MODEL_PROVIDER` for OpenAI-compatible or AWS Bedrock).
