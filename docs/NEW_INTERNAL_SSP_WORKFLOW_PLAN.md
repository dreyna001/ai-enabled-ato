# New Internal SSP Drafting Workflow

**Status:** Implemented, cut over, and locally validated. Destructive legacy
cleanup remains deferred pending deployment-data confirmation.

**Current work:** Promoted increments and narrow acceptance boundaries live in
**Active increments (do now)** below. Later profile-registry, inheritance UI,
qualified export mappings, and FedRAMP backlog remain in
[`docs/PROFILE_DRIVEN_WORKFLOW_TODO.md`](PROFILE_DRIVEN_WORKFLOW_TODO.md).

## Active increments (do now)

Track shipped, narrowly scoped behavior here. Do not treat this section as
closing the broader profile-driven backlog.

### Increment A — Minimal ODP detection and question prompting

**Delivered.**

- Detect OSCAL organization-parameter insert tokens in pinned profile control
  `requirement_text` and mark those controls as parameterized for generation.
- Reject `{{ insert: param, ... }}` placeholder syntax in implementation
  statements during generation output validation and contextual patch validation.
- Require a tracked question when a parameterized control still lacks an
  evidence-backed implementation response after generation (profile policy
  present).

**Acceptance boundaries (not done):** full organization-defined parameter
registry, profile parameter value catalog, dedicated ODP UI, qualified export
mappings, and FedRAMP parameter packs.

### Increment B — Inherited and hybrid prompt guidance

**Delivered.**

- Ground inherited and hybrid responsibility in the existing profile
  `responsibility` enum and `implementation_statement` only.
- Generation and patch prompts instruct agents to describe provider or common
  portions only when evidence supports them and to avoid inventing inheritance
  boundaries or provider scope.

**Acceptance boundaries (not done):** separate control designation from
inheritance, common-control provider selection, inherited-implementation detail
fields, and profile inheritance registry UI.

### Increment C — Draft OSCAL 1.2.2 JSON export

**Delivered.**

- After ISSO approval, export deterministic **draft** OSCAL SSP JSON
  (`oscal-json`) from the immutable approval snapshot.
- Validate exported JSON against the digest-verified official NIST OSCAL **1.2.2**
  SSP schema (`nist-oscal-1.2.2` in `docs/contracts/authority-manifest.json`).
- Portal **Review & export** exposes **Export draft OSCAL JSON**; API route
  `/ssp-workspaces/{id}/exports/oscal-json` requires an approved revision.

**Acceptance boundaries (not done):** qualified or customer-ready OSCAL SSP
products, FedRAMP OSCAL conformance, toolchain parity, privacy or C-SCRM plan
content, and authority qualification (**HS-001**). Draft export does not prove
agency template parity (**HS-002**).

### Increment D — Profile-bound control implementation statement policy

**Delivered.**

- Built-in `agency-fisma-nist-sp800-53-rev5` bundle version **1.2.0** declares
  `implementation_statement_policy` (deterministic flags, agent instruction
  blocks, authority refs) in `ssp-requirements.json`.
- Generation and contextual patches use profile-owned statement-content, ODP,
  inherited/hybrid, and semantic-review instructions; deterministic validation
  and approval honor profile flags.
- Semantic quality findings are advisory; they do not add model-controlled
  approval blockers. **1.1.0** and **1.0.0** bundles remain loadable with
  defaults matching prior behavior.

**Acceptance boundaries (not done):** dedicated not-applicable or provider fields,
common-control provider registry or inheritance UI, full ODP registry, SP 800-53A
assessment coverage, bulk control review, FedRAMP statement rules, new portal UI,
and database migrations.

## Implementation Record

- Backend product boundary: `src/ato_service/ssp_workspace/`
- Portal product route: `/ssp`
- API product routes: `/api/v1/ssp-*`
- Database migration: `20260728_0015` (evidence removal); `20260728_0016` (agency DOCX renders)
- Built-in offline profile: **Agency FISMA — NIST SP 800-53 Rev. 5** bundle
  `agency-fisma-nist-sp800-53-rev5` version **1.2.0** (NIST control catalog release
  **5.2.0**, `implementation_statement_policy`), with legacy **1.1.0** and **1.0.0**
  load compatibility
