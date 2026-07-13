# Final Product Implementation Plan

Status: Approved master plan.

Normative contracts remain in [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md),
user workflow in
[`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](../ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md),
and traceability in [`docs/requirements/traceability.yaml`](requirements/traceability.yaml).

## 1. Purpose

This document defines what the **complete deployable product** is and how all
implementation work fits together. It prevents mistaking one component plan for
the whole product.

Building every item in this plan produces the final on-prem ATO Evidence Analysis
Portal described in the technical spec. No throwaway schemas, demo-only APIs, or
parallel temporary models are part of the end state.

## 2. Relationship to other plans

| Document | Scope |
| --- | --- |
| [`docs/FINAL_PRODUCT_IMPLEMENTATION_PLAN.md`](FINAL_PRODUCT_IMPLEMENTATION_PLAN.md) | Master plan — full product, dependencies, gates |
| [`docs/PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md) | Component A — intake, extraction, editable confirm, bounded import/handoff |
| [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md) | Normative contracts, domain model, security boundaries |
| [`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](../ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md) | User-visible workflow and EP acceptance map |

**Important:** [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md) covers
**package preparation only** (upload through sealed `ready`, plus import/export
adjacency). It does **not** complete analysis, review, export approval,
production deployment, or AI qualification by itself.

## 3. Final product definition

The final product is a single-customer, on-prem installation that lets authorized
users:

1. Create systems and package revisions for three supported profiles.
2. Upload mixed evidence and context files into one revision.
3. Scan, extract, and pre-fill an editable package with provenance.
4. Confirm the package into an immutable `ready` revision.
5. Run preflight and evidence-sufficiency analysis with draft-only outputs.
6. Review findings, request evidence, and confirm weaknesses.
7. Generate profile-specific draft artifacts (FedRAMP 20x, Rev. 5 transition,
   agency FISMA security-only).
8. Submit, approve, and download an exact export ZIP.
9. Attach external authorization decisions and support post-ATO revision deltas.
10. Operate on RHEL 9 with documented backup, recovery, upgrade, and audit
    verification.

The product **does not** issue ATOs, generate official assessor conclusions,
replace GRC, run live scanners, or perform bidirectional GRC sync.

## 4. Product components

Each component is production-shaped. Delivery may use tested migrations and
diffs; components are not prototypes.

### Component A — Intake, extraction, and package review

**Plan:** [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md)

**Delivers:**

- One upload path per revision with `artifact_kind` classification
- Malware scan gate and production extractors (established-library dependency policy)
- LLM-assisted field mapping with human package-level confirm
- Versioned `SystemContext` snapshots
- Canonical package draft for all three profiles
- Assessor import, GRC/baseline import, scanner export ingest
- Privacy artifact attach and external AO decision record
- FedRAMP submission validation and export handoff prep
- ConMon-lite via child revisions and delta reports

**Exit gate:** Mixed-format qualification packages for all three profiles complete
upload → draft → edit → confirm → `ready` with provenance and audit.

---

### Component B — Preflight and evidence analysis

**Epic:** EP-01 (partial), EP-02, EP-07

**Delivers:**

- Preflight with separate `analysis_eligible` and `export_eligible`
- Missing, stale, orphaned, and contradictory content detection
- Immutable analysis runs tied to authority snapshot and config
- Exact matrix row coverage per assessment item
- Deterministic status ceilings and citation validation
- Model-assisted sufficiency matrix where evidence exists
- `insufficient_evidence` without model calls when no usable evidence
- Sealed draft consumes canonical package content from Component A

**Still to plan in detail:** run orchestration API surface, review-run matrix UX,
context budgeting integration, targeted re-analysis contract.

**Exit gate:** Ready revision runs full and targeted analysis; matrix is exact;
citations bind to source hashes; failed runs do not produce false success.

---

### Component C — Profile-specific draft artifacts

**Epics:** EP-03, EP-05

**Delivers:**

- **FedRAMP 20x:** schema-valid CPO, SDR, OCR drafts; SCG readiness; KSI summary;
  semantic Class C readiness checks from pinned authority catalog
- **Rev. 5 transition:** read-only import; transition gap analysis; OSCAL
  compatibility where qualified
- **Agency FISMA:** security SSP sections, SAR input pack, readiness summary,
  privacy scope notice; template-pack rendering when HS-002 closes

Assessor-owned fields remain import-only. Missing required material is an export
blocker, never fabricated content.

**Still to plan in detail:** artifact generator modules per profile, official
schema test matrix, paired JSON/Markdown output layout.

**Exit gate:** Qualification fixtures produce schema-valid official payloads where
applicable; assessor and privacy boundaries enforced in tests.

---

### Component D — Human review and remediation

**Epic:** EP-06

**Delivers:**

- Versioned `ReviewRevision` with dispositions per matrix row
- Accept, edit, reject, evidence request, weakness confirm, comment
- POA&M candidate only after explicit weakness confirmation
- No silent acceptance; no missing-evidence → weakness auto-routing
- Audit on every disposition

**Still to plan in detail:** portal review workbench, disposition state machine
UI, evidence-request workflow.

**Exit gate:** Full review cycle on qualification package; dispositions drive
recalculated draft outputs; audit trail complete.

---

### Component E — Approval and export

**Epic:** EP-06, EP-05

**Delivers:**

- Export draft from submitted review revision
- Exact payload hash binding; seven-day approval expiry (HS-010 default)
- Submitter cannot self-approve
- Sanitized ZIP only for approved hash
- Manifest, human drafts, machine payloads, provenance, validation results
- One-way export for customer GRC load (no writeback in v1)

**Still to plan in detail:** export assembly service, download authorization,
portal approval UX.

**Exit gate:** End-to-end submit → approve → download; changed payload invalidates
approval; replay-safe idempotency.

---

### Component F — Authentication, authorization, and roles

**Epic:** EP-06

**Delivers:**

- Production OIDC (customer IdP per HS-003)
- Package-scoped roles: system owner, ISSO/ISSM, control owner, assessor,
  reviewer, approver, AO-record custodian, viewer
- Default-deny object-level authorization
- CSRF on mutating portal routes
- Separation of duties on export approval

**Still to plan in detail:** IdP group mapping contract, role matrix per route,
assessor-only metadata rules.

**Exit gate:** RBAC, object-level auth, CSRF, and self-approval denial tests pass;
production identity verified with customer IdP.

---

### Component G — Search and package assistant

**Epic:** EP-07

**Delivers:**

- PostgreSQL full-text search scoped to one authorized revision
- Bounded package chat with citations
- Rate, token, turn, and context limits
- Refusal for authorization decisions and unsupported actions
- No cross-package search; no web browse; no tool execution

**Still to plan in detail:** search index lifecycle, chat session contract, portal
assistant panel.

**Exit gate:** Search and chat contracts pass deterministic tests; injection and
refusal fixtures pass.

---

### Component H — Change analysis and ConMon-lite

**Epic:** EP-07; overlaps Component A Diff 12

**Delivers:**

- Child revision lineage from authorized baseline
- Deterministic delta: changed artifacts, facts, controls, profile requirements
- Targeted re-analysis with complete child-run matrix (reused rows explicit)
- Post-ATO evidence re-upload workflow reusing Component A upload path

Does not replace ConMon platforms (no live dashboards, schedulers, or official
ongoing-authorization hosting).

**Exit gate:** Delta report and targeted run qualification on child revision;
no hidden stale parent analysis.

---

### Component I — Production operations and deployment

**Epic:** EP-08

**Delivers:**

- RHEL 9-compatible install, migrate, start, smoke, upgrade, rollback
- systemd units for API, intake worker, analyzer worker (only when runtime exists)
- nginx TLS front; unprivileged service identities; least-privilege writable paths
- Malware scanner adapter (HS-005)
- Backup, restore, retention, purge, legal hold behavior
- Health and readiness probes; queue and disk safeguards
- Operator runbooks synchronized with code and config

**Still to plan in detail:** live RHEL drill checklist, scanner integration
verification, backup target contract (HS-008).

**Exit gate:** Documented drills pass on target host; deployment-contract tests
and live validation both green; HS-005 and HS-008 closed for production claims.

---

### Component J — Security, qualification, and release gates

**Epics:** EP-01, EP-07, EP-08; cross-cutting

**Delivers:**

- Malicious archive, XML, Office, SVG, and path-traversal fixtures
- Prompt-injection and model-refusal regression suite
- AI evaluation per [`docs/AI_EVALUATION_GUIDE.md`](AI_EVALUATION_GUIDE.md)
- Authority manifest qualified review (HS-001)
- Immutable evaluation record (HS-006)
- P0/P1 gate records updated through EP-08 completion

**Exit gate:** Security gate before real customer data; AI qualification gates
pass; no open hard stop blocks the claimed release scope.

## 5. Build order

Components depend on each other. Do not skip gates.

```text
EP-00 contracts (done)
  -> EP-01 core safety (partial)
  -> Component A intake/review plan (PACKAGE_EDITOR_PLAN)
  -> Component B preflight/analysis
  -> Component C profile artifacts
  -> Component D review/remediation
  -> Component E approval/export
  -> Component F auth/roles (production IdP parallel with EP-06 portal)
  -> Component G search/assistant
  -> Component H change/ConMon-lite
  -> Component I operations/deployment
  -> Component J qualification/release
```

Component A must reach its exit gate before analysis consumes sealed package
content (Component B). Review and export (D, E) require analysis matrix (B) and
profile outputs (C). Production release (I, J) requires F and closed operational
hard stops.

## 6. External hard stops

These block **claims**, not necessarily all **implementation**. Track in
[`docs/requirements/hard-stops.yaml`](requirements/hard-stops.yaml).

| ID | Blocks |
| --- | --- |
| HS-001 | Authority-dependent release; official schema qualification claims |
| HS-002 | Agency field parity / customer-ready FISMA export claims |
| HS-003 | Production identity deployment |
| HS-004 | Real customer model calls |
| HS-005 | Production customer file extraction |
| HS-006 | AI qualification / pilot claims |
| HS-008 | Production readiness (backup target, keys) |
| HS-009 | Complete Class C package readiness claims without assessor inputs |
| HS-010 | Customer-specific retention/approval overrides (defaults in use) |
| HS-007 | GRC writeback (out of scope v1) |

`dev_local` may use substitutes at external boundaries only; domain behavior
matches production contracts.

## 7. Qualification corpus (release requirement)

No profile is release-complete until its corpus passes end-to-end:

1. **Agency FISMA** — mixed-format package, qualified template pack when HS-002
   closes, security-only boundary, privacy notice
2. **FedRAMP 20x Class C** — official-schema CPO/SDR/OCR, KSI and assessor import
   fixtures, semantic readiness rules
3. **Rev. 5 transition** — imported SSP/SAP/SAR/POA&M, OSCAL where qualified,
   transition gap analysis
4. **Hostile inputs** — malformed files, injection, duplicates, partial failures,
   crash/replay recovery

Corpora live under `data/qualification/` (to be populated) and run in CI plus
manual release checklist.

## 8. What is explicitly out of final product scope

Per technical spec Section 3.2:

- FedRAMP Agency Certification path, Class D, DoD/IC/classified workflows
- Privacy assessment execution
- Official AO / 3PAO / FedRAMP decisions
- Live cloud, scanner, or GRC collection
- Bidirectional GRC sync and GRC writeback
- Scan or test execution
- FedRAMP PMO / Marketplace submission
- Full ConMon platform replacement
- Multi-customer SaaS tenancy

## 9. Next planning artifacts

Component A is fully planned in [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md).
Before large-scale implementation across B–J, add focused contract sections or
short sub-plans only where detail is still missing:

| Priority | Artifact | Unblocks |
| --- | --- | --- |
| P0 | Analysis and matrix API + portal contract | Component B, D |
| P0 | Review disposition and export approval contract | Component D, E |
| P1 | Profile artifact generator spec (20x / Rev5 / FISMA) | Component C |
| P1 | OIDC role mapping and route authorization matrix | Component F |
| P1 | RHEL release drill and scanner verification runbook | Component I |
| P2 | Search and package assistant contract | Component G |

Do not duplicate normative behavior already in the technical spec; extend with
implementation file lists, acceptance tests, and exit gates per component.

## 10. Definition of done (full product)

The final product is done when:

1. All components A–J reach their exit gates.
2. All three supported profiles pass qualification corpora.
3. EP-00 through EP-08 acceptance maps in
   [`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](../ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md)
   are satisfied.
4. Open hard stops for the claimed release scope are closed or explicitly
   deferred with no false marketing claims.
5. Code, schemas, examples, systemd/nginx assets, operator docs, traceability,
   and deployment-contract tests remain synchronized.

Until then, describe the product accurately as an **evidence analysis and
authorization-package preparation workspace** with a growing analysis, review,
and export surface — not a complete replacement for GRC, assessors, or AO
processes.
