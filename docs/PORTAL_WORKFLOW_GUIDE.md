# Portal Workflow Guide

This document describes the ATO Evidence Analysis Portal: what each screen and
control does, how work flows from system creation through export, where LLM calls
occur, what domain and technical checks run at each stage, and what ATO-related
artifacts are produced.

Normative contracts live under `docs/contracts/`. This guide is operator-facing
documentation aligned with the current portal and API implementation.

---

## What the portal is

The portal is a single-page **Package Workflow** application. After OIDC
sign-in, almost all work happens on one scrollable page that walks through:

1. **System** — the authorization target
2. **Package revision** — one version of that system's evidence package
3. **Revision lifecycle** — upload → intake → draft → confirm → analyze → review → export
4. **Run outputs** — artifacts, matrix, dispositions, export ZIP

The product prepares **draft authorization readiness artifacts**. It does not
grant an Authority to Operate (ATO), certify compliance, or replace GRC or
assessor systems.

---

## Three kinds of intelligence

| Kind | Where | Role |
|------|--------|------|
| **Deterministic rules** | Intake parsing, draft validation, seal, deterministic analysis, export assembly | Schema, state machine, digests, control catalogs, hard-stops |
| **LLM (text model)** | Optional intake normalization; targeted/full analysis; package chat | Propose field values or sufficiency judgments from evidence text |
| **Human reviewer** | Draft edit, dispositions, export approval | Official judgment; triggers POA&M candidates and evidence requests |

**WSL demo note:** the **Start Deterministic Run** button creates a
`deterministic_only` run with **zero LLM calls**. It only works on **synthetic**
packages and produces a full matrix where items default to `insufficient_evidence`
unless real evidence is linked and a **targeted** or **full** run is used.

---

## Portal shell (always visible)

### Left sidebar

| Control | Purpose |
|---------|---------|
| **Package Workflow** | Nav link to the main workflow page |
| **User / groups** | Signed-in actor and OIDC group membership |
| **Sign Out** | Ends the OIDC session |

### Top readiness banner (when shown)

Shows API readiness warnings (degraded health, reconciliation required, draft
authority manifest, and similar). When degraded, some features (for example
package chat) may be disabled even if the page loads.

### Page banners

- **Blue info banner** — success messages ("Draft saved", "Run started")
- **Red error banner** — API failures

---

## End-to-end workflow diagram

```text
Sign In
  → Create / Select System
  → Create / Select Revision
  → Upload Files + Finalize
  → Scanning / Extracting (auto)
  → Edit Draft + Save
  → Confirm Package (seal)
  → Revision Ready
  → Preflight
  → Start Analysis Run
  → Matrix + Artifacts
  → Review Dispositions
  → Export + Approve + Download ZIP
```

---

## Stage-by-stage reference

### 0. Sign-in and readiness

**Portal:** Login page, sidebar, readiness banner.

| | |
|--|--|
| **LLM** | None |
| **Domain** | OIDC group → role mapping (system owner vs viewer); hard-stop awareness (HS-003 identity) |
| **Technical** | Session cookie, CSRF; `/health/ready` probes database, storage, jobs, configuration, authority manifest |
| **Artifacts** | None user-visible; audit events only |

Readiness **degraded** (for example authority manifest still `draft`, HS-001) may
block some features like chat but often still allows walking the workflow.

---

### 1. Create system

**Portal:** **Systems** card — **Create System**, system selection pills.

| | |
|--|--|
| **LLM** | None |
| **Domain** | Names the authorization target for all package revisions |
| **Technical** | RBAC; UUID identity; audit event |
| **Artifacts** | `System` database record only |

---

### 2. Create revision

**Portal:** **Package Revisions** card — quick **Create Revision** or full form.

#### Full form fields

| Field | Purpose |
|-------|---------|
| **Parent Revision (Optional)** | Start from a prior ready revision (change analysis, targeted reruns) |
| **Profile** | Agency FISMA, FedRAMP Rev. 5 transition, or FedRAMP 20x program |
| **Certification Class** | FedRAMP 20x only — Class B or C (replaces impact level) |
| **Impact Level** | FISMA / FedRAMP Rev5 — Low, Moderate, or High |
| **Data Origin** | Synthetic, redacted non-production, or customer production |
| **Sensitivity** | Data classification labels |
| **Create Revision With Selected Options** | Creates and selects the revision |

Parent selection inherits profile and locks the profile dropdown.

