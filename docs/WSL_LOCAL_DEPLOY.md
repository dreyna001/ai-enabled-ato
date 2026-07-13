# WSL Local Deploy

Run the **implemented** ATO stack inside Windows WSL with production-shaped host paths and systemd:

- API (`ato-api.service`)
- Synthetic JSON intake worker (`ato-synthetic-intake-worker.service` + 30s timer)

This is a developer-only WSL bootstrap. It is not RHEL validation, not a production release, and does not implement OIDC, production malware scanning, or customer extraction.

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

## Out of scope

- Production `onprem_production` profile enforcement
- nginx TLS edge
- OIDC/session auth (HS-003)
- Production malware scanner / customer extraction (HS-005)
- Analyzer worker or portal UI

See [`deployment/README.md`](../deployment/README.md) for the RHEL operator packaging contract.
