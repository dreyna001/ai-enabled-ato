# Package Intake, Extraction, and Review Plan

Status: Delivered (code-complete, 2026-07-14). Component A of
[`docs/FINAL_PRODUCT_IMPLEMENTATION_PLAN.md`](FINAL_PRODUCT_IMPLEMENTATION_PLAN.md).
Diff sections below are the historical delivery record.

This is not a prototype plan. Every migration, API, worker, and portal surface
is designed for the production product. Delivery is split into reversible,
tested diffs so failures can be isolated without creating throwaway paths.

This document covers **intake through sealed `ready`** and bounded
import/handoff adjacency only. Analysis, review, export approval, production
deployment, and release qualification are defined in the master plan.

## 1. Goal

Build the core package-preparation workflow end to end:

1. A customer uploads **any supported file types** for a package revision (PDF,
   Word, Excel, JSON, plain text, OSCAL, scanner exports, images, and ZIP
   packages).
2. The platform **scans, extracts, and maps** content into one editable package
   draft with field-level provenance.
3. A human **reviews and edits** the pre-filled form in the portal.
4. One **Confirm package** action seals the revision as immutable `ready`.
5. The team proceeds to analysis, gap review, and export on that sealed package.

This replaced the demo-only path (synthetic JSON leaf extraction plus per-fact
Accept/Reject cards). It is the primary customer workflow for turning uploaded
authorization evidence into a reviewable package.

### Product position

This plan implements the product wedge: **evidence intake, normalization,
editable package review, analysis handoff, and bounded interoperability with
external ATO processes**. It does not implement full RMF program management,
bidirectional GRC sync, assessor workpaper execution, AO decisions, or full
ConMon platform replacement. Those remain external or later phases.

We **do** take on adjacent work when it strengthens the same workflow:

- import assessor-supplied results (not generate them)
- one-way GRC/baseline import and export handoff (not live sync)
- parse uploaded scanner and cloud-report exports (not run scanners)
- record externally issued authorization decisions (not make them)
- validate FedRAMP submission-ready packages (not submit to PMO)
- attach privacy-office artifacts and show scope notice (not perform privacy review)
- ConMon-lite via child-revision re-upload and delta analysis

## 2. Background (pre-delivery)

Before this plan landed:

- Intake ran only on `dev_local` + `data_origin=synthetic` + `application/json`.
- Extraction walked JSON leaves and created one `FactProposal` per field.
- The portal rendered every proposal as a separate card requiring Accept or
  Reject.
- PDF, DOCX, mixed document packages, and customer `data_origin=customer` uploads
  were not processed.
- There was no aggregated editable package form.

That was insufficient for real use. A system owner or ISSO drops a folder of SSP
sections, policies, configs, and scan exports; the product must ingest those
files, propose field values, and present one editable package — not hundreds of
JSON-pointer approval cards.

Delivered stack: see [`P3_GATE_RECORD.md`](P3_GATE_RECORD.md) and README Current state. Residual: production malware scanner drill (**HS-005**).

## 3. Target User Experience

```text
Create System
  -> create PackageRevision (profile, impact level, data origin, sensitivity)
  -> upload one or many files (or one ZIP)
  -> finalize upload
  -> platform scans, extracts, and maps content into a draft
  -> revision enters awaiting_confirmation
  -> portal shows one Package editor with grouped, pre-filled fields
  -> user edits wrong or missing values, saves draft
  -> user confirms once
  -> revision becomes ready (immutable)
  -> run analysis and continue the existing downstream workflow
```

Editor behavior:

- Grouped sections: package metadata, system, contacts, security controls,
  evidence, and extensions for unmapped source fields.
- Every pre-filled value shows whether it came from deterministic parsing or
  model-assisted mapping (without exposing raw JSON pointers in the default view).
- Optional source-details panel shows artifact name, hash, and locator.
- Save draft without sealing; Confirm package seals exactly what is on screen.
- Validation errors name the section and field to fix.

