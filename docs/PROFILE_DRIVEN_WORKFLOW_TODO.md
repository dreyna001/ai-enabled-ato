# Profile-Driven Workflow and Profile Currency TODO

> **Redirect — current promoted work:** This file is **later backlog** for
> profile registry, inheritance UI, qualified export mappings, and FedRAMP paths.
> **Active increments** with delivered status and narrow acceptance boundaries are
> tracked in [`docs/NEW_INTERNAL_SSP_WORKFLOW_PLAN.md`](NEW_INTERNAL_SSP_WORKFLOW_PLAN.md)
> (**Active increments (do now)**). Minimal ODP prompting, inherited/hybrid prompt
> rules, draft OSCAL JSON export, and profile-bound implementation-statement policy
> (bundle **1.2.0**) do **not** check off the broader items below.

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

## Security Categorization

- [ ] Add profile-defined FIPS 199 categorization fields.
- [ ] Map system information types to the agency-approved NIST SP 800-60 version.
- [x] Capture confidentiality, integrity, and availability impact separately.
- [ ] Require a rationale and evidence references for each impact value.
- [ ] Record information-type adjustments and adjustment rationale.
- [x] Compute the system high-water mark deterministically.
- [x] Treat agent output as a proposal until human confirmation.
- [ ] Mark categorization stale when data types, mission, or boundary change.
- [ ] Export the information-type mapping, C/I/A rationale, adjustments, and final category.

## Authorization Boundary and Diagrams

- [ ] Require the boundary narrative to reference diagram artifact, version, and page or image.
- [ ] Identify components inside, outside, and crossing the authorization boundary.
- [ ] Identify trust zones, trust boundaries, shared services, and external services.
- [ ] Extract labeled nodes, connections, directions, protocols, and data flows from diagrams.
- [ ] Reconcile diagram-derived facts with text evidence.
- [ ] Flag conflicts, unreadable diagrams, and low-confidence extraction for review.
- [ ] Preserve artifact hash and precise image-region or page locators.
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
- [ ] Generate follow-up questions for missing required fields.
- [ ] Reuse the register in SSP sections, controls, review, and exports.
- [ ] Mark affected content stale when an interconnection changes.

## Diagram and Vision Analysis

- [ ] Separate file ingestion, OCR, generic vision extraction, and semantic diagram analysis statuses.
- [ ] Add a provider-neutral diagram-analysis contract for OpenAI, Bedrock, and local models.
- [ ] Define diagram extraction requirements inside the pinned profile.
- [ ] Validate structured nodes, edges, trust boundaries, and data flows before persistence.
- [ ] Add rendered-page vision support for diagrams embedded in PDFs.
- [ ] Show analysis coverage and failures in the UI.
- [ ] Never label a diagram **Analyzed** when only file ingestion or OCR completed.
- [ ] Add synthetic diagram evaluation fixtures and expected graph assertions.
- [ ] Measure node, connection, direction, trust-boundary, and data-flow extraction accuracy.

## NIST SP 800-18 Rev. 2 SSP Coverage

- [x] Record final NIST SP 800-18 Rev. 2 source provenance and exact Table 1 element coverage in the profile.
- [ ] Pin the separate Security Plan Example Outline supplemental artifact.
- [x] Add missing Table 1 profile requirements:
  - Laws, regulations, and policies
  - SSP approval and authorization decision
  - Operational status
  - Complete responsible-personnel list
  - Control assessment status
  - Digital identity acceptance statement
  - SSP review and change history
- [ ] Add outline-only structures not represented by Table 1:
  - General referenced-artifact register
  - [x] Acronyms and glossary (profile item `ssp.acronyms_and_glossary`, **1.2.0**)
- [ ] Define each requirement's:
  - Stable requirement ID
  - Required or optional status
  - Structured data schema
  - Evidence and follow-up-question rules
  - UI editor
  - Export mapping
  - Migration behavior
- [x] Add deterministic exact-set coverage tests against final SP 800-18r2 Table 1.

### TODO: Profile-Defined Control Fields

- [x] Add a validated `control_response` schema to the profile bundle.
- [ ] Remove globally hardcoded control-field options.
- [x] Move globally hardcoded control **statement** agent instructions (statement
  content, ODP, inherited/hybrid, semantic review) into the pinned profile
  `implementation_statement_policy` (agency **1.2.0**).
- [x] Render control fields from the pinned profile.
- [x] Build LLM output contracts from the pinned profile.
- [x] Validate API writes against the pinned profile.
- [ ] Map profile values into DOCX, JSON, and qualified OSCAL export mappings (draft OSCAL JSON from approved snapshots is shipped; full profile-driven OSCAL field mapping remains open).

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
- [ ] Add FedRAMP profiles without adding customer-name or path-specific UI code.
- [ ] Add additional agency or authorization profiles only when authoritative
  source content, profile-owner decisions, and acceptance fixtures are supplied.
