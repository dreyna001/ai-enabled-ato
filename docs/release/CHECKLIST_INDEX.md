# Operator Checklist Index

**Purpose:** Point operators to deterministic checklist sources bundled with the release package.

## Generated checklist

```bash
ato-operator print-checklist
```

The command reads `docs/requirements/hard-stops.yaml` and emits configuration, operations, airgap, and hard-stop items. It does **not** close hard stops.

## Bundled checklist sources

| Source | Contents |
| --- | --- |
| `docs/CUSTOMER_ONBOARDING.md` | Staged onboarding for RHEL 9-compatible installs |
| `docs/AIRGAP_PRESTAGE.md` | Connected prestaging and airgap target steps |
| `docs/RELEASE_PACKAGING.md` | Build, verify, and transfer procedures |
| `docs/requirements/hard-stops.yaml` | Open hard stops (**HS-001**..**HS-009**) |
| `src/ato_operator/checklist.py` | Deterministic checklist builder used by `print-checklist` |

## Validation drills

Phase 5 publishes executable drills via `ato-operator run-drill`. Immutable records use `docs/contracts/validation-drill-record.schema.json`.

Recommended record root: `/var/lib/ato/validation-drill-records/`

## Qualification corpus

```bash
ato-operator qualification-check
```

Validates `data/qualification/manifest.json` digests and coverage. Official qualification and release claims remain blocked while hard stops are open.