No per-field Accept or Reject controls in the default portal.

## 4. Scope Contract

### In scope

- Multi-file and ZIP upload into one `PackageRevision` (existing upload contract).
- Malware scan gate before extraction (production scanner; bounded dev substitute
  documented below).
- Deterministic extraction for supported formats per `ATO_TECHNICAL_SPEC.md`
  Section 15.2 and 15.4.
- LLM-assisted field mapping (`normalize_proposal`) for variable customer shapes
  after routing policy passes.
- One `PackageRevisionDraft` per `awaiting_confirmation` revision.
- Draft GET/PUT and package-level confirm APIs.
- Portal `PackageEditor` replacing proposal cards.
- Field-level provenance retained internally for every imported value.
- End-to-end acceptance for all three supported product profiles:
  `fedramp_20x_program`, `fedramp_rev5_transition`, and
  `fisma_agency_security`.
- `dev_local` qualification of the same production-shaped contracts using
  local substitutes only at external boundaries (malware scanner, identity,
  and model endpoint).
- Production intake worker, systemd unit, and scanner integration contract (closes
  **HS-005** when scanner is verified).
- Contract, migration, deployment, traceability, and test updates shipped
  together.
- **Assessor-results import:** ingest uploaded SAR, assessment exports, and
  attestation bundles; link to controls and evidence; preserve assessor-owned
  fields as import-only.
- **One-way GRC interoperability:** import approved control baseline (OSCAL,
  reference catalog) and export sealed package artifacts for customer GRC load.
- **Scanner and cloud-report ingestion:** parse Nessus, SARIF, STIG, and similar
  uploaded exports; attach findings to evidence and controls.
- **Authorization handoff record:** produce submission-ready export bundles;
  after external AO action, attach decision letter metadata and artifact without
  issuing the decision.
- **FedRAMP submission preparation:** validate official schemas and surface
  export-readiness blockers before customer PMO submission.
- **Privacy artifact handling:** accept privacy-office uploads, show privacy
  scope notice, and block export claims when required privacy inputs are missing.

### Out of scope

- Claims of agency-specific field parity or export while **HS-002** is open.
- Replacing GRC, eMASS, assessor tools, or ConMon platforms.
- Live cloud/scanner/GRC collection or bidirectional sync.
- Official assessor conclusions, AO decisions, or authorization issuance.
- Autosave, collaborative real-time editing, or in-place editing of `ready`
  revisions.
- Full ConMon platform replacement (live dashboards, scheduled collection,
  official ongoing-authorization reporting).

### Hard stops during implementation

| Hard stop | Effect on this plan |
| --- | --- |
| **HS-002** | Implement the complete product-owned FISMA canonical model and template-pack import contract. Do not claim agency-specific parity until a qualified customer pack is supplied. |
| **HS-004** | `dev_local` may call the configured OpenAI-compatible endpoint for normalization. Production customer model calls require verified customer data policy. |
| **HS-005** | Production customer extraction requires verified malware scanner. Implementation proceeds with a dev scanner substitute and production scanner adapter. |
| **HS-006** | Do not claim AI qualification or pilot readiness from normalization alone. |

## 5. Supported Inputs

Final-product extraction support matches the technical spec allowed-input
table:

| Category | Formats | Production behavior |
| --- | --- | --- |
| Structured | JSON, UTF-8 text | Deterministic parse |
| Documents | PDF, DOCX, XLSX, TXT, Markdown | Deterministic text/table extraction; scanned PDF pages use the governed vision/OCR path |
| OSCAL | JSON and XML | Deterministic parse where schema-known; else text + normalize |
| Scanner exports | Nessus XML, SARIF JSON, supported STIG JSON/XML | Deterministic parse |
| Archives | ZIP at upload boundary only | Existing upload extract; no nested archives |
| Images | PNG, JPEG, WebP, sanitized SVG, supported PDF pages | Governed vision extraction with routing policy; evidence-only fallback when vision is unavailable or prohibited |

