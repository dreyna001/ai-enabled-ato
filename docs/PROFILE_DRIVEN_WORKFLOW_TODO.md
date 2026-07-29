# Profile-Driven Workflow and Profile Currency TODO

## Objective

- Make authorization-path behavior profile-driven.
- Keep authoritative profile content local, immutable, versioned, and updateable.
- Ground agents in the pinned local profile.
- Never rely on LLM training knowledge for current control requirements.

## Initial Profile

**Profile:** `fisma-nist-sp800-53-rev5`

## Security Categorization

- [ ] Add profile-defined FIPS 199 categorization fields.
- [ ] Map system information types to the agency-approved NIST SP 800-60 version.
- [ ] Capture confidentiality, integrity, and availability impact separately.
- [ ] Require a rationale and evidence references for each impact value.
- [ ] Record information-type adjustments and adjustment rationale.
- [ ] Compute the system high-water mark deterministically.
- [ ] Treat agent output as a proposal until human confirmation.
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

- [ ] Pin the final NIST SP 800-18 Rev. 2 source and Security Plan Example Outline in the profile manifest.
- [ ] Add missing profile requirements:
  - Laws, regulations, and policies
  - SSP approval and authorization decision
  - Operational status
  - Complete responsible-personnel list
  - Control assessment status
  - Digital identity acceptance statement
  - Referenced-artifact register
  - Acronyms and glossary
  - SSP review and change history
- [ ] Define each requirement's:
  - Stable requirement ID
  - Required or optional status
  - Structured data schema
  - Evidence and follow-up-question rules
  - UI editor
  - Export mapping
  - Migration behavior
- [ ] Add deterministic coverage tests against the pinned SSP outline.

### TODO: Profile-Defined Control Fields

- [ ] Add a validated `control_response` schema to the profile bundle.
- [ ] Remove globally hardcoded control-field options.
- [ ] Render control fields from the pinned profile.
- [ ] Build LLM output contracts from the pinned profile.
- [ ] Validate API writes against the pinned profile.
- [ ] Map profile values into DOCX, JSON, and future OSCAL exports.

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

- [ ] Replace implementation-status text entry with a profile-defined selection.
- [ ] Replace responsibility/inheritance text entry with profile-defined fields.
- [ ] Separate control designation from inheritance.
- [ ] Add common-control provider selection.
- [ ] Add inherited implementation details.
- [ ] Require a rationale for `not_applicable`.
- [ ] Reject values not allowed by the pinned profile.
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
- [ ] Fail profile import when unsupported field combinations are declared.

## Profile Source of Truth

- [ ] Store authoritative source content inside each local profile bundle.
- [ ] Include:
  - Control catalog
  - Baselines
  - Overlays and tailoring
  - Organization-defined parameters
  - Common-control definitions
  - SSP requirements
  - Control-response schema
  - Export mappings and templates
  - Agent instructions and retrieval content
- [ ] Separate authority content from system implementation evidence.
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
4. [ ] Compile a new immutable profile bundle.
5. [ ] Validate schema, identifiers, baselines, references, and export mappings.
6. [ ] Sign the bundle or attach an approved detached signature.
7. [ ] Transfer the bundle through the agency-approved process.
8. [ ] Verify checksum, signature, publisher allowlist, and bundle schema offline.
9. [ ] Import the bundle as **Inactive**.
10. [ ] Generate a deterministic diff from the currently active version.
11. [ ] Require qualified SME or profile-administrator review.
12. [ ] Activate the new version explicitly.
13. [ ] Keep the previous version available for rollback and historical export.

## Profile Diff Requirements

- [ ] Display:
  - Added, removed, and changed controls
  - Baseline changes
  - Overlay and tailoring changes
  - Parameter changes
  - Control-field schema changes
  - SSP requirement changes
  - Common-control changes
  - Export-template and mapping changes
  - Agent instruction and retrieval-content changes
- [ ] Bind the diff to both bundle hashes.
- [ ] Store reviewer, decision, timestamp, and rationale.

## Workspace Migration Effects

- [ ] Keep existing workspaces pinned until explicitly migrated.
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

- [ ] Load the exact pinned profile version for every generation or agent call.
- [ ] Send only relevant profile requirements for the current section or control.
- [ ] Include profile ID, version, and bundle hash in model-call metadata.
- [ ] Require output control IDs and values to match the profile allowlists.
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

- [ ] Reject invalid, unsigned, corrupted, or unapproved bundles.
- [ ] Reject duplicate profile IDs and versions with different bytes.
- [ ] Test profile-defined UI fields and backend validation.
- [ ] Test FISMA and FedRAMP profiles with different field schemas.
- [ ] Test deterministic profile diffs.
- [ ] Test workspace migration and rollback.
- [ ] Test historical export against the original pinned profile.
- [ ] Test that prompts use pinned profile content, not model memory.
- [ ] Test air-gapped import, activation, and recovery.

## Completion Criteria

- [ ] No authorization-path field values are globally hardcoded.
- [ ] FISMA/NIST Rev. 5 behavior is fully supplied by its profile bundle.
- [ ] A materially different profile changes UI, validation, agents, and exports without application code changes.
- [ ] Every model-generated control statement identifies the pinned profile version.
- [ ] Administrators can update, diff, qualify, activate, migrate, and roll back profiles offline.