- Profile pins final **NIST SP 800-18 Rev. 2** (version 2.0.0,
  `doi.org/10.6028/NIST.SP.800-18r2`) Table 1 **standard_coverage** metadata,
  **33** SSP items (**digital identity acceptance** optional), and profile-defined
  control-response enums enforced across generation, patches, direct edits, metrics,
  and approval
- **HS-001** and **HS-002** remain open: no authority qualification, agency template
  parity, qualified OSCAL SSP or conformance claims, privacy plan, or C-SCRM plan
  claims; draft OSCAL JSON is structural working material only (see Increment C)
- Offline profile import, activation, and workspace migrate-profile scaffolding only;
  **FedRAMP SSP profiles are not supported** in this workflow
- Minimal ODP detection, placeholder rejection, and question prompting (Increment A)
- Inherited/hybrid generation and patch guidance using responsibility and
  statements only (Increment B)
- Profile-bound implementation statement policy in bundle **1.2.0** (Increment D)
- Deterministic metrics, versioned edits, contextual patches, approval
  snapshots, revision restore, profile migration, and DOCX/JSON/draft OSCAL JSON
  export (Increment C)
- Agency-shaped DOCX (Review & export): after ISSO approval, upload customer
  `.docx` template; bounded agent mapping plan; deterministic server render with
  draft notice; reviewer blocker/warning exceptions; ISSO render approve/reject;
  preview before approval and download after; exact cache and approved-mapping
  reuse scoped to workspace, revision, template digest, and profile version;
  synchronous, DOCX-only, no generic field-map UI (**HS-002** open)
- Legacy package, analysis, and review routes are not mounted
- Validation completed:
  - Empty PostgreSQL database migrated through `20260728_0016`
  - Live PostgreSQL workflow passed from evidence and screenshot intake through
    generation, editing, agent patching, approval, and JSON/DOCX export
  - Agency DOCX mapping, render, cache, and approval paths covered by repository
    contract tests (counts not re-run for this record)
  - Backend suite: 1,814 passed, 84 explicitly skipped retired/integration tests
    (snapshot before agency DOCX tests; re-run pytest for current counts)
  - Portal suite: 129 passed; production build succeeded

## Goal

Help an internal agency ISSO turn incomplete system information into an editable SSP and control implementation statements with minimal manual effort.

## Primary User

Agency ISSO.

## Workflow

1. ISSO creates a system workspace.
2. ISSO uploads available system information:
   - Documents
   - Screenshots
   - Architecture and network diagrams
   - Policies and procedures
   - Configuration and scanner exports
3. Agents extract system facts and generate:
   - The SSP as completely as available information allows
   - Individual control objects with implementation statements
   - A short list of unresolved questions
4. ISSO uses the chatbot to:
   - Answer unresolved questions
   - Provide additional system facts
   - Request changes to the SSP
   - Request changes to one or more controls
   - Add additional evidence
5. The chatbot updates the same SSP and control objects. It does not create a separate chat-only answer.
6. Agents regenerate affected content after new answers or evidence are added.
7. The ISSO edits the SSP or individual control statements when needed.
8. When ready, the ISSO approves and exports:
   - SSP
   - Control implementation statements
   - Remaining unresolved items, if any

The ISSO does not approve every answer or agent edit. Approval is a single action when the working content is ready.

## First Profile

**Agency FISMA — NIST SP 800-53 Revision 5** (`agency-fisma-nist-sp800-53-rev5`,
version **1.2.0**; legacy **1.1.0** and **1.0.0** remain loadable)

This profile supports agency-authorized systems hosted:

- On premises
- In an agency-owned cloud environment
- Across an agency-owned hybrid environment

Hosting is a system attribute, not a separate control profile.

The profile contains:

- NIST SP 800-53 Rev. 5.2.0 control catalog (via pinned OSCAL content 1.5.0)
- NIST SP 800-53B Low, Moderate, and High baselines
- NIST SP 800-18 Rev. 2 Table 1 security-plan coverage metadata mapped to **33**
  structured SSP items (DIAS optional)
- Profile-defined `control_response` enums (implementation status, responsibility,
  question owner types)
- Workspace validation, metrics, generation contracts, and exports bound to the
  selected profile version

Agency tailoring, organization-defined parameters, customer template packs, and
field-level export mappings beyond the built-in DOCX/JSON shapes remain local
bundle or customer inputs; they do not close **HS-002**.

The applicable baseline is selected once the system impact level is known.