Rejected types, macros, path traversal, and unsafe archives fail closed per spec.

### Dependency policy — established libraries at the extraction boundary

Use **Python 3.12 standard library** for JSON, UTF-8 text, Markdown, and ZIP
safety controls. Use **mature, widely used dependencies** for format-specific
parsing where reimplementing the format in-house would duplicate a large,
security-sensitive parser without reducing risk.

The `ato_service.extraction` boundary still owns routing, configured limits,
provenance, serialization, and fail-closed handling. Approved libraries are
invoked only behind those controls; they do not replace upload validation,
malware gating, or lifecycle transitions.

Each approved dependency is pinned in `pyproject.toml` with a one-line
justification, license-compatible, narrowly scoped, and covered by malformed-input
regression fixtures. Unjustified dependencies are rejected.

| Format | Primary implementation | Notes |
| --- | --- | --- |
| JSON, UTF-8 text, Markdown | `json`, `codecs`, string parsing | Stdlib only |
| ZIP members (DOCX/XLSX containers) | `zipfile` with path/size/count limits | Safety boundary before library parse |
| DOCX body text and tables | `python-docx` | Established library; ZIP safety controls remain |
| XLSX cell values | `openpyxl` | Cached values only; no formula evaluation |
| OSCAL, Nessus, STIG, SARIF XML | `defusedxml` with hardened parse | Hostile-XML protection |
| PDF text layer | `pypdf` | Primary PDF text extraction |
| PDF page rendering for vision/OCR | `pypdfium2` | Bounded renderer when scanned pages require vision |
| PNG / JPEG / WebP | `Pillow` | Bounded validation and decode |
| SVG | `defusedxml` + explicit script/external-reference stripping | No inline render |

**Explicitly not using** `pdfminer.six` or Apache Tika. **`lxml`** is approved
only as a `python-docx` dependency and for advanced XML support behind
`defusedxml` boundaries.

Pin approved packages in `pyproject.toml`, document the justification, and add
hostile-file fixtures before merge.

## 5.1 Upload model — one path, typed artifacts, separate lifecycle actions

**Default: one upload path per package revision.**

All intake files use the existing streaming upload endpoint:

```text
POST /api/v1/package-revisions/{id}/files
```

The uploader selects an **`artifact_kind`** (or the portal infers it and asks for
confirmation). The contract already defines kinds including `evidence_document`,
`scanner_export`, `oscal`, `attestation`, `architecture`, `reference_catalog`, and
FedRAMP artifact kinds. One revision can hold many files of different kinds in
the same upload session.

```text
Same upload endpoint
  -> user drops SSP PDF, Nessus export, OSCAL baseline, assessor SAR
  -> each file stored as SourceArtifact with artifact_kind + hash
  -> scan and extract run on all artifacts
  -> routing by kind + MIME decides parser and draft mapping
```

**Why one path:** system owners and ISSOs already think in terms of "add files to
this package." Separate endpoints per file type would duplicate scan, storage,
provenance, and permission logic without helping users.

**Portal UX:** one Upload area with an optional type selector (Evidence, Scanner
report, GRC/OSCAL baseline, Assessor report, Architecture, Privacy artifact).
Auto-detect where safe; require confirmation when ambiguous.

**Separate actions only where lifecycle or permissions differ:**

| Action | Why separate | When |
| --- | --- | --- |
| Control baseline import | Seeds the approved control list before or during package work; may update draft control section without a full re-upload | Revision setup or dedicated Import baseline step (still uses `reference_catalog` or `oscal` upload under the hood) |
| Authorization decision record | Happens **after** external AO action; not part of intake confirm | Post-export / post-authorization attach on System or authorized revision record |
| Export / submission handoff | Output, not input | After review and approval |
| ConMon-lite child revision | New revision lineage, not editing ready in place | After initial ATO when customer uploads updated evidence |

