# Final Product Implementation Plan

Status: Approved master plan (Phase 6 delivered-status reconciliation 2026-07-14).

Normative contracts remain in [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md),
user workflow in
[`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](../ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md),
traceability in [`docs/requirements/traceability.yaml`](requirements/traceability.yaml),
and release evidence in [`docs/RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md).

**Delivered-status legend:** ✅ code-complete (contract tests) · 🟡 partial · ⛔ blocked · 🔜 environment-not-run

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

### Component A — Intake, extraction, and package review — 🟡

**Plan:** [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md)  
**Gate:** [`P3_GATE_RECORD.md`](P3_GATE_RECORD.md)

**Delivered (code-complete):** upload path, extraction library, intake work leases, draft editor, sealed confirm, dev_local synthetic path, mixed-format extractors with hostile fixtures.

**Residual:** production malware scanner and customer extraction (**HS-005**).

---

### Component B — Preflight and evidence analysis — 🟡

**Epic:** EP-01 (partial), EP-02, EP-07  
**Gate:** [`P6_ANALYSIS_GATE_RECORD.md`](P6_ANALYSIS_GATE_RECORD.md) (analysis paths)

**Delivered:** preflight/export readiness, deterministic and model-assisted runs, exact matrix persistence, citation validation, analyzer worker.

**Residual:** live customer model calls (**HS-004**); full EP-02 gate on customer hosts.

---

### Component C — Profile-specific draft artifacts — 🟡

**Epics:** EP-03, EP-05  
**Gate:** [`P2_GATE_RECORD.md`](P2_GATE_RECORD.md), [`P4_GATE_RECORD.md`](P4_GATE_RECORD.md)

**Delivered:** FedRAMP 20x/Rev5/FISMA generators, vendored schema validation, export assembly, assessor-import boundaries.

**Residual:** qualified authority (**HS-001**), agency template parity (**HS-002**), assessor inputs (**HS-009**).

---

### Component D — Human review and remediation — 🟡

**Epic:** EP-06  
**Gate:** [`P5_GATE_RECORD.md`](P5_GATE_RECORD.md)

**Delivered:** `ReviewRevision` API, dispositions, comments, POA&M routing after weakness confirm, portal review workbench.

**Residual:** live Playwright browser acceptance (environment-not-run).

---

### Component E — Approval and export — 🟡

**Epic:** EP-06, EP-05  
**Gate:** [`P5_GATE_RECORD.md`](P5_GATE_RECORD.md)

**Delivered:** export draft, hash-bound approval, self-approval denial, expiry processing, sanitized ZIP download with audit.

**Residual:** customer approval drills on live IdP hosts.

---

### Component F — Authentication, authorization, and roles — 🟡

**Epic:** EP-06  
**Gate:** [`P5_GATE_RECORD.md`](P5_GATE_RECORD.md)

**Delivered:** Authlib OIDC JWT validation, Postgres sessions, package-scoped RBAC, CSRF/origin gate, identity-header stripping, operator `purge-auth`.

**Residual:** customer IdP verification (**HS-003**).

---

### Component G — Search and package assistant — 🟡

**Epic:** EP-07  
**Gate:** [`P6_ANALYSIS_GATE_RECORD.md`](P6_ANALYSIS_GATE_RECORD.md)

**Delivered:** PostgreSQL FTS index (`20260717_0012`), bounded chat with `CHAT_*` limits, refusal/injection tests, portal assistant panel, `ato-operator rebuild-search-index`.

**Residual:** AI qualification gates (**HS-006**).

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

### Component I — Production operations and deployment — 🟡 / 🔜

**Epic:** EP-08  
**Gate:** [`P7_GATE_RECORD.md`](P7_GATE_RECORD.md)

**Delivered:** install/upgrade/drain/rollback scripts, systemd units, nginx templates, operator runbooks, validation drill dispatchers, deployment-contract tests.

**environment-not-run:** live RHEL drills. **customer-gated:** scanner (**HS-005**), backup (**HS-008**).

---

### Component J — Security, qualification, and release gates — 🟡 / ⛔

**Epics:** EP-01, EP-07, EP-08; cross-cutting

**Delivered:** hostile fixtures, prompt-injection regression, qualification manifest, validation-drill and AI-evaluation record contracts, P0–P7 gate records, [`RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md).

**blocked:** immutable passing AI evaluation record (**HS-006**); authority qualification (**HS-001**).

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

Corpora live under `data/qualification/` with a sealed manifest validated by
`ato-operator qualification-check` and contract tests. Live PostgreSQL workflow
integration runs in CI `integration-postgres` and locally when
`ATO_TEST_DATABASE_URL` is set.

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

## 9. Planning status (Phase 6)

Component A is fully planned in [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md).
Components B–J have code-complete slices recorded in [`RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md)
and P2–P7 gate records. Remaining work is live-host validation, customer inputs,
and hard-stop closure — not greenfield contract invention.

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
