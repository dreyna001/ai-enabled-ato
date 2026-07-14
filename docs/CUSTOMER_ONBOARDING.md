# Customer Onboarding Checklist

**Status:** Delivered operator checklist (Phase 6)  
**Applies to:** RHEL 9-compatible single-node `onprem_production` installations  
**Normative sources:** [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md), [`OPERATIONS_AND_RECOVERY.md`](OPERATIONS_AND_RECOVERY.md), [`CONFIGURATION.md`](CONFIGURATION.md), [`RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md)

This checklist guides customer operators through safe staging of the implemented application, portal, systemd, and nginx assets. It does not claim production readiness while open hard stops remain.

## 1. Host prerequisites

- [ ] RHEL 9-compatible x86_64 host with SELinux enforcing
- [ ] PostgreSQL 16 reachable on loopback or a Unix socket
- [ ] Python 3.12 available for `/opt/ato-analyzer/venv`
- [ ] nginx installed; **no** active `ato-*.conf` site until TLS placeholders are replaced
- [ ] Outbound egress limited to customer-approved IdP, model endpoints, and backup destinations
- [ ] Service identity `ato` reserved (installer creates if missing)

## 2. Configuration and credentials (out of band)

Runtime settings live in JSON only. Secret bytes live in credential files referenced from JSON.

| Item | Path or action | Notes |
| --- | --- | --- |
| Production runtime JSON | `/etc/ato-analyzer/runtime-config.json` | Copy from `deployment/config/runtime-config.onprem.example.json`; installer never overwrites an existing file |
| Database DSN | `/etc/ato-analyzer/credentials/database-dsn` | Root-owned `600`; referenced as `database-dsn` |
| Audit HMAC key | `/etc/ato-analyzer/credentials/audit-hmac-key` | Root-owned `600`, at least 32 bytes; referenced as `audit-hmac-key` |
| TLS certificates | Customer-managed paths in nginx templates | Replace placeholders before enabling sites |
| Additional credentials | Only when consumed by a running process | Add `LoadCredential` mappings in systemd when a process exists |

Do not introduce `config.env` or per-setting environment overrides for application behavior.

## 3. Package and portal staging

From the release tree on a connected staging host:

```bash
# Build portal static bundle before packaging
cd portal && npm ci && npm run build && cd ..

# Install application, portal dist, systemd units, and inactive nginx examples
sudo bash scripts/install.sh
```

The installer:

- Copies fresh package bytes to `/opt/ato-analyzer`
- Stages `portal/dist` when built
- Installs `ato-api`, `ato-intake-worker`, and `ato-analyzer-worker` units
- Leaves worker units **disabled**
- Copies nginx templates to `/etc/nginx/conf.d/*.conf.example` only once
- Never overwrites live runtime JSON or credential files

## 4. Database migration and API start

```bash
sudo bash scripts/install.sh --migrate --start --smoke
```

Readiness returns HTTP **503** while **HS-001** keeps the authority manifest at `draft`. For operator checks only:

```bash
ALLOW_DEGRADED_READY=true bash scripts/smoke_service_chain.sh
```

## 5. nginx TLS promotion (manual)

1. Replace `server_name` and certificate paths in the `.conf.example` files.
2. Validate: `sudo nginx -t`
3. Rename or symlink to active `ato-portal.conf` / `ato-api.conf`.
4. Reload nginx.

Do not enable nginx automatically from installer or upgrade scripts.

## 6. Worker enablement (after acceptance tests)

Worker units ship inactive:

```bash
# Only after implemented runtime acceptance tests pass for the target profile
sudo systemctl enable --now ato-intake-worker.service
sudo systemctl enable --now ato-analyzer-worker.service
```

Drain before maintenance:

```bash
sudo bash scripts/drain_workers.sh
```

## 7. Upgrade and rollback

```bash
# Bounded upgrade: drain workers, refresh package, migrate, restart API if active
sudo bash scripts/upgrade.sh

# Metadata-only rollback marker restore; database may require full restore drill
sudo bash scripts/rollback.sh
```

## 8. Backup contract (customer-selected target)

Backup behavior is declared in runtime JSON (`BACKUP_*` keys). The product does not select a backup vendor.

```bash
sudo bash scripts/verify_backup_contract.sh
```

Fails safely when `BACKUP_OFF_HOST_ENABLED=true` but customer target, key ownership, or encryption key credential are not verified (**HS-008**).

## 9. Hard stops that block production claims

| ID | Blocks |
| --- | --- |
| HS-001 | Authority-dependent release |
| HS-003 | Production identity deployment |
| HS-004 | Real customer model calls |
| HS-005 | Customer file extraction |
| HS-008 | Production readiness (backup target and key ownership) |
| HS-009 | Complete Class C package readiness claims |

Complete the checklist items that apply to your scope; do not infer missing customer inputs.

## 10. Customer validation drills

After configuration and credentials are staged, run bounded validation drills and persist immutable records for customer evidence:

```bash
ato-operator list-drills --json
ato-operator run-drill smoke-readiness --config /etc/ato-analyzer/runtime-config.json --write-record --records-root /var/lib/ato/validation-drill-records --operator-id operator@customer.local
ato-operator run-drill model-routing-policy-block --config /etc/ato-analyzer/runtime-config.json --write-record --records-root /var/lib/ato/validation-drill-records
```

- Default mode is `dry_run`. Add `--live` only on the target host when dependencies are available.
- Live identity, ClamAV, backup/restore, and worker crash drills skip or fail explicitly when infrastructure is missing; they do not close **HS-003**, **HS-005**, or **HS-008** from repository mocks.
- Destructive drills require `--isolated-target` on an isolated host.

Validate persisted records before submission:

```bash
ato-operator validate-drill-record /var/lib/ato/validation-drill-records/records/<drill_id>/<record_id>.json
```

## 11. Airgap installations

See [`AIRGAP_PRESTAGE.md`](AIRGAP_PRESTAGE.md) for offline wheel staging and portal prebuild on a connected bastion.

## Verification (repository)

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3.12 -m pytest tests/test_deployment_contract.py tests/test_portal_contract.py -q
```
