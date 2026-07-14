# Release Packaging and Offline Verification

**Status:** Phase 6 operator guide  
**Architecture:** Deterministic allowlisted archives with checksum/SBOM evidence; no secret bytes or live customer config in packages

Phase 6 adds reproducible customer release packaging and offline verification without publication, upload, or signing side effects.

## Principles

1. **Explicit allowlist** — only approved source trees, deployment assets, operator scripts, contracts, authority bytes, qualification metadata, portal dist, and optional airgap wheels enter the archive.
2. **Deterministic output** — sorted members, fixed `SOURCE_DATE_EPOCH`, and stable gzip metadata for repeatable builds.
3. **Offline evidence** — `release/checksums.sha256`, `release/sbom.json`, and `release/package-manifest.json` ship inside every archive.
4. **Fail closed** — missing portal dist, unpinned airgap wheels, checksum tampering, traversal/symlink members, and secret-like paths fail verification.
5. **Signing is external** — tooling supports detached OpenPGP verification when `gpg` and a signature file are available; otherwise `signature_status=unavailable`.

## Connected staging workflow

```bash
# 1. Build portal static assets
cd portal && npm ci && npm run build && cd ..

# 2. Prestaged offline Python wheels with pinned digests (connected host only)
bash scripts/prestage_airgap_deps.sh

# 3. Build versioned release archive
bash scripts/build_release.sh

# 4. Verify before transfer
bash scripts/verify_release.sh dist/releases/ato-analyzer-*.tar.gz
```

Airgap-inclusive packaging:

```bash
bash scripts/build_release.sh --require-airgap
```

Optional detached signature verification after customer signing:

```bash
gpg --detach-sign --armor dist/releases/ato-analyzer-0.1.0.tar.gz
scripts/verify_release.sh \
  --signature dist/releases/ato-analyzer-0.1.0.tar.gz.asc \
  dist/releases/ato-analyzer-0.1.0.tar.gz
```

Equivalent operator CLI:

```bash
ato-operator build-release --require-airgap
ato-operator verify-release --archive dist/releases/ato-analyzer-0.1.0.tar.gz
```

## Airgap target workflow

```bash
# Verify prestaged wheels/manifest without network
bash scripts/prestage_airgap_deps.sh --verify-only

# Verify transferred archive before install
bash scripts/verify_release.sh /media/ato-analyzer-0.1.0.tar.gz
```

Install from extracted or copied release tree:

```bash
sudo bash scripts/install.sh
sudo install -o root -g ato -m 640 /path/to/runtime-config.json /etc/ato-analyzer/runtime-config.json
sudo install -o root -g root -m 600 /path/to/dsn.txt /etc/ato-analyzer/credentials/database-dsn
sudo install -o root -g root -m 600 /path/to/audit-hmac-key /etc/ato-analyzer/credentials/audit-hmac-key
sudo bash scripts/install.sh --migrate --start --smoke
```

## Package indexes (bundled under docs/release/)

| Index | Purpose |
| --- | --- |
| [`VERSION_INDEX.md`](release/VERSION_INDEX.md) | Version and signing posture |
| [`CONFIG_INDEX.md`](release/CONFIG_INDEX.md) | Runtime JSON templates and schema sources |
| [`CREDENTIAL_INDEX.md`](release/CREDENTIAL_INDEX.md) | Credential identifiers and provisioning |
| [`INSTALL_UPGRADE_ROLLBACK_INDEX.md`](release/INSTALL_UPGRADE_ROLLBACK_INDEX.md) | Lifecycle scripts |
| [`CHECKLIST_INDEX.md`](release/CHECKLIST_INDEX.md) | Operator checklist sources |
| [`EVIDENCE_INDEX.md`](release/EVIDENCE_INDEX.md) | Checksum, SBOM, and drill evidence map |

## What packaging does not include

- `.git`, caches, `node_modules`, dev secrets, live config/credentials, test databases, local storage, or customer data
- Release publication/upload or customer signing key generation
- Claims that archives are signed when no detached signature is supplied

See also [`AIRGAP_PRESTAGE.md`](AIRGAP_PRESTAGE.md) and [`CUSTOMER_ONBOARDING.md`](CUSTOMER_ONBOARDING.md).