## Product Data

### SSP

- One editable working document
- Updated by agents, chatbot actions, or direct user edits
- Generated from the current structured system facts and control objects

### Control Object

Each control is independently editable and contains:

- Control ID and title
- Implementation status
- Implementation statement
- Inheritance or responsibility
- Supporting evidence
- Unresolved information

Agents and users can edit control objects. SSP control sections are rendered from the current control objects.

### Chatbot

The chatbot is a contextual document-editing interface. It opens beside the
current SSP section or control and:

- Resolves missing information
- Updates system facts
- Updates SSP sections
- Updates control objects
- Adds or links evidence
- Regenerates only affected content
- Shows a targeted patch before it is applied

The chatbot does not regenerate the full SSP for a local edit. Applying a patch
is atomic, versioned, and reversible.

## Portable Product Boundary

The product is divided into three layers:

```text
Core Product
  Intake -> Facts -> SSP -> Controls -> Questions -> Approval -> Export

Agency Content Bundle
  Baseline + tailoring + parameters + inheritance + SSP template

Deployment Profile
  Identity + model endpoints + storage + database + scanner + secrets
```

The core product contains no agency-name conditionals. A normal agency
deployment requires configuration and local content, not application code.

### Core Product

The same code runs in every environment and owns:

- Workspace and revision lifecycle
- Evidence intake and extraction
- Structured facts and provenance
- SSP sections and control objects
- Question tracking
- Agent-generated patches
- Deterministic coverage metrics
- ISSO approval snapshots
- Document export

### Agency Content Bundle

The versioned local bundle supplies:

- NIST catalog and baseline content
- Agency overlay and tailoring
- Organization-defined parameters
- Common-control and inheritance data
- Required SSP items and validation rules
- SSP template and export field mappings

### Deployment Profile

Validated runtime configuration supplies:

- Identity provider and role mappings
- Text and vision model endpoints
- Database and storage locations
- Malware scanner integration
- Secret references
- Network and data-handling policy

Provider-specific behavior stays at these runtime boundaries. The SSP,
control, question, approval, and metric logic remains provider-neutral.

## Canonical Records

| Record | Purpose | Key states |
| --- | --- | --- |
| `Workspace` | One system's working area | `working`, `archived` |
| `WorkspaceRevision` | Versioned editable content | `working`, `approved`, `superseded` |
| `EvidenceArtifact` | Uploaded source and extraction result | `uploaded`, `processing`, `processed`, `failed` |
| `SystemFact` | Structured fact used by documents | `active`, `superseded` |
| `SspSection` | Editable SSP section | `empty`, `generated`, `edited`, `reviewed` |
| `ControlStatement` | Individual selected control object | `empty`, `generated`, `partial`, `reviewed` |
| `Question` | Known unresolved information | `open`, `answered`, `dismissed` |
| `AgentPatch` | Proposed bounded document edit | `proposed`, `applied`, `rejected`, `stale` |
| `ProfileVersion` | Immutable imported agency bundle | `inactive`, `active`, `archived` |
| `ApprovalSnapshot` | ISSO approval tied to exact content | immutable |

Each factual value records provenance as `extracted`, `agent_generated`, or
`isso_entered`. Extracted and agent-generated facts retain evidence locators.

## Deterministic UI Metrics

The LLM never calculates displayed workflow metrics.

| Metric | Calculation |
| --- | --- |
| Evidence | Count of workspace evidence records |
| Processed evidence | Evidence where extraction state is `processed` |
| Screenshots | Evidence with a supported image media type |
| Selected controls | Count of controls in the resolved pinned profile |
| Controls drafted | Selected controls with a schema-valid non-empty implementation statement |
| Partial controls | Selected controls missing a profile-required field, citation, or tracked answer |
| Open questions | Persisted questions where state is `open` |
| Evidence links | Count of validated fact, section, and control evidence links |
| Last agent update | Timestamp of the latest successfully applied generation or patch |
| ISSO approved | Existence of an approval snapshot matching the current content hash |

The profile defines required SSP items and validation rules. SSP completion is:

```text
round(100 * satisfied_required_items / total_required_items)
```

A required item is satisfied when its value passes the profile's type, enum,
and length rules. An agent-generated factual item must also have a valid
evidence link. This percentage measures profile coverage, not authorization
readiness or control effectiveness.

