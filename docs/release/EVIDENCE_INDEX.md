# Release Evidence Index

**Purpose:** Map offline evidence artifacts produced during release build and verification.

## Archive evidence (inside `release/` prefix)

| File | Purpose |
| --- | --- |
| `release/package-manifest.json` | Allowlist, build metadata, migration head, portal/airgap requirements |
| `release/checksums.sha256` | SHA-256 digest manifest for every bundled file |
| `release/sbom.json` | Practical SBOM from `pyproject.toml` and `portal/package-lock.json` |

## Airgap evidence (when prestaged)

| File | Purpose |
| --- | --- |
| `dist/airgap/manifest.json` | Pinned wheel, portal lock, and optional portal dist digests |
| `dist/airgap/wheels/*.whl` | Offline Python dependencies |

## Qualification and authority evidence

| Path | Purpose |
| --- | --- |
| `data/qualification/manifest.json` | Sealed qualification corpus with digests |
| `docs/contracts/authority-manifest.json` | Pinned authority metadata (**HS-001** review remains open while `status=draft`) |
| `reference/authorities/` | Vendored authority bytes referenced by the manifest |
| `reference/profiles/*.json` | Bundled draft analysis profiles (`qualification_status=draft`; **HS-001** blocks qualified claims) |
| `scripts/compile_analysis_profiles.py` | Regenerate/check bundled draft profiles before release build |
| `scripts/compile_fisma_analysis_profile.py` | Compile customer FISMA agency profile as deployment input (not bundled by default) |

## Profile regeneration and verification commands

```bash
python scripts/compile_analysis_profiles.py --check
python scripts/compile_fisma_analysis_profile.py --help
scripts/verify_release.sh dist/releases/ato-analyzer-<version>.tar.gz
```

Release build fails closed when bundled profiles are missing or differ from deterministic regeneration. Offline archive verification checks presence, checksum manifest alignment, JSON parse, schema validation, and that `qualification_status` remains `draft`.

## AI evaluation evidence (optional)

Immutable AI qualification records use `docs/contracts/ai-evaluation-record.schema.json`. Writing records requires an operator-supplied safe root; records are never generated inside release archives.

## Verification commands

```bash
scripts/verify_release.sh dist/releases/ato-analyzer-<version>.tar.gz
ato-operator verify-release --archive dist/releases/ato-analyzer-<version>.tar.gz
```

Optional detached signature verification:

```bash
scripts/verify_release.sh --signature ato-analyzer-<version>.tar.gz.asc dist/releases/ato-analyzer-<version>.tar.gz
```

When signing keys are unavailable, verification reports `signature_status: unavailable` and still performs checksum, allowlist, schema, and secret-exclusion checks.
