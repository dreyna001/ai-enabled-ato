# Profile-Driven Workflow and Profile Currency TODO

> **Redirect — current promoted work:** This file is **later backlog** for
> profile registry, inheritance UI, qualified export mappings, and FedRAMP paths.
> **Active increments** with delivered status and narrow acceptance boundaries are
> tracked in [`docs/NEW_INTERNAL_SSP_WORKFLOW_PLAN.md`](NEW_INTERNAL_SSP_WORKFLOW_PLAN.md)
> (**Active increments (do now)**). Minimal ODP prompting, inherited/hybrid prompt
> rules, draft OSCAL JSON export, and profile-bound implementation-statement policy
> (bundle **1.2.0**) do **not** check off the broader items below.

**Last audited:** 2026-08-13 against repository state on `main`.

## Current state summary

**Shipped (agency FISMA profile 1.2.0):**

- Internal SSP workflow cut over; Increments A–D delivered (see NEW_INTERNAL plan).
- Profile bundle drives control enums, SSP item constraints, generation/patch
  contracts, and `implementation_statement_policy`.
- Basic **FIPS 199 categorization**: separate C/I/A, high-water mark, agent proposal
  with human confirm, approval gate, portal panel, and partial DOCX export.
- Draft OSCAL JSON export from approved snapshots; agency DOCX render pipeline
  (HS-002 still open for customer parity).
- Offline profile compile, validate, import (inactive), activate, deterministic
  diff, and migrate-profile API scaffolding.
- Screenshot vision fact extraction with image-region locators (not authorization
  boundary or diagram graph analysis).

**Still open (needs code unless noted):**

- SP 800-60 information-type mapping, categorization staleness, full categorization
  export, and profile/runtime alignment for per-axis rationale fields.
- Authorization boundary diagram analysis, structured interconnection register,
  semantic diagram pipeline, profile admin UI, bundle signing, full migration
  semantics, FedRAMP SSP profiles, and profile-driven export mappings.
- Hard stops **HS-001** through **HS-009** (mostly customer/review gates, not
  greenfield product features).

## Objective

- Make authorization-path behavior profile-driven.
- Keep authoritative profile content local, immutable, versioned, and updateable.
- Ground agents in the pinned local profile.
- Never rely on LLM training knowledge for current control requirements.

## Initial Profile

**Profile:** `agency-fisma-nist-sp800-53-rev5` (shipped built-in bundle version **1.2.0**)

**Delivered in 1.2.0 (agency only):** explicit `implementation_statement_policy`
in `ssp-requirements.json` (deterministic flags, agent instruction blocks, authority
refs); generation, patches, and approval honor profile flags; semantic quality
findings advisory only. **Delivered in 1.1.0 (agency only):** manifest records final NIST SP 800-18 Rev. 2 provenance (`doi.org/10.6028/NIST.SP.800-18r2`, version 2.0.0) plus NIST OSCAL content 1.5.0 / SP 800-53 5.2.0 baselines; **33** profile SSP items with **digital identity acceptance optional**; **21** exact Table 1 `standard_coverage` rows (19 `ssp_item`, 2 `controls`); profile-defined section constraints and control enums enforced in generation, patches, direct edits, and approval, with profile requiredness enforced in metrics; legacy **1.0.0** bundles remain loadable with defaults; import / activate / migrate-profile API scaffolding exists for future profiles (FedRAMP not supported). Minimal ODP token detection, placeholder rejection, inherited/hybrid prompt guidance, draft OSCAL JSON export, and profile-bound statement policy are shipped narrowly—see NEW_INTERNAL **Active increments**. **HS-001** and **HS-002** stay open—no authority qualification, agency template parity, qualified OSCAL SSP or conformance claims, privacy plan, or C-SCRM plan claims.

**Delivered in workflow (not bundle-version specific):** FIPS 199 categorization
UI and API (`SystemCategorizationPanel`, `POST .../categorization`,
`save_system_categorization`), agent categorization proposals until ISSO confirm,
and approval blocked until `system.categorization_status` is `confirmed`.

## Concrete code scope for the streamlined operator workflow

1. Surface the existing information-types field in the categorization panel.
2. Let the agent populate it from documents.
3. Clearly label agent suggestions.
4. Add `confirmed_system_context` to generation and editing requests.
5. Generate one full categorization narrative from the confirmed values.
6. Ensure every export includes it.
7. Add the simple foundational-change warning and reconfirmation requirement.

