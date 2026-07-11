# Product Contract Files

These files turn [`ATO_TECHNICAL_SPEC.md`](../../ATO_TECHNICAL_SPEC.md) into reviewable machine contracts before product feature code is written.

| File | Purpose | Freeze status |
| --- | --- | --- |
| `domain.schema.json` | Internal object, enum, and state shapes | Published P-1 contract |
| `analysis-profile.schema.json` | Deterministic authority/applicability catalog | Published P-1 contract |
| `authority-manifest.schema.json` | Pinned source metadata and digest rules | Published P-1 contract |
| `authority-manifest.json` | Exact source pins used for qualification | Bytes verified; HS-001 review remains open |
| `content-manifest.schema.json` | Immutable package source inventory | Published P-1 contract |
| `artifact-manifest.schema.json` | Durable run-output inventory | Published P-1 contract |
| `export-manifest.schema.json` | Hash-bound approved ZIP inventory | Published P-1 contract |
| `preflight.schema.json` | Deterministic analysis/export eligibility result | Published P-1 contract |
| `runtime-config.schema.json` | Validated non-secret runtime and endpoint settings | Published P-1 contract |
| `openapi.json` | OpenAPI 3.1 API surface and shared HTTP contracts | Published P-1 contract (`info.version` 1.0.0) |
| `LIFECYCLE_AND_ERRORS.md` | Legal state transitions and stable error taxonomy | Published P-1 contract |

P-1 gate outcome is recorded in [`../P1_GATE_RECORD.md`](../P1_GATE_RECORD.md).
The implemented P1.2 development boundary is specified in
`LIFECYCLE_AND_ERRORS.md` Section 2.1.6: only `dev_local` synthetic JSON intake,
with no production scanner/customer extraction or OIDC claim.

## Rules

- These files are design contracts, not proof that the corresponding runtime behavior is implemented.
- JSON Schemas use Draft 2020-12 and reject unknown fields unless explicitly documented.
- OpenAPI uses relative references to `domain.schema.json`.
- Official FedRAMP and OSCAL schemas remain external authorities. Internal schemas MUST NOT replace or weaken them.
- A contract change requires matching specification, traceability, fixture, migration, and test updates.
- Runtime/deployment values and behavior form one contract across code, schema/examples, systemd/nginx, install/smoke scripts, operator docs, and deployment-contract tests; change these surfaces together.
- Production release is blocked while `authority-manifest.json` has `status=draft` or any source has a null digest.

## Validation

Install the development dependencies and run the deterministic, network-free
contract suite:

```text
python -m pip install -e ".[dev]"
python -m pytest tests/test_contracts.py
python -m pytest tests/test_deployment_contract.py
```

The development extra includes unpinned `jsonschema[format]`. It supplies Draft
2020-12 validation and format checking for the internal schemas, fixtures, and
OpenAPI-linked contracts without adding a runtime dependency.

Contract fixtures live in `docs/contracts/fixtures` and use
`<contract>.<outcome>.<case>.json`. Covered contracts are `domain`,
`analysis-profile`, `content-manifest`, `artifact-manifest`, `export-manifest`,
`preflight`, and `runtime-config`; each has at least one valid and one invalid
fixture.

The suite parses repository contract, fixture, and vendored-reference JSON;
validates each internal schema against its declared metaschema; verifies the
authority manifest's local bytes; recursively resolves local OpenAPI references;
checks the minimum API, concurrency, idempotency, and security contracts;
compares duplicated closed enums; and checks JSON-compatible requirements
traceability. Vendored official schemas are parsed and digest-checked but are
not validated against an internal metaschema.

Deployment packaging assets (systemd unit, nginx template, install/smoke scripts)
are checked separately by `tests/test_deployment_contract.py`. Runtime JSON
precedence and capability flags are documented in
[`../CONFIGURATION.md`](../CONFIGURATION.md).