| | |
|--|--|
| **LLM** | None |
| **Domain** | Chooses authorization framework path and baseline metadata |
| **Technical** | Profile enum validation; parent must be `ready` |
| **Artifacts** | `PackageRevision` row with status `uploading` |

#### Profile behavior at create

| Profile | Impact / class | Authorization path (later) |
|---------|----------------|----------------------------|
| FISMA | Impact level required | Agency |
| FedRAMP Rev5 | Impact level required | FedRAMP |
| FedRAMP 20x | Certification class B/C; no draft impact level | FedRAMP |

---

### 3. Upload and finalize

**Portal:** **Choose files**, per-file artifact kind, **Upload N file(s)**,
**Finalize upload**.

| | |
|--|--|
| **LLM** | None |
| **Domain (ATO)** | Accepts evidence types: manifests, PDFs, DOCX, OSCAL, scanner exports, FedRAMP CPO/SDR/OCR/SCG, privacy artifacts, architecture, attestations |
| **Technical** | File type and size limits; RBAC; content-addressed blob storage |

**Artifacts produced:**

| Artifact | Location / form |
|----------|-----------------|
| Source blob bytes | `{storage_root}/{sha2_prefix}/{sha256}` |
| Source artifact record | Database: kind, filename, digests, scan/extraction status |
| Content manifest (after finalize) | `manifests/packages/{revision_id}/content-manifest.json` |

Revision status: `uploading` → `scanning` after finalize.

---

### 4. Scanning (malware)

**Portal:** **Intake progress** — "Scanning Uploaded Artifacts" (read-only, auto-refresh).

| | |
|--|--|
| **LLM** | None |
| **Domain (ATO)** | HS-005: no extraction until files are clean (production uses real AV; dev/WSL may use a digest substitute) |
| **Technical** | ClamAV or dev substitute; size and SHA re-check; state machine |

**Outcomes:**

- Clean → `extracting`
- Infected → `quarantined` (terminal)
- Type mismatch → `invalid`

**Artifacts:** updated `SourceArtifact.malware_scan_status` only.

---

### 5. Extracting (intake)

**Portal:** "Extracting and Mapping Package Content" (read-only, auto-refresh).

| | |
|--|--|
| **LLM (optional)** | **Yes — `normalize_proposal`, 0–2 calls per normalization step** |
| **Domain (ATO)** | Parses uploads into draft structure |
| **Technical** | PDF/DOCX/XLSX/JSON/XML extraction with limits; zip/XML safety; draft JSON schema; provenance |

#### What intake produces in the draft (domain)

- **Control implementation statements** → `security_controls.{control_id}.implementation_statement`
- **Implementation status** per control
- **System context** fields (display name, boundary, mission, authorization path)
- **Profile imports** (FedRAMP SSP/SAP/SAR/POA&M/OSCAL as import-only objects)
- **Assessor-owned fields** tagged import-only; owner uploads cannot populate assessor sections
- **Evidence links** and profile-specific sections (FedRAMP 20x, Rev5, FISMA)

#### Intake LLM (`normalize_proposal`) — when it runs

| Aspect | Detail |
|--------|--------|
| **When** | After deterministic extraction, if empty draft fields exist and extracted text segments are available |
| **Input** | Fact bundle: empty targets + evidence excerpts |
| **Output** | Proposed values for allowed empty fields only |
| **Guardrails** | Cannot write assessor inputs, findings, POA&M, or profile_id; must cite source artifact; max 2 calls; routing/policy can block (`policy_blocked`) |
| **Debug artifacts (if run)** | `revisions/{id}/normalization/{step_id}/prompt.json`, `fact-bundle.json`, `response.json` |

#### Artifacts after intake

| Artifact | Content |
|----------|---------|
| **`PackageRevisionDraft`** (database) | Full editable package document |
| **`field_provenance`** | Which upload pre-filled each field |
| Status | `awaiting_confirmation` |

The product does **not** generate official signed SSP/SAR/POA&M at intake — only
imports and draft fields.

---

### 6. Draft edit (`awaiting_confirmation`)

**Portal:** tabbed **Package Editor**.

#### Load states

- Loading skeleton
- Error banner
- Empty draft warning (404)

#### Editor tabs and fields