`Open questions` means currently recorded unresolved questions. It does not
claim that the agent has discovered every unknown.

The workspace is reviewable when:

- All processing and generation jobs are terminal
- Every required item is satisfied or linked to an open question
- Every selected control has a statement or a tracked unresolved reason
- The current revision is saved and internally consistent

## Bounded LLM Contracts

### Evidence Extraction

Input:

- One bounded evidence artifact or chunk group
- Allowed fact targets from the active profile

Output:

- Proposed facts
- Source artifact and locator
- Extraction method
- Conflicts with current facts

### SSP and Control Generation

Input:

- Validated system facts
- Selected profile requirements
- Existing SSP section or control content
- Relevant evidence excerpts

Output:

- Targeted SSP section patches
- Targeted control statement patches
- Evidence links
- New structured questions

### Contextual Chat Editing

Input:

- Current SSP section or control
- User instruction or answer
- Related facts and evidence
- Current revision identifier

Output:

- Allowed JSON patch operations
- Added or resolved questions
- Evidence links
- Short change summary

Application code validates target paths, revision freshness, field types,
profile membership, citations, and size limits before applying a patch. Invalid
or unsupported model output changes nothing and produces a visible error.

## Reuse, Rebuild, and Retirement

### Reuse

- Authentication and authorization
- Audit logging
- Database and session foundation
- File upload and content-addressed storage
- Malware-scan boundary
- Safe document extraction
- Model routing and runtime policy
- Job processing and leases
- Citation validation
- OSCAL parsing
- Runtime configuration and deployment assets

### Build as the New Product Core

New code lives behind a clear `ssp_workspace` backend boundary and new portal
workflow components. It owns the canonical records, metrics, generation,
contextual editing, approval, and export described above.

### Retire After Cutover

- Analysis and sufficiency-matrix workflow
- Review revisions and dispositions
- SAR and POA&M generation
- Current multi-stage export approval workflow
- Read-only package chat
- Existing package confirm/seal workflow
- Old portal workflow components

Old code is not deleted until the new end-to-end workflow passes its acceptance
gate. Before removing migrations or stored records, confirm that no live agency
deployment depends on them.

## Implementation Plan

### Diff 1 — Contracts and Persistence

**Delivered.**

Implement canonical records, states, validation schemas, revision hashes, and
metric functions. Reuse the existing database, audit, authorization, and
runtime foundations.

Acceptance:

- Invalid state transitions fail without partial writes
- Metrics are deterministic for fixed fixtures
- Approval snapshots bind to an exact revision hash
- No existing workflow route changes yet

### Diff 2 — Workspace, Profile, and Intake

**Delivered.**

Add the workspace API and portal shell, resolve the first local NIST profile,
and connect existing upload and extraction capabilities.

Acceptance:

- An ISSO can create a workspace and select Low, Moderate, or High
- The workspace pins an immutable profile version
- Supported evidence is uploaded, processed, and listed
- Failed or unsupported evidence is visible and does not silently disappear

### Diff 3 — Initial Generation

**Delivered.**

Add bounded fact extraction, SSP-section generation, control-statement
generation, question creation, and deterministic coverage reporting.

Acceptance:

- One representative system fixture generates an SSP and full selected control inventory
- Every generated factual claim has evidence or is linked to an open question
- Missing information remains missing or becomes a question
- Replaying the same inputs does not duplicate questions or controls

### Diff 4 — Editing and Contextual Agent

**Delivered.**

Add direct editing, contextual chat, validated targeted patches, conflict
detection, and rollback.

Acceptance:

- An ISSO can edit an SSP section or control directly
- The agent changes only the requested targets
- Stale patches are rejected
- Manual edits are not overwritten by unrelated regeneration
- Applied patches are auditable and reversible

### Diff 5 — Approval and Export

**Delivered.**

Add one-step ISSO approval and export from an immutable approval snapshot.
Ship canonical JSON and DOCX plus draft OSCAL 1.2.2 JSON validated against the
pinned official SSP schema; qualified or customer-ready OSCAL remains out of scope.

Acceptance:

- Approval records actor, timestamp, profile version, and content hash
- Editing approved content creates a new working revision
- DOCX, JSON, and draft OSCAL JSON exports agree with the approved snapshot
- Draft OSCAL JSON fails closed when authority digest verification fails
- Open questions can be included in an export appendix
- Optional agency-shaped DOCX from a customer-uploaded template after ISSO
  approval (see agency DOCX implementation record above); does not close **HS-002**

