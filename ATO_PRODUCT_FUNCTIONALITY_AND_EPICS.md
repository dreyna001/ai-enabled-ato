# ATO Evidence Analysis Portal Functionality and Epics

**Status:** User-facing workflow and delivery map (Phase 6A upload-first reconciliation 2026-07-17)  
**Normative implementation contract:** [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md)  
**Release evidence:** [`docs/RELEASE_EVIDENCE_INDEX.md`](docs/RELEASE_EVIDENCE_INDEX.md)

This document describes what users do and receive. It does not override technical schemas, state transitions, security rules, or release gates.

## 1. Create a system

### User provides

- System name
- Owner and viewer groups
- External system identifier, when one exists

### Product does

1. Confirms the user may create a system.
2. Validates the required system identity and access metadata.
3. Creates a stable system record.
4. Supports soft archive (`archived_at`); default lists hide archived systems. No hard delete.

### User receives

- A system workspace

### Product does not

- Store an authorization profile, certification class, or impact level on the System
- Select a baseline or impact level
- Tailor agency controls
- Create a DoD, IC, privacy, classified, or FedRAMP Agency Certification workflow

## 2. Create a package revision and upload evidence (upload-first)

### User provides

- Optional parent revision (must already be `ready` when linked)
- Optional parent link only; no profile, certification class, impact level, data origin, sensitivity, or title fields at create
- One or more supported source files after the revision exists

### Product does

1. Authorizes the system and package action.
2. Creates a minimal PackageRevision under the selected System with status `uploading`.
3. Accepts uploads and finalization without requiring path metadata first.
4. Streams files into generated temporary paths.
5. Enforces total, per-file, file-count, type, and archive limits.
6. Runs malware scanning before extraction (production requires an approved scanner; dev/demo may use a substitute — not production evidence).
7. Detects type and safely extracts supported content.
8. Hashes and stores each source unchanged.
9. Records source dates and locators.
10. Chunks and indexes extracted text for bounded retrieval.
11. Applies model-routing policy before any intake MAP, OCR, vision, or analysis call; pre-attestation real model calls remain policy-blocked unless approved routing supports them.

### User receives

- A revision workspace ready for upload
- Per-file scan and extraction status
- Explicit errors for rejected or unreadable content
- A package revision that is not yet analysis-ready

### Product does not

- Require profile, class, impact, data origin, or sensitivity before the first upload
- Fetch URLs embedded in documents
- Execute scans, macros, formulas, scripts, or package content
- Silently truncate over-limit content
- Send blocked data to a model
- Treat dev mock scanner or routing behavior as production evidence

## 3. Complete intake, attestation, and confirm the package draft

### User provides

- Authorization path metadata after upload and intake begin: profile, certification class or impact level (as applicable)
- Required human attestation for **data origin** and **sensitivity** (never AI-written)
- Edits to pre-filled draft fields and conflict resolutions in the Package Editor
- Explicit **Confirm Package** when satisfied

### Product does

1. Runs deterministic parsers for known formats.
2. Runs bounded intake **MAP** passes (one structured model call per covered artifact or chunk group, packed to the configured context utilization cap — default 70% minus output and instruction reserves).
3. Runs deterministic intake **REDUCE** merge into `PackageRevisionDraft`, `field_provenance`, gap list, and conflict records.
4. Surfaces an intake readiness report: files received, suggested path metadata, gaps, MAP step status (including `policy_blocked`), and conflicts.
5. Reveals editable metadata and Package Editor tabs after upload finalize; AI may **suggest** profile, class, and impact level but never auto-locks them.
6. Requires the operator to select **data origin** and **sensitivity** manually; backend readiness remains the source of truth for confirm eligibility.
7. Resolves draft-field conflicts through existing ETag draft save; metadata-only conflicts resolve through metadata PATCH with ETag.
8. On confirmation, seals the current `awaiting_confirmation` PackageRevision as immutable `ready`; it does not create another revision.
9. Creates a child PackageRevision with the ready revision as its parent for any later source, canonical fact, profile, label, or link change.

### User receives

- Pre-filled, fully editable package draft with provenance badges
- Intake readiness and conflict panels
- A canonical package snapshot after confirm
- Field-level provenance showing upload vs model-assisted pre-fill

### Product does not