**What we do not add:** separate upload services per vendor (Tenable upload vs
Qualys upload vs Word upload). Vendor-specific logic lives in extractors keyed
by `artifact_kind` and detected format.

**Assessor uploads:** same file endpoint. Production authorization requires an
assessor role to author assessor-owned metadata; package owners may upload an
assessor-signed artifact but cannot alter imported assessor conclusions.

**Privacy uploads:** same file endpoint with an explicit `privacy_artifact`
artifact kind. Privacy material is stored and handed off but never assessed by
this product.

## 6. Extraction and Normalization Architecture

### Pipeline stages

```text
uploading
  -> finalize -> scanning
  -> [malware scan each artifact]
  -> extracting
  -> [deterministic text/structure extract per file]
  -> [aggregate extracted segments]
  -> [map segments to draft fields]
  -> awaiting_confirmation (draft created)
  -> [human edit via portal]
  -> confirm -> ready (sealed)
```

All stage transitions increment `revision_version` once and commit atomically with
audit events, consistent with `docs/contracts/LIFECYCLE_AND_ERRORS.md`.

### Deterministic extraction

For each `SourceArtifact` after a clean scan:

1. Validate detected MIME against declared type and allowed set.
2. Run the format-specific extractor in an unprivileged worker (no network).
3. Store extracted text or structured fragments with byte offsets or JSON
   pointers as locators.
4. Record `extraction_method=deterministic` or `extraction_method=text`.

Known structured formats (JSON, OSCAL JSON, SARIF, etc.) map directly into draft
sections where field paths are defined in the product entry schema. Unknown keys
land in `extensions`.

### Model-assisted mapping

When shape is variable (narrative PDF sections, agency-specific Word exports,
unfamiliar JSON):

1. Routing policy runs first; blocked labels produce visible failure with
   `llm_call_count=0`.
2. Call `normalize_proposal` with a bounded fact bundle: extracted text segments,
   filenames, detected types, and the target draft schema.
3. Model returns **structured proposals only** (field path, proposed value,
   source artifact id, locator, confidence label).
4. Validate response against the normalization schema; one repair attempt.
5. Write proposals into draft pre-fill and provenance with
   `extraction_method=llm_normalize` and `model_step_id`.
6. Model proposals are **draft suggestions**, not trusted facts, until the human
   confirms the whole package.

The package editor displays model-derived fields with a visible indicator. Package
confirmation records human acceptance of the entire sealed draft including every
model-derived visible field. Hidden model-derived fields are prohibited.

### Mapping into one draft

Extraction and normalization **produce one `PackageRevisionDraft`**, not a second
editable copy alongside per-leaf proposals.

`FactProposal` may remain as an internal audit artifact generated during mapping,
but the portal edits only `PackageRevisionDraft.document`. Implementation must
avoid two independently editable representations.

## 7. Domain Model

### PackageRevisionDraft

```text
package_revision_id: UUID primary key / foreign key
schema_version: string
document: JSON object
field_provenance: JSON object
updated_by: user or service actor ID
updated_at: UTC datetime
```

`field_provenance` maps canonical JSON pointers to:

```text
source_artifact_id
source_sha256
source_locator
extraction_method
model_step_id
```

### Sealed content at confirm

On confirm, canonical JSON bytes and SHA-256 digest are written to an immutable
package-content record (separate from the mutable draft row). Ready revisions
resolve only through sealed content.

### Versioned system context

System facts that persist across authorization cycles use a versioned
`SystemContext` record. A package revision references an immutable snapshot so
later system-context edits do not alter a ready package. Upload extraction may
propose changes to the next system-context version; a human must approve them.

```text
display_name
external_system_id
mission_summary
authorization_boundary
environments
hosting_locations
major_components
external_dependencies
information_types
fips_199_rationale
impact_level
authorization_path
approved_control_set_reference
```