## Security Categorization

- [ ] Add profile-defined FIPS 199 categorization fields.
- [x] Map system information types to the agency-approved NIST SP 800-60 version.
- [x] Capture confidentiality, integrity, and availability impact separately.
- [x] Require a rationale and evidence references for each impact value (confirm gate + export block; profile schema fields still open).
- [x] Record information-type adjustments and adjustment rationale.
- [x] Compute the system high-water mark deterministically.
- [x] Treat agent output as a proposal until human confirmation.
- [x] Mark categorization stale when data types, mission, or boundary change.
- [x] Export the information-type mapping, C/I/A rationale, adjustments, and final category (profile 1.4.0 structured `system.data_types` register + export block).

## Authorization Boundary and Diagrams

- [ ] Require the boundary narrative to reference diagram artifact, version, and page or image.
- [ ] Identify components inside, outside, and crossing the authorization boundary.
- [ ] Identify trust zones, trust boundaries, shared services, and external services.
- [x] Extract labeled nodes, connections, directions, protocols, and data flows from diagrams (first slice: `POST /diagram-analysis` on PNG/JPEG/WebP/PDF).
- [x] Reconcile diagram-derived facts with text evidence (conflict list in proposal; ISSO confirms manually).
- [ ] Flag conflicts, unreadable diagrams, and low-confidence extraction for review.
- [ ] Preserve artifact hash and precise image-region or page locators.
  - **Partial:** screenshot vision extraction stores image-region locators in
    `vision.py` / `evidence.py`; not wired to authorization-boundary workflow.
- [ ] Allow manual correction of extracted diagram structure.

## Interconnection Register

- [ ] Add profile-defined interconnection fields:
  - Connected organization, system, or service
  - Inbound, outbound, or bidirectional direction
  - Data and information types exchanged
  - Interface and protocol
  - Connection owner
  - Authorization or agreement type
  - Agreement identifier, status, and expiration
  - Boundary-crossing protections
  - Evidence references
  - **Today:** profile item `system.interconnections` is a `string_list` only.
- [ ] Generate follow-up questions for missing required fields.
- [ ] Reuse the register in SSP sections, controls, review, and exports.
- [ ] Mark affected content stale when an interconnection changes.

## Diagram and Vision Analysis

- [ ] Separate file ingestion, OCR, generic vision extraction, and semantic diagram analysis statuses.
- [ ] Add a provider-neutral diagram-analysis contract for OpenAI, Bedrock, and local models.
- [ ] Define diagram extraction requirements inside the pinned profile.
- [ ] Validate structured nodes, edges, trust boundaries, and data flows before persistence.
- [x] Add rendered-page vision support for diagrams embedded in PDFs (diagram analysis renders PDF pages via pypdfium2).
- [ ] Show analysis coverage and failures in the UI.
- [ ] Never label a diagram **Analyzed** when only file ingestion or OCR completed.
- [ ] Add synthetic diagram evaluation fixtures and expected graph assertions.
- [ ] Measure node, connection, direction, trust-boundary, and data-flow extraction accuracy.

## NIST SP 800-18 Rev. 2 SSP Coverage (core)

