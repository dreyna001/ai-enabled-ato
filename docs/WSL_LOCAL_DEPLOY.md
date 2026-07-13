# WSL Local Deploy

Run the **implemented** ATO stack inside Windows WSL with production-shaped host paths and systemd:

- API (`ato-api.service`)
- Unified intake worker (`ato-synthetic-intake-worker.service` + 30s timer; drains
  `ato_service.intake` through the preserved `ato-synthetic-intake-worker` alias)

This is a developer-only WSL bootstrap. It is not RHEL validation, not a production release, and does not implement OIDC, production malware scanning (**HS-005**), or production customer extraction.

## Prerequisites

1. **WSL 2** with **systemd enabled**. In `/etc/wsl.conf`:

   ```ini
   [boot]
   systemd=true
   ```

   Restart WSL from PowerShell:

   ```powershell
   wsl --shutdown
   ```

2. **Repository checkout inside WSL**, not only on the Windows mount. Clone or copy the repo to your Linux home, for example `~/ai-enabled-ato`.

3. **Ubuntu/Debian-based WSL** for automatic package install (PostgreSQL, Python 3.12).

## One-command install

From the repository root inside WSL:

```bash
sudo bash scripts/wsl-local-deploy.sh
```

The script:

1. Installs PostgreSQL and Python 3.12 (apt)
2. Creates local `ato` database role/database with generated credentials
3. Runs `scripts/install.sh --skip-nginx --skip-systemd` for `/opt`, `/etc`, `/var` layout
4. Installs `deployment/config/runtime-config.wsl_local.json` to `/opt/ato-analyzer/runtime-config.json`
5. Bind-mounts `/var/ato-packages` to the dev_local storage path under `/opt/ato-analyzer/data/ato-storage`
6. Installs WSL systemd units and runs migrations
7. Starts the API and synthetic worker timer
8. Runs smoke checks with `ALLOW_DEGRADED_READY=true` (HS-001 draft manifest)

## Host layout (inside WSL)

Same paths as production packaging:

```text
/opt/ato-analyzer/                 application venv, package, migrations, runtime-config.json
/etc/ato-analyzer/credentials/     database-dsn, audit-hmac-key (generated; never commit)
                                   after portal enable: oidc-client-secret, ato-local.env
/var/ato-packages/                 mutable package storage (bind-mounted into app storage path)
/etc/systemd/system/ato-api.service
/etc/systemd/system/ato-synthetic-intake-worker.service
/etc/systemd/system/ato-synthetic-intake-worker.timer
```

Runtime profile is **`dev_local`** so the API and synthetic worker share one config and storage layout. This is intentional for the current P1.2 slice; it is not `onprem_production`.

## Verify

```bash
curl -sS http://127.0.0.1:8001/health/live
curl -sS http://127.0.0.1:8001/health/ready
systemctl status ato-api.service
systemctl status ato-synthetic-intake-worker.timer
journalctl -u ato-api -n 50 --no-pager
```

WSL local binds the API to **8001** (not 8000) so a Windows-side dev server can keep the default loopback port.

Expect `/health/ready` to return HTTP **503** with `reconciliation_required` until HS-001 closes. Liveness should return HTTP **200**.

## Manual worker drain

The timer runs every 30 seconds. To drain immediately after finalizing a revision:

```bash
sudo systemctl start ato-synthetic-intake-worker.service
journalctl -u ato-synthetic-intake-worker -n 50 --no-pager
```

## Re-run after WSL restart

Bind mounts do not persist across WSL restarts. Re-bind and restart:

```bash
cd ~/ai-enabled-ato
sudo mount --bind /var/ato-packages /opt/ato-analyzer/data/ato-storage
sudo systemctl restart ato-api.service
sudo systemctl restart ato-synthetic-intake-worker.timer
```

Or rerun the full deploy script (idempotent for credentials unless regenerated).

## Options

```bash
sudo bash scripts/wsl-local-deploy.sh --no-smoke
sudo bash scripts/wsl-local-deploy.sh --no-start --no-migrate
sudo bash scripts/wsl-local-deploy.sh --help
```

## Enable portal + OpenAI (optional)

After the base WSL install succeeds, enable OIDC dev auth, portal sessions, and OpenAI text-model settings:

```bash
cp config.local.env.example config.local.env
# edit config.local.env and set ATO_TEXT_MODEL_API_KEY=your-key
sudo bash scripts/wsl-portal-enable.sh
```

### OpenAI API key (`config.local.env`)

1. Copy [`config.local.env.example`](../config.local.env.example) to `config.local.env` at the repository root (`config.local.env` is gitignored).
2. Set your key on one line:

   ```env
   ATO_TEXT_MODEL_API_KEY=your-openai-api-key-here
   ```

3. Run `sudo bash scripts/wsl-portal-enable.sh` from the repo root inside WSL.

The script installs `/etc/ato-analyzer/credentials/ato-local.env` (mode `600`, root-owned). The WSL API unit loads it with `EnvironmentFile=`. OpenAI settings live in `deployment/config/runtime-config.wsl_portal.json`.

Optional override for the source file path: `sudo ATO_LOCAL_ENV_FILE=/path/to/config.local.env bash scripts/wsl-portal-enable.sh`

**Model:** `gpt-4.1` at `https://api.openai.com/v1` (`TEXT_MODEL_ENDPOINT_PROFILE`: `external_openai`). Production on-prem config is unchanged.

**Portal deterministic runs:** The portal **Start deterministic run** workflow does not call the text LLM today (`llm_call_count=0`). OpenAI config enables `text_llm` for dev and future model-backed paths; it does not change deterministic run behavior.

### Portal UI (Windows)

From the repository on Windows (not inside WSL):

```powershell
cd portal
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies API calls to the WSL API on `http://127.0.0.1:8001`.

Synthetic demo package walkthrough: [`data/synthetic-packages/fisma-demo-portal/README.md`](../data/synthetic-packages/fisma-demo-portal/README.md).

## Out of scope

- Production `onprem_production` profile enforcement
- nginx TLS edge
- Production OIDC (dev OIDC issuer on loopback only)
- Production malware scanner / customer extraction (HS-005)
- Analyzer worker

See [`deployment/README.md`](../deployment/README.md) for the RHEL operator packaging contract.