- Write `data_origin` or `sensitivity` from model output
- Auto-lock AI suggestions for profile, class, or impact
- Treat model output as authoritative without human edit and confirm
- Modify the source upload
- Change a ready package revision instead of creating a child revision

## 4. Run package preflight

### Product does

1. Validates schema, IDs, dates, links, required path identity, and package limits.
2. Separates analysis eligibility from export readiness.
3. Identifies missing, stale, orphaned, contradictory, and unconfirmed content.
4. Shows which official or customer-required package materials remain missing.
5. Computes an informational percentage of passed applicable checks.

### User receives

- Analysis blockers
- Export blockers
- Warnings and evidence requests
- A path-specific readiness checklist

### Product does not

- Use the percentage alone to block analysis
- Treat missing evidence as proof of a weakness
- Hide missing package requirements behind a high score

## 5. Analyze evidence sufficiency

### Product does

1. Creates an immutable run tied to one PackageRevision, authority snapshot, configuration, prompt bundle, and model profile.
2. Builds the exact expected inventory of controls, FedRAMP rules, or KSIs.
3. Applies deterministic stale, missing, broken-link, and context checks.
4. Skips model calls for items with no usable evidence and marks them `insufficient_evidence`.
5. Sends bounded fact bundles for other items.
6. Validates structured responses, citation locators, allowed IDs, and exact row coverage.
7. Applies deterministic status ceilings.
8. Fails the run rather than marking an incomplete matrix successful.

### User receives

For each assessment item:

- Draft analysis status
- Finding summary
- Gaps
- Assessor questions
- Typed citations
- Context-completeness indicator

### Status meaning

| Status | Plain meaning |
| --- | --- |
| Supported | Supplied, reviewed context directly supports all material claim elements |
| Partial | Some support exists but important elements are missing, stale, weak, or not fully reviewed |
| Unsupported | Supplied evidence contradicts the claim or shows the implementation is absent |
| Insufficient evidence | The package does not contain enough usable evidence to decide |

These are draft analysis labels, not official control, certification, or authorization status.

## 6. Prepare FedRAMP 20x Program materials

### User provides

- Provider-owned CPO, SDR, OCR, SCG, KSI, and supporting facts
- Imported independent assessor material
- KSI validation methods, results, and metric history
- Required dates, incidents, vulnerabilities, agencies, and change facts

### Product does

1. Validates CPO, SDR, and OCR against pinned official schemas.
2. Applies pinned Program/Class C semantic and cadence rules.
3. Drafts provider-owned prose only from supplied facts.
4. Preserves assessor-owned material as imported content.
5. Identifies missing operational and independent-assessment obligations.
6. Produces paired official JSON and human-readable drafts.
7. Produces auxiliary readiness, matrix, KSI, and delta analysis.

### User receives

- CPO draft
- SDR draft
- OCR draft or initial example
- SCG readiness report
- FedRAMP package-readiness report
- Evidence/KSI matrix
- Optional package delta and confirmed internal weakness candidates

### Product does not

- Perform KSI validation methods
- Generate independent verification or validation
- Invent an incident-free period or absence of vulnerabilities
- Host required reviews or submit the package
- Present an auxiliary product POA&M as a required 20x artifact

## 7. Prepare agency FISMA security materials

### User provides

- Customer-authoritative tailored security controls
- Organization-defined parameters and inheritance decisions
- Agency template pack and field mappings
- Implementation statements and evidence

### Product does

1. Validates the supplied control inventory and evidence links.
2. Performs bounded evidence analysis.
3. Drafts security SSP sections and SAR input material.
4. Routes evidence gaps to requests.
5. Routes potential weaknesses to human review.
6. Creates a POA&M candidate only after a human confirms a weakness.
7. Produces a security-readiness summary with a privacy-scope notice.

### User receives

- Security SSP section draft
- SAR input pack
- Security readiness summary
- Evidence sufficiency matrix
- Human-confirmed POA&M candidates

### Product does not

- Decide baseline, tailoring, parameters, or inheritance
- Assess privacy controls
- Claim a signed or official SAR
- Fill missing owner, severity, due date, milestone, or risk values

## 8. Review findings and drafts

### User actions

For each row, an authorized reviewer may:

- Accept
- Edit
- Reject
- Request evidence
- Confirm a weakness
- Add a comment

### Product does