**Core rule:** the built-in `agency-fisma-nist-sp800-53-rev5` profile covers **Table 1
only** (21 elements). Outline-only supplements and agency-specific extras belong in
**overlay profiles** — see [Outline supplements and overlay-only (nice-to-have, not core)](#outline-supplements-and-overlay-only-nice-to-have-not-core).

**What keeps the core SSP template up to date:** new immutable profile bundles
(`scripts/build_ssp_profile_bundle.py`), offline import / diff / activate / migrate
(see **Offline Profile Update Process** below). Built-in DOCX export follows Table 1
`standard_coverage` chapter order from the pinned profile — not a separate Example
Outline file.

- [x] Record final NIST SP 800-18 Rev. 2 source provenance and exact Table 1 element coverage in the profile.
- [x] Add missing Table 1 profile requirements:
  - Laws, regulations, and policies
  - SSP approval and authorization decision
  - Operational status
  - Complete responsible-personnel list
  - Control assessment status
  - Digital identity acceptance statement
  - SSP review and change history
- [x] Acronyms and glossary in core (product choice; outline-only in SP 800-18 but shipped as required profile item `ssp.acronyms_and_glossary`, **1.2.0**)
- [ ] Define each requirement's:
  - [x] Stable requirement ID
  - [x] Required or optional status
  - [x] Structured data schema
  - [ ] Evidence and follow-up-question rules (partial: `evidence_required_for_agent` and generation validation exist; no per-item follow-up rule config in profile)
  - [ ] UI editor (partial: generic section textarea in `SspDocumentPanel`; no typed editors for `string_list` or enum fields)
  - [ ] Export mapping (partial: SP 800-18 DOCX via `standard_coverage`; not a full per-profile export contract)
  - [ ] Migration behavior (partial: `migrate_workspace_profile` adds empty new items; no stale/revalidate semantics)
- [x] Add deterministic exact-set coverage tests against final SP 800-18r2 Table 1.

## Outline supplements and overlay-only (nice-to-have, not core)

**Not required** in the built-in agency FISMA profile. Document here for overlay
profiles, customer packs, or later optional bundles — do **not** gate core ISSO
approval or Table 1 completeness on these items.

- [ ] **General referenced-artifact register (B8).** Optional overlay SSP item
  (`ssp.referenced_artifacts`). Not a Table 1 element; agencies that want a formal
  register add it via overlay profile with `required: false` unless the customer
  explicitly requires it.

**Out of scope (do not implement):** pinning the separate NIST Security Plan
Example Outline supplemental artifact. Built-in export uses Table 1
`standard_coverage` from the profile bundle for repeatable DOCX structure; the
Example Outline file is not vendored and will not be added.

### TODO: Profile-Defined Control Fields

- [x] Add a validated `control_response` schema to the profile bundle.
- [ ] Remove globally hardcoded control-field options.
  - **Partial:** profile drives SSP workspace when bundle declares schema; fallbacks
    remain in `generation_contracts.py`, `profile_bundles.py` defaults, portal
    `DEFAULT_CONTROL_RESPONSE_OPTIONS`, and legacy package editor paths.
- [x] Move globally hardcoded control **statement** agent instructions (statement
  content, ODP, inherited/hybrid, semantic review) into the pinned profile
  `implementation_statement_policy` (agency **1.2.0**).
- [x] Render control fields from the pinned profile.
- [x] Build LLM output contracts from the pinned profile.
- [x] Validate API writes against the pinned profile.
- [ ] Map profile values into DOCX, JSON, and qualified OSCAL export mappings.
  - **Partial:** draft OSCAL JSON from approved snapshots is shipped; JSON/DOCX use
    snapshot shapes, not full profile-driven field mappings; qualified OSCAL remains
    out of scope (**HS-001**).

### FISMA/NIST Rev. 5 Control Schema

```yaml
control_response:
  implementation_status:
    required: true
    values:
      - implemented
      - partially_implemented
      - planned
      - not_implemented
      - not_applicable
      - unknown
  control_designation:
    required: true
    values:
      - system_specific
      - common
      - hybrid
      - unknown
  inheritance:
    enabled: true
    provider_required_when_inherited: true
    implementation_details_required: true
  not_applicable:
    rationale_required: true
```

**Note:** shipped bundle uses a single `responsibility` enum
(`system_specific`, `hybrid`, `inherited`, `unknown`), not separate
`control_designation` / provider / N/A rationale fields from this target schema.

### UI Changes

- [x] Replace implementation-status text entry with a profile-defined selection.
- [x] Replace responsibility/inheritance text entry with profile-defined fields.
- [ ] Separate control designation from inheritance.
- [ ] Add common-control provider selection.
- [ ] Add inherited implementation details.
- [ ] Require a rationale for `not_applicable`.
- [x] Reject values not allowed by the pinned profile.
- [ ] Add bulk review and confirmation.
- [ ] Mark incompatible values for review after profile migration.
  - **Partial:** out-of-profile values can remain visible in selects after migration;
    no dedicated review markers or profile-admin UI.

## Additional Authorization Paths

- [ ] Allow each profile to define different:
  - Field names
  - Allowed values
  - Required fields
  - Control applicability rules
  - Inheritance requirements
  - Organization-defined parameters
  - Validation rules
  - Approval rules
  - Export mappings
  - LLM structured-output contracts
  - **Partial:** agency bundle supports `control_response`, SSP items, and
    statement policy; full per-profile matrix not generalized.
- [ ] Add FedRAMP profiles without adding customer-name or path-specific UI code.
  - FedRAMP analysis profiles exist elsewhere; FedRAMP SSP workspace profiles are
    not supported in this workflow.
- [ ] Add additional agency or authorization profiles only when authoritative
  source content, profile-owner decisions, and acceptance fixtures are supplied.
  - **Partial:** import/activate scaffolding accepts new bundles; product boundary
    is agency-only today.
- [ ] Fail profile import when unsupported field combinations are declared.
  - **Partial:** schema and semantic validation on load; no SSP-specific unsupported
    combination gate beyond bundle validation.

## Profile Source of Truth

- [x] Store authoritative source content inside each local profile bundle.
- [x] Include control catalog, baselines, and SSP requirements with a validated control-response schema (agency 1.1.0 bundle).
- [ ] Include complete export mappings, templates, agent instructions, and retrieval content per profile.
  - **Partial:** `implementation_statement_policy` and control/SSP constraints are
    profile-owned; export mappings and retrieval content are not complete.
- [x] Separate authority content from system implementation evidence.
- [ ] Prevent source documents from acting as model instructions.
  - **Partial:** generation system prompt treats source text as data; no separate
    authority-context channel beyond profile policy blocks.

## Bundle Manifest

```yaml
profile_id: fisma-nist-sp800-53-rev5
profile_version: immutable-version
authority_sources:
  - publisher: authoritative-publisher
    document_id: document-identifier
    version: source-version
    publication_date: YYYY-MM-DD
    source_uri: recorded-source-location
    sha256: source-file-sha256
bundle_sha256: complete-bundle-sha256
created_at: timestamp
created_by: builder-identity
qualification_status: draft|qualified|retired
signature: detached-signature-reference
```

## Offline Profile Update Process

1. [ ] Monitor authoritative publishers outside the deployed application.
2. [ ] Acquire updated source material through an approved connected environment.
3. [ ] Record source version, publication date, source location, and checksum.
   - **Partial:** manifest `sources[]` and builder record version/reference;
     no in-app acquisition workflow.
4. [x] Compile a new immutable profile bundle.
5. [x] Validate schema, identifiers, baselines, references, and standard_coverage links.
6. [ ] Sign the bundle or attach an approved detached signature.
7. [ ] Transfer the bundle through the agency-approved process.
8. [ ] Verify checksum, signature, publisher allowlist, and bundle schema offline.
   - **Partial:** checksum and schema on import; no signature or publisher allowlist
     for SSP bundles yet.
9. [x] Import the bundle as **Inactive**.
10. [x] Generate a deterministic diff from the currently active version.
11. [ ] Require qualified SME or profile-administrator review.
    - **Partial:** activation requires platform admin; no SME qualification gate.
12. [x] Activate the new version explicitly.
13. [ ] Keep the previous version available for rollback and historical export.
    - **Partial:** prior versions remain in DB; historical export resolves revision
      profile; no profile rollback API.

## Profile Diff Requirements

- [x] Display added, removed, and changed controls.
- [x] Display SSP requirement (item) changes.
- [ ] Display baseline, overlay, parameter, control-field schema, export-template, common-control, and agent-content changes.
  - **Partial:** `implementation_statement_policy_changed` and source version changes
    in offline `diff_profiles`; no profile-admin UI yet.
- [ ] Bind the diff to both bundle hashes.
- [ ] Store reviewer, decision, timestamp, and rationale.

## Workspace Migration Effects

- [x] Keep existing workspaces pinned until explicitly migrated.
- [ ] Preserve approved revisions with their original profile version.
  - **Partial:** revisions store profile id/version; export resolves historical
    profile row when workspace profile differs.
- [ ] On migration:
  - [ ] Add new controls as `unaddressed` (partial: new controls get `ControlState.EMPTY`)
  - [ ] Preserve removed controls in revision history (partial: old revisions retain them; working revision drops removed controls)
  - [ ] Mark changed control statements as `stale`
  - [ ] Revalidate statuses, designations, inheritance, and N/A rationales
  - [ ] Revalidate SSP fields and organization-defined parameters
  - [ ] Rebuild profile-derived UI selections (partial: envelope reloads `control_response`)
  - [ ] Rebuild LLM schemas and retrieval indexes
  - [ ] Reevaluate completion and approval eligibility
- [ ] Require ISSO review before a migrated revision can be approved.
- [ ] Support rollback to the prior pinned profile version.
  - **Partial:** revision restore can repoint workspace to a historical profile row
    if still imported; no dedicated profile rollback endpoint.
  - **Tests:** migrate API route exists; no behavioral migration/rollback tests yet.

## Agent Grounding

- [x] Load the exact pinned profile version for every generation or agent call.
- [ ] Send only relevant profile requirements for the current section or control.
  - **Today:** every generation/patch prompt includes all SSP sections and all controls.
- [ ] Include profile ID, version, and bundle hash in model-call metadata.
  - **Partial:** present in prompt payload; audit metadata records `model_attempts` only.
- [x] Require output control IDs and values to match the profile allowlists.
- [ ] Reject unsupported or stale profile references.
  - **Partial:** bundle validation on import/load; export resolves historical profile;
    no general stale-profile rejection on agent calls.
- [ ] Keep authority context distinct from system evidence citations.
  - **Partial:** system prompt and `supporting_fact_ids` exist; no separate authority channel.
- [ ] Regenerate affected outputs when the workspace profile changes.

## Profile Administration UI

- [ ] Add a local **Profiles** administration view.
  - Mockup only in `docs/theme-mockups/internal-ssp-workflow-ui.html`.
- [ ] Display:
  - Profile ID and version
  - Active, inactive, archived, or retired state
  - Authority source versions and publication dates
  - Bundle hash and signature status
  - Qualification status
  - Import and activation history
  - Workspaces pinned to each version
  - Available migration diff
  - **Partial:** API `GET /ssp-profiles` returns id, version, status, bundle hash,
    import/activation timestamps; portal uses list only for workspace creation.
- [ ] Label currency as **Latest imported version**, not globally current.
- [ ] Add configurable age and review-due warnings.
- [x] Do not perform direct internet retrieval from deployed environments.

## Validation and Tests

- [ ] Reject invalid, unsigned, corrupted, or unapproved bundles.
  - **Partial:** corrupt/invalid rejected via checksum and schema validation; no
    signature or qualification gate on SSP import.
- [ ] Reject duplicate profile IDs and versions with different bytes.
  - **Partial:** duplicate `(profile_key, version)` blocked on import.
- [x] Test profile-defined backend validation and portal control selectors.
- [ ] Test FISMA and FedRAMP profiles with different field schemas.
  - FedRAMP analysis profiles tested; no FedRAMP SSP workspace profile bundles.
- [x] Test deterministic profile diffs.
- [ ] Test workspace migration and rollback.
- [ ] Test historical export against the original pinned profile.
  - **Partial:** export code resolves historical profile; no dedicated test.
- [ ] Test that prompts use pinned profile content, not model memory.
- [ ] Analyze whether claim-level evidence verification materially improves SSP
  quality beyond the current evidence-link and human-review gates before adding
  new workflow, persistence, or UI.
- [ ] Qualify the production mapping, generation, and review models against
  expert-reviewed SSP fixtures with measured accuracy, failure thresholds,
  endpoint/model-version provenance, and regression gates.
- [ ] Test air-gapped import, activation, and recovery.
  - **Partial:** release packaging tests and WSL activate script; no full air-gap E2E.

## Completion Criteria

- [ ] No authorization-path field values are globally hardcoded.
- [ ] FISMA/NIST Rev. 5 behavior is fully supplied by its profile bundle.
  - **Partial:** agency 1.2.0 bundle drives most behavior; defaults/fallbacks and
    export mappings remain partly code-owned.
- [ ] A materially different profile changes UI, validation, agents, and exports without application code changes.
- [ ] Every model-generated control statement identifies the pinned profile version.
  - **Partial:** profile metadata in prompts and revision facts; statements do not
    embed profile version text.
- [ ] Administrators can update, diff, qualify, activate, migrate, and roll back profiles offline.
  - **Partial:** import, activate, diff, migrate API and operator scripts exist;
    signing, qualification workflow, admin UI, and rollback remain open.