| Tab | Key fields | Notes |
|-----|------------|-------|
| **Package** | Title (required), Prepared For, Profile (read-only) | |
| **System** | Display Name, Authorization Boundary, Mission Summary (required); Impact Level (FISMA/Rev5 only); Authorization Path (read-only) | FedRAMP 20x hides impact level |
| **Contacts** | System Owner, ISSO (name, role, email) | |
| **Controls** | Add/Remove control; Implementation Status; **Implementation Statement** (required per control) | |
| **Evidence** | Read-only JSON of linked evidence | |
| **Profile** | Profile-specific JSON (FedRAMP 20x / Rev5 / FISMA) | Tab label varies by profile |
| **Privacy** | Privacy Scope Notice | |
| **Assessor Inputs** | Read-only; populated from intake imports | |

#### Provenance badges

- **From upload** — pre-filled from uploaded artifact
- **Model-assisted** — normalized with LLM help during intake

#### Actions

| Button | Disabled when | Action |
|--------|---------------|--------|
| **Save Draft** | Not dirty, saving, stale conflict, validation errors | Persists draft with ETag |
| **Confirm Package** | Dirty, saving, stale, validation errors | Opens confirm dialog → seals package |

**Confirm dialog:** "Seal the displayed package draft as an immutable ready revision?"

| | |
|--|--|
| **LLM** | None |
| **Domain** | Required fields, profile field combinations, control statements, authorization path |
| **Technical** | JSON schema; `If-Match` ETag; idempotency; RBAC (system owner / ISSO) |

Save runs the same seal-readiness validation as confirm.

---

### 7. Confirm / seal (`ready`)

| | |
|--|--|
| **LLM** | None |
| **Domain (ATO)** | Locks package facts and system context for analysis and export |
| **Technical** | Canonical SHA-256 digests; immutable state transition; search index build |

**Artifacts produced:**

| Artifact | ATO meaning |
|----------|-------------|
| **`SealedPackageContent`** | Immutable package document — internal SSP-shaped fact base including all **control implementation statements** |
| **`SystemContextSnapshot`** | Frozen system context (boundary, impact, control set reference) |
| **`package_revision_search_chunks`** | Full-text search index for search and chat |
| Bindings on revision | `package_content_sha256`, `system_context_snapshot_id` |

After seal, content is immutable. Changes require a new child revision.

#### FedRAMP 20x impact at seal

Draft `impact_level` stays null. The sealed system-context snapshot uses a
**nominal** FIPS 199 impact derived from certification class (for example Class C
→ low, Class B → moderate).

---

### 8. Preflight

**Portal:** **Preflight** panel — analysis eligible, export eligible, blockers, warnings.

| | |
|--|--|
| **LLM** | None |
| **Domain (ATO)** | Readiness gates before analysis and export |
| **Technical** | Computed on demand; authority and profile fingerprints |

| Check | Meaning | Severity |
|-------|---------|----------|
| Revision ready | Package confirmed | Analysis + export blocker |
| Sealed content | Facts exist for analysis | Analysis + export blocker |
| Assessor inputs present | FedRAMP assessor-owned imports (HS-009) | Export blocker only |
| Privacy artifacts present | Privacy section complete | Export blocker only |
| Profile section populated | Profile section filled | Warning only |

**Artifacts:** ephemeral JSON via API only (unless copied into export as
`validation/export-readiness.json`).

---

### 9. Dependencies and capabilities

**Portal:** read-only checklist from `/health/ready`.

Shows infrastructure probes and feature gates: Preflight, Analysis Runs, Package
Search, Package Assistant, Export Workflow — each **available** or **disabled**.

---

### 10. Change analysis (child revisions only)

**Portal:** **Change Analysis** panel when revision has a parent.

Compares to parent revision: changed controls, added artifacts, suggested
targeted assessment item IDs. Feeds **Start Targeted Run**.

---

### 11. Package Assistant (search + chat)

| Feature | LLM? | What it does |
|---------|------|--------------|
| **Search package content** | No | PostgreSQL full-text over sealed chunks and extracted artifact text |
| **Ask about this package** | Yes — `package_chat`, 1–3 calls | Grounded Q&A with citations |

#### Chat LLM behavior

- Answers only from retrieved authorized chunks
- **Refuses:** authorization decisions, risk acceptance, official compliance claims, prompt injection
- Falls back to deterministic excerpt concatenation when model is blocked (classified data, policy, limits)

**Artifacts:** none persisted (ephemeral per request).

---

### 12. Analysis runs

**Portal:** **Start Deterministic Run**, **Start Targeted Run**, run pills, run status, matrix, artifacts, review/export.

#### Run types

| Run type | Portal button | LLM | Typical use |
|----------|---------------|-----|-------------|
| `deterministic_only` | Start Deterministic Run | **No** | WSL demo / synthetic smoke test |
| `targeted` | Start Targeted Run | **Yes** | Re-analyze changed controls after child revision |
| `full` | API (no separate portal button today) | **Yes** | Full model-assisted sufficiency pass |