These are populated from uploads or manual edit; the product does not invent them.

## 8. Draft Shape

The final product uses a versioned canonical package model with a common core
and three explicit profile sections. This is a discriminated schema, not a
generic plugin framework.

```json
{
  "package": {
    "profile_id": "fisma_agency_security",
    "title": "",
    "prepared_for": "",
    "reporting_period": null
  },
  "system": {
    "display_name": "",
    "authorization_boundary": "",
    "mission_summary": "",
    "impact_level": "moderate",
    "authorization_path": ""
  },
  "contacts": {
    "system_owner": [],
    "isso": [],
    "issm": [],
    "control_owners": [],
    "assessors": [],
    "approvers": []
  },
  "control_set": {
    "source": {},
    "tailoring": [],
    "organization_defined_parameters": {},
    "inheritance": []
  },
  "security_controls": {
    "AC-1": {
      "implementation_status": "implemented",
      "implementation_statement": "",
      "responsible_parties": [],
      "evidence_links": []
    }
  },
  "evidence": {},
  "findings": {},
  "poam_candidates": {},
  "assessor_inputs": {},
  "privacy": {
    "artifacts_present": false,
    "scope_notice": "Privacy review is external to this product."
  },
  "fedramp_20x": null,
  "fedramp_rev5_transition": null,
  "fisma_agency_security": {},
  "extensions": {}
}
```

Rules:

- The common core stores system, contacts, approved control-set references,
  control implementation, evidence, findings, and provenance once.
- `fedramp_20x` directly represents CPO, SDR, OCR, SCG references, KSI methods,
  metric history, and imported independent-assessment fields required by the
  pinned official schemas.
- `fedramp_rev5_transition` preserves imported SSP, SAP, SAR, POA&M, and OSCAL
  values without mutating the source package.
- `fisma_agency_security` stores security-only agency fields and applies a
  versioned customer template pack when one is qualified.
- Security controls are dynamic keyed by validated control ID.
- Unknown source material is preserved under `extensions` with original locators.
- No silent discard; invalid or over-limit documents fail validation.
- Customer-specific required fields wait for **HS-002** template packs.

### Product roles

The production role contract is package-scoped:

- system owner: supplies and confirms system facts;
- ISSO/ISSM: manages package content, evidence requests, and readiness review;
- control owner: supplies implementation details and evidence for assigned
  controls;
- assessor: imports and owns assessor conclusions;
- reviewer: dispositions analysis findings;
- approver: approves the exact export payload;
- AO-record custodian: attaches an externally issued authorization decision;
- viewer: read-only.

Customer IdP groups map to these roles. Separation-of-duty validation prevents
the same principal from approving an export they submitted where the selected
profile requires independence.

## 9. API Contract

### Read draft

```text
GET /api/v1/package-revisions/{id}/draft
```

Returns document, provenance summary, schema version, revision version, ETag.
Owner and viewer groups may read.

### Save draft

```text
PUT /api/v1/package-revisions/{id}/draft
```

Owner only; status `awaiting_confirmation`; `If-Match`, `Idempotency-Key`, CSRF;
full document validates; atomic revision increment and audit.

### Confirm package

```text
POST /api/v1/package-revisions/{id}/confirm
```

Package-level confirm: validates draft, seals canonical bytes, transitions to
`ready`. Does **not** require per-leaf proposal decisions.

### Proposal endpoints

Deprecated after portal migration. Not used by the default workflow.

## 10. Portal Design

- `PackageEditor` component with section navigation and inline editing.
- Dynamic security control rows (add, edit, remove).
- Visible badges for model-assisted vs deterministic pre-fill.
- Profile-aware sections for FedRAMP 20x, Rev. 5 transition, and agency FISMA.
- Role-aware, read-only rendering for assessor-owned and externally issued
  decision fields.
- Save draft, Confirm package, dirty-state warning, ETag conflict reload.
- `WorkflowPage` orchestrates upload status, extraction progress, editor, and
  post-ready actions.
