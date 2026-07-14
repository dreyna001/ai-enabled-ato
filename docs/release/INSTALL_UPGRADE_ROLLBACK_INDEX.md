# Install, Upgrade, and Rollback Index

**Purpose:** Map bounded lifecycle scripts bundled in release archives.

## Install

| Script | Role |
| --- | --- |
| `scripts/install.sh` | Root installer: host layout, application tree, portal bundle, systemd/nginx templates |
| `scripts/smoke_service_chain.sh` | Loopback health smoke (`/health/live`, `/health/ready`) |

Typical production sequence:

```bash
sudo bash scripts/install.sh
# provision /etc/ato-analyzer/runtime-config.json and credentials out of band
sudo bash scripts/install.sh --migrate --start --smoke
```

## Upgrade and drain

| Script | Role |
| --- | --- |
| `scripts/drain_workers.sh` | Graceful worker stop before maintenance |
| `scripts/upgrade.sh` | Drain, refresh package bytes, optional migrate, restart API |

## Rollback and backup contract

| Script | Role |
| --- | --- |
| `scripts/rollback.sh` | Restore last install snapshot metadata (not database schema) |
| `scripts/verify_backup_contract.sh` | Fail-safe backup declaration checks (**HS-008**) |

## Airgap prestaging

| Script | Role |
| --- | --- |
| `scripts/prestage_airgap_deps.sh` | Connected-host wheel download with pinned digests |
| `scripts/build_release.sh` | Deterministic versioned archive from allowlist |
| `scripts/verify_release.sh` | Offline archive verification before transfer/install |

## Operator CLI (`ato-operator`)

Installed into `/opt/ato-analyzer/venv` during `install.sh`. Key lifecycle commands:

- `validate-config`, `validate-credentials`, `preflight`
- `migrate-db`, `verify-migrations`
- `verify-release` (also available pre-install via `scripts/verify_release.sh`)
- `run-drill`, `qualification-check`, `print-checklist`

See [`OPERATIONS_AND_RECOVERY.md`](../OPERATIONS_AND_RECOVERY.md) for recovery principles and drill evidence requirements.