#### A. Deterministic run

| | |
|--|--|
| **LLM** | None (`llm_call_count = 0`) |
| **Domain** | One assessment matrix row per item in the pinned analysis profile; synthetic packages without linked evidence → `insufficient_evidence` |
| **Technical** | Exact matrix coverage; profile digest match; synthetic + ready gates; status ceilings |

#### B. Targeted / full run (model-assisted)

| | |
|--|--|
| **LLM** | **Yes — `sufficiency_matrix`, up to 2 calls per batch, 120 per run** |
| **Domain (ATO)** | Per assessment item, model proposes sufficiency status, finding summary, gaps, assessor questions (clarifying only), citations |
| **Technical** | Batches of 10; citation validation; status ceiling rules; exact coverage; schema validation; routing gates |

The model **does not** authorize, certify, or accept risk. Items with
`model_analysis_allowed=false` or no evidence get deterministic rows without LLM.

#### What the matrix is (ATO)

The matrix is **not** a POA&M or SSP. It is a **sufficiency / readiness matrix**:
per-control (or per-KSI) evidence assessment to drive human review.

#### Run artifacts (succeeded runs)

| Path | Content |
|------|---------|
| `runs/{run_id}/machine/matrix.json` | All matrix rows |
| `runs/{run_id}/artifact-manifest.json` | Manifest of run outputs and digests |
| Database `MatrixRow` | Same rows via API |
| Database `RunStep` | Step audit metadata |

The contract allows additional paths under `human/`, `machine/`, `provenance/`,
and `validation/`; current workers primarily emit `machine/matrix.json`.

#### Run status actions

| Status | Portal behavior |
|--------|-----------------|
| `queued` / `running` | Progress message; **Cancel Run** (confirm dialog) |
| `failed` / `cancelled` / `policy_blocked` | Failure message with error code |
| `succeeded` | Artifacts panel, matrix, review/export workbench |

---

### 13. Review and dispositions

**Portal:** **Review and Export** workbench.

| | |
|--|--|
| **LLM** | None — human reviewer only |
| **Domain (ATO)** | Reviewer resolves each matrix row |
| **Technical** | Review version + `If-Match`; all dispositions non-pending before submit; audit trail |

#### Disposition decisions

| Decision | Domain meaning | Side effect |
|----------|----------------|-------------|
| **accepted** | Agree with model/system status | — |
| **edited** | Override summary (edited summary required) | — |
| **rejected** | Disagree | — |
| **evidence_requested** | Need more evidence | Creates **EvidenceRequest** (only if row was `insufficient_evidence`) |
| **weakness_confirmed** | Confirmed weakness | Creates **PoamCandidate** (feeds POA&M draft at export; only if partial/unsupported) |

#### Review workflow buttons

| Button | Action |
|--------|--------|
| **Open Review Revision** | Creates review workspace for the run |
| **Save disposition** | Saves one row's decision |
| **Post row comment** / **Add comment** | Reviewer notes |
| **Submit review** | Locks review when all rows resolved |
| **Clear local resume** | Clears browser-stored review ID |

**Artifacts (database only until export):**

| Record | ATO role |
|--------|----------|
| `ReviewRevision` | Review workspace bound to one run |
| `Disposition` | Human decision per matrix row |
| `EvidenceRequest` | Structured evidence gap |
| `PoamCandidate` | Weakness flagged for POA&M draft |
| `ReviewComment` | Reviewer notes |

---

### 14. Export draft → approval → download

**Portal:** **Create export draft** → **Submit for approval** → **Approve export** / **Reject export** → **Download ZIP**.

| | |
|--|--|
| **LLM** | None — deterministic assembly from sealed package + matrix + dispositions |
| **Domain** | Bundles draft authorization package for handoff; disclaimers throughout |
| **Technical** | Payload manifest hash sealed; separate approver required; approval expiry (HS-010) |

Export does **not** certify compliance or authorize the system.

#### Common export ZIP contents (all profiles)

| Path | ATO purpose |
|------|-------------|
| `README.txt` | Hard-stop and draft disclaimers |
| `manifest.json` | Export manifest (hashes, lineage) |
| `machine/package-document.json` | Full sealed package including all control implementation statements |
| `machine/assessment-matrix.json` | Matrix from analysis run |
| `human/assessment-matrix.md` | Human-readable matrix |
| `provenance/review-run.json` | Review ↔ run linkage |
| `provenance/dispositions.json` | All human decisions |
| `validation/export-readiness.json` | Blockers and warnings at export time |
| `validation/schema-purity.json` | Official-schema structural checks |
| `human/readiness-summary.md` | Readiness narrative |