- One upload panel with artifact-kind selector; optional dedicated Import
  baseline and Attach authorization decision steps where lifecycle differs.
- Remove default per-fact Accept/Reject cards.

## 11. Workers and Deployment

### Replace synthetic-only worker with intake worker

| Profile | Scanner | Extraction | Normalization |
| --- | --- | --- | --- |
| `dev_local` | Deterministic clean result after type/size checks (documented substitute; not a production scanner) | Same extractors and schemas as production | OpenAI-compatible path when configured |
| Production | Customer-approved scanner; fail closed if unavailable | Same extractors in hardened sandbox | Customer-approved endpoint per **HS-004** |

Ship the production systemd unit inactive only after the worker runtime and
acceptance tests exist, following the deployment contract. Keep worker
unprivileged, no network during deterministic extract; model calls only through
the gateway with routing policy.

## 12. Security and Policy Boundaries

- Treat all uploads and draft PUT bodies as untrusted.
- Enforce spec limits: package size, file count, PDF pages, extracted text size.
- No formula evaluation, macro execution, or URL fetching from document content.
- Routing policy before any model call; default deny for sensitive labels in
  external profiles.
- Client cannot override server-derived hashes or locators in provenance.
- Production extraction blocked until scanner verification closes **HS-005**;
  dev path is explicitly labeled non-production.

## 13. Migration and Compatibility

- Add `package_revision_drafts` and sealed content tables in one Alembic
  migration.
- Existing `awaiting_confirmation` synthetic revisions: auto-assemble draft from
  proposals once, or return explicit `reconciliation_required`.
- Existing `ready` revisions remain immutable.
- Update OpenAPI, domain schema, portal schemas, traceability, lifecycle docs,
  and deployment contract tests in the same change set as each diff.

## 14. Implementation Plan

### Diff 1: Draft contracts and persistence

Publish draft schema, sealed-content contract, DB models, migration, and contract
tests. No behavior change to intake yet.

**Acceptance:** schemas reject malformed drafts; one draft per revision;
persistence tables and domain contracts are published. Ready revisions still use
the existing proposal-gated confirm path until Diff 5; sealed-content and
`package_content_sha256` non-null invariants are staged for Diff 5 and are not
DB-enforced in Diff 1.

### Diff 2: Production format extractors and vision fallback

Implement focused extractors module using the Section 5 established-library
policy:

- JSON / plain text / Markdown — stdlib only
- DOCX — `python-docx` behind ZIP safety controls
- XLSX — `openpyxl` cached cell values; no formula evaluation
- OSCAL JSON/XML, Nessus XML, SARIF JSON, and supported STIG JSON/XML — `defusedxml`
- PDF text layer — `pypdf`
- Scanned PDF pages — `pypdfium2` bounded renderer when vision path is enabled
- PNG, JPEG, WebP — `Pillow`; sanitized SVG — `defusedxml` + explicit stripping
- ZIP member handling at upload only (already partially present)

Unit tests with fixtures per format; fault injection for malformed inputs.

**Acceptance:** each supported format produces bounded extracted segments with
locators; image-only documents use vision when policy permits and produce an
explicit evidence-only result otherwise; unsafe inputs fail without partial
side effects.

### Diff 3: Intake worker pipeline

Replace synthetic-only worker with profile-aware intake worker:

- Claim revisions in `scanning` and `extracting`
- Run scan gate (dev substitute vs production scanner adapter)
- Run deterministic extractors per artifact
- Aggregate segments; build initial draft for structured uploads
- Transition to `awaiting_confirmation` with draft row

Wire the console script and WSL deployment. Add an inactive production systemd
unit in the deployment diff after worker acceptance tests pass.

**Acceptance:** mixed JSON + PDF + DOCX upload in `dev_local` produces a draft;
invalid files transition to `invalid` or `quarantined` correctly.