1. Preserves the immutable model output.
2. Writes human decisions into a versioned review revision.
3. Prevents lost updates with record versions.
4. Recalculates draft outputs from the selected review revision.
5. Audits every decision.

### Product does not

- Rewrite history
- Treat silence as acceptance
- Convert a missing-evidence row into a POA&M weakness

## 9. Run targeted re-analysis and compare revisions

### User provides

- A new PackageRevision or selected affected assessment items
- A prior successful run

### Product does

1. Detects changed source hashes, facts, links, profile, and authority versions.
2. Forces a full run for material profile, authority, or canonical-fact changes.
3. Recomputes affected rows otherwise.
4. Copies reused rows with explicit parent provenance into a complete child-run matrix.
5. Shows additions, removals, changed status, changed citations, and changed package requirements.

### User receives

- A complete new run
- A package delta report
- Provenance showing which rows were recomputed or reused

### Product does not

- Modify a prior run
- Hide stale parent analysis in a partial result

## 10. Use package search and assistant

### User provides

- Search terms or one package-scoped question

### Product does

1. Enforces authorization to the selected package revision.
2. Retrieves only package-scoped content.
3. Limits context, rate, input size, turns, and daily token use.
4. Returns typed citations for factual claims.
5. Refuses decisions or unsupported questions.

### User receives

- Cited evidence lookup
- Plain-language explanation of package facts
- Draft language based on confirmed facts
- Gap and comparison explanations

### Product does not

- Search other packages
- Browse the web
- Execute tools or actions
- Change package data
- Answer whether an ATO or certification should be granted

## 11. Submit, approve, and export

### Package owner does

1. Selects one review revision.
2. Reviews all export blockers.
3. Creates an exact export draft.
4. Submits its payload hash for approval.

### Approver does

1. Opens the exact submitted payload.
2. Approves or rejects with a reason.

### Product does

1. Prevents the submitter from approving when `SINGLE_USER_MODE_ENABLED` is `false` (default and production examples).
2. Allows the same principal to submit and approve export only when single-user mode is explicitly enabled for dev/demo; authentication, target access, CSRF, ETag, idempotency, payload-hash binding, expiry, and audit boundaries remain unchanged.
3. Invalidates approval after seven days or any payload change.
4. Produces a sanitized ZIP only for the approved hash.
5. Authorizes and audits each download.
6. Includes manifest, human drafts, machine payloads, provenance, and validation results.

### Product does not

- Write to a GRC or government endpoint in v1
- Make approval equivalent to official authorization
- Reuse approval for changed content

## 12. Operate the on-prem application

### Platform administrator does

- Connects the customer IdP
- Configures non-secret runtime settings in the validated JSON selected by `ATO_RUNTIME_CONFIG_PATH`
- Provisions secret bytes separately through protected credential references
- Enables explicit capabilities only after their required endpoints, credentials, policy, and qualification are available
- Configures malware scanning, TLS, storage, backup, retention, and monitoring
- Runs explicit migration, start, smoke, upgrade, rollback, restore, and audit-integrity procedures

### Product does

- Runs as unprivileged services on RHEL 9-compatible Linux
- Fails startup before serving when required configuration or enabled-capability dependencies are invalid
- Keeps application code root-owned and grants each process only its required writable paths and credentials
- Exposes only HTTPS externally
- Restricts outbound connections
- Tracks durable jobs and recovers expired safe work
- Reports health, queue, storage, model, auth, approval, backup, and audit status
- Stops new uploads/runs before disk exhaustion
- Ships process, proxy, timer, and credential assets only when their runtime behavior exists

### Product does not

- Expose model endpoint configuration to portal users
- Use `config.env`, capability bundles, or environment-variable sprawl as a second settings source
- Overwrite live customer configuration or credentials during installation
- Store secrets in repository examples or logs
- Treat static deployment-contract tests as proof of a successful RHEL deployment
- Claim high availability in v1
- Claim one-hour RPO with daily-only backup

## 13. Epic acceptance map

**Legend:** ✅ code-complete (deterministic tests) · 🟡 partial / environment-not-run · ⛔ blocked by hard stop

### EP-00 - Contract freeze — ✅

Done when:

- Active documents name the same paths, artifacts, states, data labels, and boundaries.
- Official authority snapshots are pinned and hashed.
- Internal schemas, OpenAPI, threat model, AI evaluation guide, operations/config contracts, traceability, and deployment-contract tests exist.

