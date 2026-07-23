# Release Packaging and Offline Verification

**Status:** Phase 6 operator guide  
**Architecture:** Deterministic allowlisted archives with checksum/SBOM evidence; no secret bytes or live customer config in packages

Phase 6 adds reproducible customer release packaging and offline verification without publication, upload, or signing side effects.

## Principles

1. **Explicit allowlist** — only approved source trees, deployment assets, operator scripts, contracts, authority bytes, bundled draft analysis profiles, qualification metadata, portal dist, and optional airgap wheels enter the archive.
2. **Deterministic output** — sorted members, fixed `SOURCE_DATE_EPOCH`, and stable gzip metadata for repeatable builds.
3. **Offline evidence** — `release/checksums.sha256`, `release/sbom.json`, and `release/package-manifest.json` ship inside every archive.
4. **Fail closed** — missing portal dist, unpinned airgap wheels, missing or drifted bundled analysis profiles, checksum tampering, traversal/symlink members, symlinked source-tree paths during packaging, declared tar member size limits (256 MiB per member, 2 GiB aggregate uncompressed), and secret-like paths fail verification.
5. **Signing is external** — tooling supports detached OpenPGP verification when `gpg` and a signature file are available; otherwise `signature_status=unavailable`.

## Connected staging workflow

```bash
# 1. Build portal static assets
cd portal && npm ci && npm run build && cd ..

# 2. Verify bundled draft analysis profiles match deterministic generation
python scripts/compile_analysis_profiles.py --check

# 3. Prestaged offline Python wheels with pinned digests (connected host only)
bash scripts/prestage_airgap_deps.sh

# 4. Build versioned release archive
bash scripts/build_release.sh

# 5. Verify before transfer
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

## Bundled analysis profiles

Every customer release ships four deterministic **draft** analysis profiles under `reference/profiles/`:

| File | Purpose |
| --- | --- |
| `fedramp-20x-program-class-c.json` | FedRAMP 20x Program Class C draft profile |
| `fedramp-rev5-transition-low.json` | FedRAMP Rev5 transition draft profile (low) |
| `fedramp-rev5-transition-moderate.json` | FedRAMP Rev5 transition draft profile (moderate) |
| `fedramp-rev5-transition-high.json` | FedRAMP Rev5 transition draft profile (high) |

Regenerate or verify committed artifacts before packaging:

```bash
python scripts/compile_analysis_profiles.py
python scripts/compile_analysis_profiles.py --check
```

After qualified SME review and authority manifest approval, regenerate and verify bundled profiles as qualified:

```bash
python scripts/compile_analysis_profiles.py --qualification-status qualified
python scripts/compile_analysis_profiles.py --qualification-status qualified --check
```

Release build invokes the same deterministic check and fails closed when committed files are missing or drift from regeneration.

**HS-001 warning:** bundled profiles remain `qualification_status=draft` in the current repository. Offline verification reports this explicitly; they must not be represented as qualified until **HS-001** authority review closes and qualified regeneration succeeds. Qualified regeneration commands fail while the manifest remains `draft`.

Customer FISMA agency profiles are **deployment inputs**, not bundled release artifacts. Compile per deployment with inventory and authority inputs:

```bash
python scripts/compile_fisma_analysis_profile.py \
  --inventory /path/to/fisma-control-inventory.json \
  --output /path/to/fisma-agency-security-profile.json
```

After qualified SME review and authority manifest approval, compile with approved inventory and matching qualification status, then verify:

```bash
python scripts/compile_fisma_analysis_profile.py \
  --inventory /path/to/fisma-control-inventory-approved.json \
  --output /path/to/fisma-agency-security-profile.json \
  --require-approved-inventory \
  --qualification-status qualified
python scripts/compile_fisma_analysis_profile.py \
  --inventory /path/to/fisma-control-inventory-approved.json \
  --output /path/to/fisma-agency-security-profile.json \
  --require-approved-inventory \
  --qualification-status qualified \
  --check
```

The current repository manifest remains `draft`; qualified FISMA compiles fail until manifest approval is recorded.

Point runtime config at the generated profile path on the target host; do not expect a default FISMA profile inside the release archive.

## What packaging does not include

- `.git`, caches, `node_modules`, dev secrets, live config/credentials, test databases, local storage, or customer data
- Customer FISMA agency profiles (compiled per deployment from operator-supplied inventory)
- Release publication/upload or customer signing key generation
- Claims that archives are signed when no detached signature is supplied

See also [`AIRGAP_PRESTAGE.md`](AIRGAP_PRESTAGE.md) and [`CUSTOMER_ONBOARDING.md`](CUSTOMER_ONBOARDING.md).