### Diff 4: LLM field mapping

Add `normalize_proposal` step using existing text LLM gateway:

- Bounded prompts with extracted segments and target schema
- Structured response validation and one repair
- Merge proposals into draft with provenance and visible model markers
- `dev_local` uses configured OpenAI-compatible credentials

**Acceptance:** narrative PDF or unfamiliar JSON pre-fills labeled draft fields;
routing block produces visible failure with zero model calls; no hidden model fields.

### Diff 5: Draft API and package confirm

Implement GET/PUT draft and updated confirm semantics in API and service layer.

**Acceptance:** edit, save, reload, confirm once to `ready`; ETag and auth failures
behave correctly; sealed digest matches displayed document.

### Diff 6: Portal package editor

Replace proposal inbox with `PackageEditor`; show extraction progress; support
mixed uploads in workflow UI.

**Acceptance:** local demo uploads multiple file types, shows pre-filled editor,
persists edits, confirms to `ready` without per-leaf approvals.

### Diff 7: Cleanup and production scanner

- Deprecate proposal review UI and routes from default path
- Implement production malware scanner adapter contract
- Document scanner configuration and HS-005 verification steps
- Update traceability and gate records

**Acceptance:** production profile refuses extraction when scanner unavailable;
dev_local full acceptance flow documented in `docs/WSL_LOCAL_DEPLOY.md`.

### Diff 8: Control-set, GRC, and structured export ingest

Objective:

- Route `scanner_export`, `oscal`, and `reference_catalog` artifacts through
  deterministic parsers and into draft `evidence` / `security_controls` links.

Expected files:

- draft mapping for control baseline import
- direct profile mapping for FedRAMP CPO/SDR/OCR/SCG and Rev. 5 OSCAL inputs
- fixtures and extractor tests

**Acceptance:** uploaded Nessus + OSCAL baseline appear as linked evidence and
control rows in draft; assessor-owned fields are not overwritten.

### Diff 9: Assessor-results import

Objective:

- Ingest `attestation` and assessor report uploads; populate `assessor_inputs`
  as import-only, human-attributed fields.

Expected files:

- assessor import mapper
- draft schema extension for assessor-owned tags
- contract tests enforcing `owner=assessor` non-generation

**Acceptance:** SAR upload links to controls; product never generates assessor
conclusions; missing assessor inputs surface as export-readiness blockers only.

### Diff 10: Export handoff and FedRAMP submission prep

Objective:

- Validate official FedRAMP/customer export schemas; produce submission-ready
  ZIP; surface deterministic export-readiness blockers.

Expected files:

- export validation service (extends existing export contract)
- portal export-readiness panel
- FedRAMP schema fixture tests

**Acceptance:** invalid official payload fails with explicit blockers; valid
draft export matches sealed revision content; no PMO submission claim.

### Diff 11: Authorization decision record and privacy artifacts

Objective:

- Attach external AO decision metadata and artifact after authorization.
- Accept privacy-office uploads; show privacy scope notice on export surfaces.

Expected files:

- authorization decision attach API (separate from intake confirm)
- privacy artifact tagging on upload
- export blocker when required privacy inputs absent per profile

**Acceptance:** decision record stores external AO artifact without granting ATO
in product; privacy notice appears on drafts and exports; privacy assessment is
not performed in-product.

### Diff 12: ConMon-lite

Objective:

- Child revision from authorized baseline; delta report between authorized and
  new upload; targeted re-analysis on changed artifacts only.

Expected files:

- revision lineage compare service
- portal "updated evidence" flow reusing single upload path

**Acceptance:** customer uploads new scan export to child revision; delta report
lists changed evidence and controls affected; no live ConMon scheduler added.

## 15. Verification

Per-diff focused tests plus final suite:

```powershell
python -m pytest tests/ato_service -m "not integration" -q
python -m pytest tests/test_contracts.py tests/test_deployment_contract.py -q
cd portal
npm test
npm run build
```