- [ ] Fail profile import when unsupported field combinations are declared.

## Profile Source of Truth

- [x] Store authoritative source content inside each local profile bundle.
- [x] Include control catalog, baselines, and SSP requirements with a validated control-response schema (agency 1.1.0 bundle).
- [ ] Include complete export mappings, templates, agent instructions, and retrieval content per profile.
- [x] Separate authority content from system implementation evidence.
- [ ] Prevent source documents from acting as model instructions.

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
4. [x] Compile a new immutable profile bundle.
5. [x] Validate schema, identifiers, baselines, references, and standard_coverage links.
6. [ ] Sign the bundle or attach an approved detached signature.
7. [ ] Transfer the bundle through the agency-approved process.
8. [ ] Verify checksum, signature, publisher allowlist, and bundle schema offline.
9. [x] Import the bundle as **Inactive**.
10. [x] Generate a deterministic diff from the currently active version.
11. [ ] Require qualified SME or profile-administrator review.
12. [x] Activate the new version explicitly.
13. [ ] Keep the previous version available for rollback and historical export.

## Profile Diff Requirements

- [x] Display added, removed, and changed controls.
- [x] Display SSP requirement (item) changes.
- [ ] Display baseline, overlay, parameter, control-field schema, export-template, common-control, and agent-content changes (includes `implementation_statement_policy` in offline `diff_profiles`; no profile-admin UI yet).
- [ ] Bind the diff to both bundle hashes.
- [ ] Store reviewer, decision, timestamp, and rationale.

## Workspace Migration Effects

- [x] Keep existing workspaces pinned until explicitly migrated.
- [ ] Preserve approved revisions with their original profile version.
- [ ] On migration:
  - Add new controls as `unaddressed`
  - Preserve removed controls in revision history
  - Mark changed control statements as `stale`
  - Revalidate statuses, designations, inheritance, and N/A rationales
  - Revalidate SSP fields and organization-defined parameters
  - Rebuild profile-derived UI selections
  - Rebuild LLM schemas and retrieval indexes
  - Reevaluate completion and approval eligibility
- [ ] Require ISSO review before a migrated revision can be approved.
- [ ] Support rollback to the prior pinned profile version.

## Agent Grounding

- [x] Load the exact pinned profile version for every generation or agent call.
- [ ] Send only relevant profile requirements for the current section or control.
- [ ] Include profile ID, version, and bundle hash in model-call metadata.
- [x] Require output control IDs and values to match the profile allowlists.
- [ ] Reject unsupported or stale profile references.
- [ ] Keep authority context distinct from system evidence citations.
- [ ] Regenerate affected outputs when the workspace profile changes.

## Profile Administration UI

- [ ] Add a local **Profiles** administration view.
- [ ] Display:
  - Profile ID and version
  - Active, inactive, archived, or retired state
  - Authority source versions and publication dates
  - Bundle hash and signature status
  - Qualification status
  - Import and activation history
  - Workspaces pinned to each version
  - Available migration diff
- [ ] Label currency as **Latest imported version**, not globally current.
- [ ] Add configurable age and review-due warnings.
- [ ] Do not perform direct internet retrieval from deployed environments.

## Validation and Tests

- [x] Reject invalid, unsigned, corrupted, or unapproved bundles.
- [ ] Reject duplicate profile IDs and versions with different bytes.
- [x] Test profile-defined backend validation and portal control selectors.
- [ ] Test FISMA and FedRAMP profiles with different field schemas.
- [x] Test deterministic profile diffs.
- [ ] Test workspace migration and rollback.
- [ ] Test historical export against the original pinned profile.
- [ ] Test that prompts use pinned profile content, not model memory.
- [ ] Analyze whether claim-level evidence verification materially improves SSP
  quality beyond the current evidence-link and human-review gates before adding
  new workflow, persistence, or UI.
- [ ] Qualify the production mapping, generation, and review models against
  expert-reviewed SSP fixtures with measured accuracy, failure thresholds,
  endpoint/model-version provenance, and regression gates.
- [ ] Test air-gapped import, activation, and recovery.

## Completion Criteria

- [ ] No authorization-path field values are globally hardcoded.
- [ ] FISMA/NIST Rev. 5 behavior is fully supplied by its profile bundle.
- [ ] A materially different profile changes UI, validation, agents, and exports without application code changes.
- [ ] Every model-generated control statement identifies the pinned profile version.
- [ ] Administrators can update, diff, qualify, activate, migrate, and roll back profiles offline.