#### FISMA (`fisma_agency_security`) — generated drafts

| Path | What it is |
|------|------------|
| `human/ssp-security-draft.md` + `machine/ssp-security-draft.json` | **SSP security section draft** from sealed `security_controls` (+ optional customer template pack HS-002) |
| `human/sar-input-pack.md` + `machine/sar-input-pack.json` | **Assessor input pack** — not a signed SAR |
| `human/poam-draft.md` + `machine/poam-draft.json` | **POA&M draft** from `weakness_confirmed` dispositions + matrix rows |
| `human/security-readiness.md` + `machine/security-readiness.json` | Security readiness summary |
| `validation/fisma-export-readiness.json` | FISMA-specific validation |

#### FedRAMP Rev5 transition — preserve imports

| Path | What it is |
|------|------------|
| `machine/ssp.json`, `human/ssp.md` | Imported SSP (if uploaded) — **not generated** |
| `machine/sap.json`, `human/sap.md` | Imported SAP |
| `machine/sar.json`, `human/sar.md` | Imported SAR (assessor-owned) |
| `machine/poam.json`, `human/poam.md` | Imported POA&M |
| `machine/oscal.json`, `human/oscal.md` | Imported OSCAL (optional) |
| `machine/rev5-transition-readiness.json`, `human/rev5-transition-readiness.md` | Transition readiness |

#### FedRAMP 20x program

| Path | What it is |
|------|------------|
| `machine/cpo.json`, `human/cpo.md` | Continuous Program Oversight |
| `machine/sdr.json`, `human/sdr.md` | Significant Change Request |
| `machine/ocr.json`, `human/ocr.md` | Operational Change Request |
| `human/scg-readiness.md` | SCG readiness |
| `machine/ksi-summary.json`, `human/ksi-summary.md` | KSI methods summary |
| `machine/fedramp-readiness.json`, `human/fedramp-readiness.md` | Program readiness |

Official-shaped JSON may carry `official_schema_id` when structurally valid. All
export outputs remain **draft** artifacts.

---

## Where key ATO concepts live

| Concept | Created / edited | Becomes official? |
|---------|------------------|-------------------|
| **Control implementation statement** | Draft editor (+ intake extract/LLM) | Sealed at confirm; in export `package-document.json` and FISMA `ssp-security-draft` |
| **SSP (full)** | FedRAMP: import upload; FISMA: generated security draft only | Export drafts only |
| **SAR** | Assessor import only (Rev5) or SAR input pack (FISMA) | Product never writes signed SAR |
| **POA&M** | Rev5: import; FISMA: `poam-draft` from weaknesses | Draft candidates → export draft |
| **Assessment matrix** | Analysis run | Drives review; exported as matrix JSON/MD |
| **Evidence request** | Review disposition `evidence_requested` | Database record |
| **System context / boundary** | Draft System tab | Sealed snapshot |

---

## LLM usage summary

| Stage | LLM calls | Agent role |
|-------|-----------|------------|
| Upload / scan / finalize | 0 | — |
| Extract (deterministic) | 0 | Format parsers |
| Intake normalize (optional) | 0–2 | Map extracted text → empty draft fields |
| Draft edit / seal | 0 | — |
| Preflight | 0 | — |
| Search | 0 | PostgreSQL full-text |
| Chat | 1–3 | Grounded Q&A with refusals |
| **Deterministic run** | **0** | Rule-based matrix |
| Targeted / full run | up to 120 | Evidence sufficiency per control/KSI |
| Review / export | 0 | — |

### Model routing guardrails (all LLM calls)

- Classified data unsupported
- Customer production / CUI on external endpoints requires explicit policy approval
- Per-run and per-step call budgets
- Embedding capability always prohibited
- Vision extraction deferred (not live in current workers)

Primary implementation files:

- `src/ato_service/model_gateway.py` — central policy gate
- `src/ato_service/normalize_proposal/` — intake LLM
- `src/ato_service/sufficiency_matrix/` — analysis LLM
- `src/ato_service/package_chat.py` — chat LLM

---

## Checks summary (domain vs technical)

