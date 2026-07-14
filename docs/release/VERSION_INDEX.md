# Release Version Index

**Purpose:** Static, non-mutable release metadata bundled with customer packages.  
**Source of truth:** `pyproject.toml` (`project.version`) and `release/package-manifest.json` inside the archive.

## Application version

| Field | Location | Notes |
| --- | --- | --- |
| Python package version | `pyproject.toml` → `[project].version` | Drives archive naming (`ato-analyzer-<version>.tar.gz`) |
| Portal package version | `portal/package-lock.json` → root `version` | Static bundle only; portal source is not shipped in release archives |
| Schema contracts | `docs/contracts/*.schema.json` | Published contract versions are independent of application semver |

## Build identity

The release builder records immutable build metadata in `release/package-manifest.json`:

- `package_version` — application semver from `pyproject.toml`
- `source_date_epoch` — deterministic archive timestamp (defaults to `SOURCE_DATE_EPOCH` or `1700000000`)
- `git_revision` — optional builder-supplied revision string (`RELEASE_GIT_REVISION`); never read from `.git` inside the archive
- `builder` — tool identifier (`ato-operator release-packaging`)

## Signature status

Release archives are **not** claimed signed by the build tooling. When customer signing keys are available out of band:

1. Generate a detached signature: `gpg --detach-sign --armor ato-analyzer-<version>.tar.gz`
2. Verify before install: `gpg --verify ato-analyzer-<version>.tar.gz.asc ato-analyzer-<version>.tar.gz`

If no detached signature file is supplied, `scripts/verify_release.sh` and `ato-operator verify-release` report `signature_status: unavailable` and do not claim authenticity.

## Related operator docs

- [`RELEASE_PACKAGING.md`](../RELEASE_PACKAGING.md) — connected build and airgap verification steps
- [`INSTALL_UPGRADE_ROLLBACK_INDEX.md`](INSTALL_UPGRADE_ROLLBACK_INDEX.md) — lifecycle scripts inside the package