**Recorded:** [`docs/P1_GATE_RECORD.md`](docs/P1_GATE_RECORD.md), [`docs/P6_GATE_RECORD.md`](docs/P6_GATE_RECORD.md)

### EP-01 - Core safety — 🟡 partial

Done when:

- Routing policy always precedes model calls.
- Configured limits are enforced.
- Matrix rows and citations are exact and stable.
- Runs are immutable and crash-safe.
- Invalid, blocked, quarantined, retryable, failed, cancelled, and succeeded outcomes are distinct.
- Runtime JSON, secret references, explicit capability flags, semantic startup validation, and deployment scaffold satisfy deterministic contract tests.

**Delivered:** P0 gate helpers, model gateway/routing, matrix coverage, job lease recovery, deployment contracts. **Residual:** live quarantine production routes; portal XSS suite (**P0-008** planned).

### EP-02 - Package foundation — 🟡 partial

Done when:

- Systems and PackageRevisions exist.
- Upload-first revision create defers path metadata until post-upload attestation.
- Bounded intake MAP/REDUCE populates an editable draft; human-only labels require operator attestation.
- Postgres jobs recover without duplicate side effects.
- One FISMA synthetic package completes the full backend flow.
- Worker units and worker credential/config projections are added only with the implemented worker runtime and its replay/readiness tests.

**Delivered:** upload-first API and portal create, system soft-archive, context packer (`CONTEXT_UTILIZATION_TARGET`), intake MAP/REDUCE worker path, intake report and conflict UI, metadata deferral migration (`20260717_0013`), draft editor confirm/seal, intake workers, workflow integration tests (CI optional). **Residual:** production customer extraction (**HS-005**); upload-first **P7** integration gate pending (see [`docs/UPLOAD_FIRST_INTAKE_PLAN.md`](docs/UPLOAD_FIRST_INTAKE_PLAN.md)).

### EP-03 - FedRAMP 20x Program — 🟡 partial

**Delivered:** Class C generators, vendored schema validation, qualification fixtures. **Residual:** qualified authority review (**HS-001**), assessor inputs (**HS-009**). Gate: [`docs/P2_GATE_RECORD.md`](docs/P2_GATE_RECORD.md)

### EP-04 - Secure intake — 🟡 partial

**Delivered:** extraction library, hostile fixtures, intake work leases. **Residual:** production scanner (**HS-005**). Gate: [`docs/P3_GATE_RECORD.md`](docs/P3_GATE_RECORD.md)

### EP-05 - Draft artifacts — 🟡 partial

**Delivered:** FedRAMP/FISMA generators, export assembly, assessor import boundaries. **Residual:** agency template parity (**HS-002**). Gate: [`docs/P4_GATE_RECORD.md`](docs/P4_GATE_RECORD.md)

### EP-06 - Review portal — 🟡 partial

**Delivered:** OIDC sessions, RBAC matrix, review/export API, React portal, upload-first workflow UX, Playwright asset contracts, optional single-user export mode (`SINGLE_USER_MODE_ENABLED`, default `false`). **Residual:** live browser E2E on managed stack (environment-not-run); customer IdP (**HS-003**). Gate: [`docs/P5_GATE_RECORD.md`](docs/P5_GATE_RECORD.md)

### EP-07 - Advanced analysis — 🟡 partial / ⛔ qualification

**Delivered:** search, chat, model-assisted analyzer unit paths, refusal/injection tests. **Blocked:** adjudicated AI qualification (**HS-006**). Gate: [`docs/P6_ANALYSIS_GATE_RECORD.md`](docs/P6_ANALYSIS_GATE_RECORD.md)

### EP-08 - On-prem release — 🟡 partial

**Delivered:** systemd/nginx/install scripts, operator docs, drill dispatchers, qualification manifest. **Residual:** live RHEL drills (environment-not-run); **HS-005**, **HS-008**. Gate: [`docs/P7_GATE_RECORD.md`](docs/P7_GATE_RECORD.md)

## 14. Delivery rule

Implementation follows EP-00 through EP-08. A later epic does not begin while a required earlier contract or exit gate is missing. Every epic preserves the cross-cutting runtime/deployment contract: code, JSON schema and redacted examples, explicit capability dependencies, process-specific credentials, deployment assets, operator docs, traceability, and deterministic tests change together. Detailed requirements and hard stops are in [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md).