Final acceptance flow:

1. Create system and revision with `data_origin=customer` in `dev_local`.
2. Upload JSON + PDF + DOCX (or demo ZIP).
3. Finalize; intake worker processes all artifacts.
4. Open portal editor; verify pre-filled system boundary, controls, and evidence.
5. Edit fields; save and reload.
6. Confirm once; revision `ready` with sealed digest.
7. Verify audit trail and provenance for deterministic and model-assisted fields.

## 16. Acceptance Criteria

Functional:

- Supported file types extract and contribute to one editable draft.
- Human edits persist; one confirm seals the displayed draft.
- Model-assisted values are visible and included in human confirmation.
- No per-leaf Accept/Reject required for the default workflow.
- Unknown fields preserved under `extensions`.
- Ready revisions immutable; child revision for later changes.
- Failed scan, unsafe type, or invalid content fails visibly.
- Assessor, AO, and privacy material remain import/attach-only; product never
  issues official decisions or assessor conclusions.
- One upload endpoint serves mixed artifact kinds; separate actions only for
  baseline import, authorization decision attach, and export handoff.

Non-functional:

- Deterministic serialization and validation at boundaries.
- Authorization, CSRF, ETag, idempotency, and audit on all mutations.
- Worker sandbox: unprivileged, resource limits, no network on extract path.
- Code, contracts, docs, deployment assets, and tests updated together.
- Extractors follow Section 5: stdlib for JSON, text, Markdown, and ZIP safety;
  approved established libraries for format-specific parsing, all behind routing,
  limits, and fail-closed handling.

## 17. Final Product Decisions

1. Existing `awaiting_confirmation` revisions: **auto-assemble draft** from
   proposals when possible; otherwise `reconciliation_required`.
2. Proposal endpoints: **deprecated one release**, portal stops calling immediately.
3. Sealed content: **separate immutable table** at confirm; draft row remains the
   mutable editing surface until confirm.
4. Upload architecture: **one file upload path per revision** with `artifact_kind`
   classification; separate lifecycle actions only for baseline import, external
   authorization decision attach, and export handoff.
5. Profile architecture: **one versioned canonical core plus three explicit
   profile schemas**; no FISMA-first or FedRAMP-first throwaway model.
6. System context: **versioned `SystemContext` snapshots** referenced by package
   revisions, not mutable text trapped only inside one draft.
7. Document flexibility: deterministic parsers for known structures and a
   bounded `normalize_proposal` LLM step for variable layouts; every mapped
   value retains source provenance and remains human-editable.
8. OCR/vision: **part of the final product**, governed by routing policy with an
   evidence-only fallback; image-only documents are not silently ignored.
9. Roles: **full package-role contract** with assessor ownership and export
   separation of duties; `owner/viewer` is migration compatibility only.
10. Qualification corpus: four permanent suites:
    - mixed-format agency FISMA package plus qualified template-pack fixture;
    - official-schema FedRAMP 20x Class C package;
    - Rev. 5 SSP/SAP/SAR/POA&M and OSCAL transition package;
    - hostile/malformed files, prompt injection, duplicates, and partial-failure
      fixtures.
11. Dependencies: stdlib for JSON, text, Markdown, and ZIP safety controls;
    mature established libraries (`pypdf`, `pypdfium2`, `python-docx`, `openpyxl`,
    `defusedxml`, `lxml`, `Pillow`) for format-specific parsing behind the
    extraction boundary; no `pdfminer.six` or Tika.

## 18. Delivery Rule

Implementation proceeds in dependency order, but every diff lands only
production-shaped contracts and data. There are no demo-only schemas, temporary
portal models, or parallel editable representations. `dev_local` swaps external
dependencies; it does not change domain behavior.

No profile is considered complete until its end-to-end qualification corpus,
official schema checks where applicable, portal workflow, export handoff, and
operator documentation pass together.
