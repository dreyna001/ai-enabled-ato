# Airgap Dependency Prestaging

**Status:** Delivered operator guide (Phase 6)  
**Architecture:** JSON runtime settings + out-of-band credential files (no secret bytes in bundles)  
**Release evidence:** [`RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md)

Air-gapped hosts cannot reach public package indexes during install. Prestaging on a connected staging bastion preserves the same runtime contract used by connected installs.

## Principles

1. **Non-secret settings in JSON** — copy `deployment/config/runtime-config.onprem.example.json` to `/etc/ato-analyzer/runtime-config.json` and redact customer placeholders on the target host.
2. **Secret bytes in credential files** — provision `database-dsn`, `audit-hmac-key`, and any additional identifiers only on the target host with root-owned permissions.
3. **No vendor lock-in for backup** — `BACKUP_*` JSON fields declare intent; the customer selects and verifies the backup target separately (**HS-008**).
4. **Inactive edge by default** — nginx templates ship as `*.conf.example`; operators promote them after TLS material exists.

## Connected bastion workflow

```bash
# From repository root on a connected staging host
bash scripts/prestage_airgap_deps.sh

# Build portal static assets for packaging
cd portal && npm ci && npm run build && cd ..

# Build and verify deterministic release archive
bash scripts/build_release.sh --require-airgap
bash scripts/verify_release.sh dist/releases/ato-analyzer-*.tar.gz
```

This creates:

```text
dist/airgap/
  wheels/          # pip-downloaded dependencies with pinned SHA-256 digests
  manifest.json    # wheel, portal lock, and optional portal dist digests
dist/releases/
  ato-analyzer-<version>.tar.gz
  release/checksums.sha256, release/sbom.json inside the archive
```

Transfer the verified release archive (or extracted tree) to the airgap host through customer-approved media. See [`RELEASE_PACKAGING.md`](RELEASE_PACKAGING.md) for full connected and airgap target steps.

## Target host install

```bash
# Verify transferred archive offline (recommended)
bash scripts/verify_release.sh /media/ato-analyzer-<version>.tar.gz

# Verify prestaged wheels/manifest without network
bash scripts/prestage_airgap_deps.sh --verify-only

# Create venv and install from local wheels only
sudo python3.12 -m venv /opt/ato-analyzer/venv
sudo /opt/ato-analyzer/venv/bin/pip install \
  --no-index --find-links dist/airgap/wheels /opt/ato-analyzer

# Or use the installer after copying the full release tree
sudo bash scripts/install.sh
```

Provision runtime JSON and credentials before `--migrate` or `--start`:

```bash
sudo install -o root -g ato -m 640 /path/to/runtime-config.json /etc/ato-analyzer/runtime-config.json
sudo install -o root -g root -m 600 /path/to/dsn.txt /etc/ato-analyzer/credentials/database-dsn
sudo install -o root -g root -m 600 /path/to/audit-hmac-key /etc/ato-analyzer/credentials/audit-hmac-key
```

## Post-install checks

```bash
sudo bash scripts/verify_backup_contract.sh
sudo bash scripts/install.sh --migrate --start
bash scripts/smoke_service_chain.sh
```

## What prestaging does not include

- Customer IdP values (**HS-003**)
- Malware scanner integration (**HS-005**)
- Model endpoint approval (**HS-004**)
- Backup target verification (**HS-008**)
- TLS certificates (customer PKI)

Those remain explicit customer onboarding steps in [`CUSTOMER_ONBOARDING.md`](CUSTOMER_ONBOARDING.md).