| Stage | Domain (ATO) | Technical |
|-------|--------------|-----------|
| Upload | Artifact kind semantics | RBAC, blob storage, manifest |
| Scan | Malware gate (HS-005) | AV, digests, state machine |
| Extract | Assessor vs owner field ownership | Parsers, schema, references |
| Draft | FIPS impact, auth path, control statements | JSON schema, ETag, profile rules |
| Seal | Immutable facts + system context | SHA-256, search index |
| Preflight | Assessor inputs, privacy for export | Ready + sealed checks |
| Analysis | Control catalog completeness | Coverage, citations, ceilings |
| Review | Disposition semantics, POA&M routing | Concurrency, audit |
| Export | HS-001/002/009/010, draft-only claims | Manifest hash, approval chain |

### Hard-stops (cross-cutting governance)

Defined in `docs/requirements/hard-stops.yaml`. Examples:

| ID | Gate |
|----|------|
| HS-001 | Qualified authority review |
| HS-002 | Customer FISMA template pack |
| HS-003 | Customer IdP verified |
| HS-004 | Model endpoint data policy |
| HS-005 | Production malware scanner |
| HS-009 | Assessor-owned FedRAMP inputs |
| HS-010 | Retention / approval override |

---

## Revision status → portal panels

| Status | What you see |
|--------|--------------|
| `uploading` | Package upload panel |
| `scanning` / `extracting` | Intake progress panel |
| `invalid` / `quarantined` / `archived` | Terminal intake panel |
| `awaiting_confirmation` | Package editor |
| `ready` | Preflight, assistant, analysis, review, export panels |

---

## Happy-path walkthrough

1. **Sign in** at `/login`
2. **Create System** → select it
3. **Create Revision** (profile, impact or cert class, data origin, sensitivity)
4. **Upload** evidence → **Finalize upload**
5. Wait for **scanning / extracting**
6. **Edit draft** → fix validation → **Save Draft**
7. **Confirm Package** → status **Ready**
8. Check **Preflight** (analysis eligible = yes)
9. **Start Deterministic Run** (or targeted/full for LLM sufficiency) → wait for **Succeeded**
10. Inspect **Run Artifacts** and **Matrix**
11. **Open Review Revision** → set every disposition → **Submit review**
12. **Create export draft** → **Submit for approval** → approver **Approve** → **Download ZIP**

---

## WSL local demo notes

- Start the portal: `bash scripts/start-portal.sh` → http://localhost:5173/
- API proxy target: `http://127.0.0.1:8001`
- See `docs/WSL_LOCAL_DEPLOY.md` for API, worker, and Bedrock/OpenAI setup
- **Start Deterministic Run** is the expected demo path for synthetic packages
- To exercise LLM sufficiency analysis, use **Start Targeted Run** or a `full` run
  with real evidence linked in the sealed package
- To get a **POA&M draft** in FISMA export, confirm weaknesses in review
  (`weakness_confirmed` dispositions)

---

## Related documentation

| Document | Topic |
|----------|-------|
| `docs/contracts/LIFECYCLE_AND_ERRORS.md` | State machine and error codes |
| `docs/contracts/README.md` | JSON schemas and OpenAPI |
| `docs/requirements/hard-stops.yaml` | Governance hard-stops |
| `docs/PACKAGE_EDITOR_PLAN.md` | Package editor product intent |
| `docs/WSL_LOCAL_DEPLOY.md` | Local deployment and portal enable |
| `docs/CONFIGURATION.md` | Runtime config including text model settings |
| `docs/AI_EVALUATION_GUIDE.md` | AI qualification harness (non-production) |

---

## Key implementation files

| Area | Path |
|------|------|
| Portal workflow page | `portal/src/pages/WorkflowPage.tsx` |
| Package editor | `portal/src/components/PackageEditor.tsx` |
| Draft validation (client) | `portal/src/utils/draftValidation.ts` |
| Intake | `src/ato_service/intake.py` |
| Draft seal | `src/ato_service/package_revision_drafts.py` |
| Preflight | `src/ato_service/preflight.py` |
| Deterministic analysis | `src/ato_service/deterministic_analyzer.py` |
| Model-assisted analysis | `src/ato_service/model_assisted_analyzer.py` |
| Review / dispositions | `src/ato_service/review_revisions.py` |
| POA&M routing | `src/ato_service/poam_routing.py` |
| FISMA SSP/POA&M generation | `src/ato_service/fisma_generator.py` |
| Export assembly | `src/ato_service/export_assembly.py`, `export_service.py` |
| Run artifacts | `src/ato_service/run_artifacts.py`, `artifact_manifests.py` |
