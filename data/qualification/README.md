# Qualification corpus

Synthetic, non-sensitive fixtures for deterministic qualification validation. No customer data, secrets, malware, real credentials, or dangerous active payloads are stored here.

## Layout

| Path | Purpose |
| --- | --- |
| `manifest.json` | Schema-validated manifest with SHA-256 digests and hard-stop-safe claim metadata |
| `profiles/fisma-agency-security/` | Agency FISMA security-only mixed-format and sealed-draft fixtures |
| `profiles/fedramp-20x-class-c/` | FedRAMP 20x Class C synthetic CPO/SDR/OCR and sealed-draft fixtures |
| `profiles/fedramp-rev5-transition/` | Rev.5 transition import and OSCAL-shaped fixtures |
| `assessor-import/` | Assessor-owned import-only excerpts (not assessor conclusions) |
| `hostile-inputs/` | Malformed, XXE, and prompt-injection regression fixtures |
| `scenarios/` | Duplicate, idempotency replay, lease recovery, and crash descriptors |

Official FedRAMP, NIST, and OSCAL authority bytes remain in `reference/authorities/`. Fixtures here are clearly labeled synthetic and do not replace qualified authority review (HS-001).

## Regeneration

After adding or editing corpus bytes (never edit digests by hand):

```text
python3 scripts/regenerate_qualification_manifest.py
ato-operator qualification-check
```

The regeneration script recomputes `sha256` and `size_bytes` for every fixture entry declared in `scripts/regenerate_qualification_manifest.py`. Update that script when adding new fixtures, then rerun regeneration and `qualification-check`.

## Validation

`ato-operator qualification-check` validates:

- manifest schema (`docs/contracts/qualification-manifest.schema.json`)
- path safety (no traversal outside `data/qualification`)
- digest and size integrity for every fixture
- unique `fixture_id` and `relative_path` values
- profile coverage for all three supported profiles
- hostile coverage (injection, XXE, malformed parse rejection)
- replay/lease/duplicate/crash scenario coverage

The command reports governed hard stops and **never closes them**. `claim_metadata.closes_hard_stops` must remain `false` on every fixture.

## Hard-stop limitations

| Hard stop | What this corpus does **not** claim |
| --- | --- |
| HS-001 | Reviewed authority snapshot or official schema qualification |
| HS-002 | Agency field parity or customer-ready FISMA export |
| HS-003 | Production identity deployment |
| HS-004 | Real customer model calls |
| HS-005 | Production customer file extraction or live scanner operation |
| HS-006 | AI qualification or pilot readiness |
| HS-007 | GRC writeback |
| HS-008 | Production backup/recovery readiness |
| HS-009 | Complete Class C package readiness without assessor inputs |

EICAR and live malware scanner fixtures are intentionally absent from this corpus. Malware regression uses in-test bytes per `tests/ato_service/test_malware_scan.py` and operator HS-005 checklists on RHEL.

## Tests

Focused validation tests live in `tests/ato_operator/test_qualification_check.py`. Hostile injection regression uses `hostile-inputs/prompt-injection-fixtures.json` via `tests/ato_service/test_hostile_input_regression.py`.