### Diff 5b — Agency-shaped DOCX (customer template)

**Delivered.**

After ISSO approval, the ISSO uploads a customer-provided agency `.docx` in
Review & export. Templates are stored content-addressed. Agents produce a bounded
mapping plan and reviewer exceptions; the server renders deterministic copy with a
draft notice. The ISSO previews, approves or rejects the render, and downloads
after approval. No worker, DOCX-only, no generic field-map UI.

Acceptance:

- Render creation requires an approved revision hash binding
- Mapping plan JSON uses closed canonical refs and control-table column maps only
- Blockers force **review_failed**; ISSO approval disabled while blockers remain
- Exact render cache for same workspace, revision, and template digest
- Approved mapping reuse for same workspace, template digest, and profile version
- **HS-002**, **HS-004**, and **HS-005** remain open for parity, production model
  calls, and malware scanning

### Diff 6 — Cutover and Deletion

**Cutover delivered. Destructive deletion is intentionally deferred until an
operator confirms that no live deployment or retained record needs the legacy
tables and migrations. Legacy product routes and screens are unreachable.**

Make the new workflow the portal default, remove unreachable old product code,
and reconcile docs, configuration, deployment assets, and tests.

Acceptance:

- The new workflow passes one end-to-end fixture from upload through export
- No portal or API route references retired workflow modules
- Reused platform tests still pass
- Deleted code is proven unreachable

## Cross-Environment Acceptance

The first release is portable when:

- Core tests run without provider-specific services
- Runtime validation fails fast on invalid deployment profiles
- Model, storage, identity, and scanner settings remain outside business logic
- A second synthetic agency bundle loads without code changes
- A second supported deployment profile starts without code changes
- No agency name appears in application branching logic

## Known Non-Blocking Inputs

These are supplied through local bundles or deployment configuration and do not
block core implementation:

- A production agency SSP template
- Agency-specific overlays and organization-defined parameters
- Final identity-provider group mappings
- Production model endpoint approvals
- Production scanner and storage locations

## Local Profile Management

The application does not retrieve profile updates from the internet.

Profiles are stored locally as immutable, versioned bundles. Each bundle contains:

- Profile identifier and version
- Source publication versions
- NIST OSCAL catalog and baseline files
- Agency overlays, tailoring, and parameters
- SSP template and mappings
- File checksums
- Bundle manifest

## Offline Profile Update Procedure

1. An authorized administrator downloads official source files on a connected workstation.
2. The administrator builds a profile bundle and records source versions and checksums.
3. The bundle is transferred through the agency-approved process.
4. The application validates its schema, manifest, checksums, and supported versions.
5. The administrator reviews a change summary.
6. The bundle is imported as inactive.
7. The administrator activates the new version.
8. Existing systems remain pinned to their original profile version until explicitly migrated.
9. Migration creates a new working revision and shows added, removed, or changed controls.
10. The previous profile version remains available for rollback and historical exports.

## Items That May Need Updates

- NIST SP 800-53 catalog
- NIST SP 800-53B baselines
- OSCAL schemas and content format
- Agency overlays and tailoring
- Organization-defined parameters
- Common-control and inheritance definitions
- Agency SSP template and field mappings

## Out of Scope

- Control assessment
- Security Assessment Plan
- Security Assessment Report
- POA&M management
- Authorization decision
- Continuous monitoring
- FedRAMP SSP profiles (retained FedRAMP analysis/export paths elsewhere are not
  this workspace)
- Qualified, toolchain-ready, or customer-ready OSCAL SSP products; OSCAL SSP
  conformance; privacy plan; and C-SCRM plan content (**HS-001** / **HS-002** and
  the security-only boundary remain governing). Draft OSCAL JSON from an approved
  snapshot is in scope as non-qualifying working material only.

## Reference Sources

- [NIST SP 800-53 controls and downloads](https://csrc.nist.gov/projects/risk-management/sp800-53-controls/downloads)
- [NIST SP 800-53B control baselines](https://csrc.nist.gov/pubs/sp/800/53/b/upd1/final)
- [NIST OSCAL project](https://github.com/usnistgov/OSCAL)
- [NIST OSCAL content releases](https://github.com/usnistgov/oscal-content/releases)
