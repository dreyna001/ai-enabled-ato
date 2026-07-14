# ATO Evidence Analysis Portal Technical Specification

**Status:** Normative product and implementation contract  
**Version:** 2.1.0  
**Effective date:** 2026-07-10  
**Repository:** `ai-enabled-ato`

This document is the single source of truth for product scope, domain contracts, workflows, security boundaries, implementation order, and acceptance gates.

Where another repository document conflicts with this specification, this specification wins. The historical Block 1 developer CLI has been retired; `ato_service` and the frozen contracts are the active implementation path.

## 1. Normative language and source precedence

`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are normative.

Source precedence:

1. Official FedRAMP, NIST, and customer agency requirements pinned in the authority manifest.
2. This specification.
3. Machine-readable internal schemas, OpenAPI, traceability, and hard-stop registers generated from or reviewed against this specification.
4. Versioned runtime/operator contracts and deployment assets, including `docs/CONFIGURATION.md`, `docs/OPERATIONS_AND_RECOVERY.md`, `deployment/README.md`, and `deployment/`.
5. Supporting product and demo documents.

Implementation MUST stop rather than guess when an external authority or customer input listed in Section 29 is unavailable.

## 2. Product definition

The ATO Evidence Analysis Portal is a private, single-customer application that:

1. Ingests one bounded authorization evidence snapshot at a time.
2. Preserves source files and field-level provenance.
3. Performs deterministic validation and readiness checks.
4. Uses bounded LLM steps for evidence comparison, explanation, and draft prose.
5. Produces reviewable draft package materials and readiness findings.
6. Requires human disposition and separate approval before export.
7. Leaves official records, certification, authorization, risk acceptance, and assessor decisions in authoritative customer and government processes.

The product is an analysis and package-preparation layer. It is not a GRC system of record, evidence collector, scanner, trust center, FedRAMP submission service, workflow engine, or authorization authority.

## 3. Locked scope

### 3.1 In scope

| Profile ID | Scope | Priority |
| --- | --- | --- |
| `fedramp_20x_program` | FedRAMP 20x Program Certification package preparation and readiness | Primary |
| `fedramp_rev5_transition` | Read-only import and transition analysis for existing Rev. 5 packages | Secondary |
| `fisma_agency_security` | Security-only agency FISMA package analysis using customer template packs | Secondary |

FedRAMP 20x Class C is the first qualified certification class. Class B reuses the engine and official schemas but has a separate applicability catalog and qualification fixture set. Class B is not implemented by merely reducing a control count.

### 3.2 Explicitly out of scope

- FedRAMP 20x Agency Certification path in v1
- FedRAMP Class D
- DoD RMF, eMASS, CCRI, and IC workflows
- Classified data processing
- Privacy controls, privacy plans, and privacy assessments
- Official AO, SCA, 3PAO, assessor, or FedRAMP decisions or signatures
- Live cloud, KSI, CI/CD, scanner, or GRC collection
- Trust-center hosting
- Quarterly review hosting
- FedRAMP Marketplace or PMO submission
- Bidirectional synchronization
- Scan or test execution
- Attestation signing
- AI-generated architecture diagrams
- Multi-customer SaaS or shared-host tenancy

Agency FISMA outputs MUST state that privacy artifacts are external and were not assessed.

### 3.3 One customer per installation

One installation serves one customer enterprise. It MAY contain many systems, package revisions, runs, and users. It MUST NOT contain data for unrelated customer enterprises and MUST NOT introduce a `tenant_id` abstraction.

## 4. Non-negotiable product posture

| Rule | Normative requirement |
| --- | --- |
| Assistive | The product MUST NOT grant or deny authorization, accept risk, or declare official compliance. |
| Evidence-bound | Package facts MUST resolve to supplied evidence. Missing facts MUST remain `unknown` or `TBD - input missing`. |
| Draft-only | AI-generated content MUST remain draft until reviewed. Approval permits export; it does not make content official. |
| Deterministic gates | Security policy, schema, applicability, citation, completeness, status ceilings, and export eligibility MUST be enforced in code. |
| No invention | The model MUST NOT invent evidence, architecture, settings, incidents, vulnerabilities, agencies, owners, dates, assessor work, or official status. |
| Human accountability | Only a human MAY confirm a weakness, disposition a finding, or approve an export. |
| Schema purity | Official FedRAMP JSON and OSCAL payloads MUST remain valid official-schema documents. Product metadata belongs in a sidecar manifest unless an official extension point exists. |

Portal summary and matrix views MUST display:

```text
Draft analysis readiness - not official status in GRC, FedRAMP, or an agency authorization process.
```

Paired human-readable reports and export manifests MUST include:

```text
AI Disclosure: This report was produced with machine assistance. All findings,
summaries, and status labels are draft inference bound to the evidence provided
in the package. They do not constitute an official compliance determination,
risk acceptance, certification, or authorization decision. A qualified human
reviewer must review and approve the content before use in an authoritative
government or customer process.
```

The disclosure MUST NOT be injected into an official machine-readable payload if doing so violates its schema.

## 5. Authoritative references

### 5.1 Initial pinned authorities

The first implementation qualification MUST use:

| Authority | Initial pin |
| --- | --- |
| FedRAMP Consolidated Rules for 2026 | Reviewed snapshot as of 2026-07-10 |
| FedRAMP Certification Package Overview schema | `fedramp-certification-package-overview-schema-2026-06-24.json` |
| FedRAMP Security Decision Record schema | `fedramp-security-decision-record-schema-2026-06-24.json` |
| FedRAMP Ongoing Certification Report schema | `fedramp-ongoing-certification-report-schema-2026-06-24.json` |
| NIST SP 800-53 / 53A / 53B | Release 5.2.0 |
| NIST OSCAL | 1.2.2, subject to customer/GRC compatibility qualification |

The implementation MUST create a versioned authority manifest containing, for every vendored authority:

```text
authority_id
source_url
source_version_or_date
retrieved_at_utc
sha256
effective_date
review_status
```

Production MUST NOT fetch or silently update authority content at runtime. An authority update requires review, regression tests, evaluation where applicable, and a release note.

### 5.2 Two validation layers

Official artifact validation has two independent gates:

1. Syntax/schema validation against the pinned official schema.
2. Semantic/applicability validation against the pinned rule catalog for profile, path, class, effective date, and audience.

Passing JSON Schema alone MUST NOT be reported as a complete package.

## 6. FedRAMP 20x Program contract

### 6.1 Product claim

For `fedramp_20x_program`, the product prepares and evaluates a complete draft package from customer-supplied information. It does not perform provider operational obligations.

### 6.2 Required package materials

| Material | Product behavior | Ownership boundary |
| --- | --- | --- |
| Certification Package Overview (CPO) | Validate and draft paired official JSON and human-readable content | Provider-owned fields may be drafted; assessor summary is import-only |
| Security Decision Record (SDR) | Validate and draft rule/KSI records in official JSON plus human-readable form | Provider implementation may be drafted; independent verification/validation is import-only |
| Ongoing Certification Report (OCR) | Validate and draft a real or example initial OCR and later quarterly reports | Incidents, accepted vulnerabilities, agencies, and attestations require explicit input |
| Secure Configuration Guide (SCG) | Ingest or draft provider-owned guidance; validate required sections and CPO reference | Product settings and secure defaults MUST NOT be invented |
| Independent assessment material | Ingest assessor identity, summary, verification, validation, and comments | AI MUST NOT generate or alter assessor-owned conclusions |
| KSI method and metric material | Ingest methods, tests, evidence, validation, and metric history | Product summarizes; it does not operate or schedule validation methods |

### 6.3 Class C readiness checks

The pinned rule catalog, not hard-coded prose, determines final applicability. The initial Class C semantic checks MUST include:

- At least two imported automated validation methods for each applicable KSI where required by the current rules.
- Required historical KSI status metrics, including at least the current six-month initial-certification history rule where applicable.
- Current CPO, SDR, OCR, SCG reference, and independent assessment inputs.
- Initial package freshness and independent assessment age under the pinned rules.
- Required package maintenance, next-report, and related dates.
- Explicit readiness blockers for operational requirements the product does not operate.

Missing required material MAY still be analyzed, but MUST appear as an export-readiness blocker. It MUST NOT be filled with fabricated content.

### 6.4 20x cadence

For the initial authority snapshot:

- Initial application package freshness is checked against the current seven-day rule.
- Class C independent assessment freshness is checked against the current three-month rule.
- Class C package maintenance is checked against the current two-week rule.
- OCR reporting is checked against the current three-month rule.
- Required KSI metric history is checked against the current rule.

These values MUST come from the authority catalog so a future reviewed update does not require prompt changes.

### 6.5 Auxiliary analysis

The product MAY produce an evidence sufficiency matrix, readiness checklist, KSI summary, internal weakness candidates, and package delta. These are product analysis artifacts, not substitutes for required official FedRAMP materials.

A product POA&M candidate is an optional GRC handoff aid. It MUST NOT be labeled as a required 20x package artifact unless the pinned authority explicitly requires it.

## 7. FedRAMP Rev. 5 transition contract

`fedramp_rev5_transition` supports:

- Read-only import of SSP, assessment plan, assessment results, and POA&M data.
- OSCAL JSON or XML when compatible with the pinned customer/FedRAMP toolchain.
- Evidence matrix and transition gap analysis.
- Comparison to the `fedramp_20x_program` package requirements.
- Draft export only after official schema/constraint validation and human approval.

It MUST NOT be offered as the default path for a new certification.

## 8. Agency FISMA security-only contract

`fisma_agency_security` supports agency-owned systems and security artifacts only.

The customer supplies:

- Agency name and system identifier.
- FIPS 199 impact level: `low`, `moderate`, or `high`.
- The authoritative tailored control list, organization-defined parameters, overlays, and inheritance decisions.
- Agency SSP, assessment, POA&M, and readiness templates or field maps.
- Agency freshness and approval policy.

The product MUST NOT select the official baseline, tailor controls, decide inheritance, or claim agency field parity without a qualified template pack.

Core security outputs:

- Security SSP section drafts
- SAR input pack, not an official signed SAR
- Human-confirmed POA&M draft candidates
- Security readiness summary
- Evidence sufficiency matrix
- Paired Markdown/JSON and OSCAL where the customer toolchain accepts the qualified OSCAL version

Every FISMA readiness output MUST include:

```text
Privacy artifacts and privacy-control assessment are outside this product scope and must be completed in the customer's authorization process.
```

## 9. Architecture and trust boundaries

```text
Customer IdP
  -> nginx TLS endpoint
       -> React portal
       -> FastAPI API
            -> Postgres metadata/state/audit index
            -> protected package filesystem
            -> Postgres-backed analyzer job queue
                 -> analyzer/extraction worker
                      -> configured text model endpoint
                      -> configured vision model endpoint, when enabled
```

### 9.1 Component ownership

| Component | Owns |
| --- | --- |
| nginx | TLS termination, security headers, SPA delivery, proxying, request-size enforcement |
| React portal | UI orchestration and explicit loading/empty/error/review states |
| FastAPI API | Authentication, object authorization, request validation, state transitions, streaming upload/download |
| Postgres | Metadata, identities/groups, ACLs, package/run/review/export state, job leases, audit event index |
| Filesystem | Content-addressed source blobs, immutable package manifests, run artifacts, exports, quarantine |
| Analyzer | Deterministic parsing/validation, bounded model calls, report generation |
| Model endpoint | Text or vision inference only; no direct database/filesystem/tool access |

Postgres is authoritative for lifecycle state. The filesystem is authoritative for immutable blob bytes. Neither is a complete source of truth alone.

## 10. Runtime and endpoint profiles

Runtime location and model trust are separate settings.

### 10.1 Runtime profiles

| Value | Purpose |
| --- | --- |
| `dev_local` | Developer workstation; repository-relative data; synthetic fixtures |
| `onprem_production` | Customer RHEL 9-compatible deployment; Postgres and protected local storage |

### 10.2 Endpoint profiles

| Value | Meaning |
| --- | --- |
| `mock` | Deterministic tests; no network |
| `external_openai` | External OpenAI-compatible endpoint |
| `internal_openai_compatible` | Customer-approved endpoint inside an approved boundary |

Text and vision endpoints are configured independently. They MAY use the same URL/model only after both capabilities pass qualification.

Model endpoint URLs and credentials are deployment configuration. They MUST NOT be editable through the portal or API.

### 10.3 Configuration and capability controls

- Non-secret runtime settings MUST live in the closed, schema-validated JSON document selected by `ATO_RUNTIME_CONFIG_PATH` or `--config`. `--config` takes precedence. A flat `config.env` or individual environment overrides for JSON settings MUST NOT become a second source of truth.
- Environment variables MAY bootstrap the config path, loopback bind address/port, and protected development or migration credential-file paths. Secret bytes MUST remain in systemd credentials or root-owned files and MUST NOT appear in runtime JSON.
- Optional functionality MUST use explicit capability flags with deterministic startup dependency validation. Flags default off unless the production schema explicitly requires a value.
- Capability bundles or presets MUST NOT be introduced until at least three implemented optional capabilities create a demonstrated operator need and their precedence, migration, and observability rules are approved.
- When API, worker, portal, timer, or other processes are added, each process MUST receive only the configuration and credentials it consumes.
- Runtime code, schemas, examples, systemd/nginx assets, install/smoke scripts, operator docs, traceability, and deployment-contract tests form one contract and MUST change together when shared values or behavior change.

## 11. Data labels and model routing

One `data_classification` string is insufficient. Every package revision has two required axes.

### 11.1 Data origin

```text
synthetic
redacted_nonproduction
customer_production
```

### 11.2 Sensitivity

```text
public
internal_unclassified
customer_sensitive
cui
classified
unknown
```

### 11.3 Routing defaults

| Origin / sensitivity | External endpoint | Approved internal endpoint |
| --- | --- | --- |
| `synthetic` / non-classified | Allowed | Allowed |
| `redacted_nonproduction` / non-CUI | Allowed only when endpoint policy explicitly permits | Allowed by policy |
| `customer_production` / any | Denied | Customer policy decides |
| any / `customer_sensitive` | Denied | Customer policy decides |
| any / `cui` | Denied | Only under an explicitly approved boundary |
| any / `classified` | Denied | Denied by product scope |
| any / `unknown` | Denied | Denied |

The uploader MUST declare both labels before file bytes are finalized. Deterministic indicator scanning MAY escalate or block but MUST NOT downgrade declared labels.

The effective labels are the most restrictive combination of declared labels and deterministic indicators. A package author MUST NOT override a denied route. There is no `ALLOW_SENSITIVE_OPENAI` production bypass.

Routing policy MUST run before the first normalization, vision, embedding, chat, or analysis model call. A blocked request records `policy_blocked` and `llm_call_count=0`.

## 12. Identifier, time, and schema conventions

- Server-managed object IDs are UUID v4.
- User display names and external IDs are separate fields and MUST NOT be used as filesystem paths.
- Timestamps are UTC RFC 3339 with `Z`.
- Date-only source dates use ISO `YYYY-MM-DD`.
- Every stored JSON object contains `schema_version`.
- Every immutable object contains or is referenced by a SHA-256 digest.
- Enums are closed; unknown values fail validation.
- All user-controlled strings have explicit length limits in machine schemas.

## 13. Core domain model

Machine-readable schemas created in phase P-1 MUST implement these contracts exactly.

### 13.1 System

```text
system_id: UUID
display_name: string
external_system_id: string | null
owner_group: string
viewer_groups: string[]
created_at: datetime
archived_at: datetime | null
```

### 13.2 PackageRevision

```text
package_revision_id: UUID
system_id: UUID
parent_revision_id: UUID | null
profile_id: fedramp_20x_program | fedramp_rev5_transition | fisma_agency_security
certification_class: B | C | null
data_origin: enum
sensitivity: enum
effective_data_labels: string[]
authority_manifest_id: string
content_manifest_sha256: sha256 | null
package_content_sha256: sha256 | null
system_context_snapshot_id: UUID | null
revision_version: positive integer (initial 1; incremented once per successful artifact addition and once per successful lifecycle transition)
status: uploading | scanning | extracting | awaiting_confirmation | ready | invalid | quarantined | archived
created_by: user_id
created_at: datetime
```

`revision_version` is the optimistic-concurrency token. The strong ETag is the quoted form `"v{revision_version}"`. `POST /api/v1/package-revisions/{id}/confirm` requires `If-Match`; a missing header returns HTTP 428 `if_match_required` and a stale header returns HTTP 412 `etag_mismatch`.

`content_manifest_sha256` is always present. It is `null` only while a revision is `uploading` or when an `archived` or `invalid` historical row never reached `scanning`. The `uploading -> scanning` transition requires a durable validated content manifest and atomically sets this SHA-256. A `ready` revision always has a non-null upload-manifest digest.

`package_content_sha256` is the canonical sealed package document digest. The field is always present on `PackageRevision` but remains `null` until package-level confirm writes `sealed_package_contents`. It is distinct from `content_manifest_sha256`, which binds uploaded source artifacts only. Ready revisions created through the package-editor draft path have non-null `package_content_sha256` and `system_context_snapshot_id`. Legacy proposal-gated confirm without a draft row may leave both fields null until a later reconciliation migration.

`system_context_snapshot_id` references an immutable versioned system-context row when sealed content exists. It remains `null` until confirm selects or creates the snapshot on the package-editor draft path.

A ready revision is immutable. Any source, canonical fact, profile, label, or link change creates a child revision.

### 13.2.1 SystemContextSnapshot

```text
system_context_snapshot_id: UUID
system_id: UUID
version: positive integer (unique per system)
content_sha256: sha256
document: JSON object
created_by: user_id
created_at: datetime
```

System-context snapshots are insert-only. Later edits create a new version; ready package revisions reference the snapshot captured at confirm.

### 13.2.2 PackageRevisionDraft

```text
package_revision_id: UUID (primary key and foreign key; one draft per revision)
document_schema_version: semver string
document: package-draft-document JSON object
field_provenance: JSON object mapping canonical JSON pointers to source metadata
updated_by: user_id
updated_at: datetime
```

The mutable draft row exists only while a revision is in `awaiting_confirmation` under the package editor workflow. Diff 1 publishes persistence only; intake and draft APIs write this row in later diffs.

### 13.2.3 SealedPackageContent

```text
package_revision_id: UUID (primary key and foreign key)
document_schema_version: semver string
document: package-draft-document JSON object
field_provenance: JSON object
content_sha256: sha256
system_context_snapshot_id: UUID
sealed_by: user_id
sealed_at: datetime
```

Sealed content is immutable. Ready revisions resolve canonical package bytes through this row once package-level confirm seals the current draft.

### 13.3 SourceArtifact

```text
artifact_id: UUID
package_revision_id: UUID
display_filename: string
storage_key: generated safe key
sha256: sha256
size_bytes: integer
declared_media_type: string
detected_media_type: string
artifact_kind: enum
malware_scan_status: pending | clean | infected | error
extraction_status: pending | succeeded | failed | not_applicable
source_date: date | null
uploaded_at: datetime
```

External URLs found in uploaded content are text facts only. The product MUST NOT fetch them automatically.

### 13.4 FactProposal and FactProvenance

Every extracted or normalized fact stores:

```text
fact_proposal_id: UUID
package_revision_id: UUID
json_pointer: string
proposed_value: JSON value
source_artifact_id: UUID
source_sha256: sha256
source_locator: page | sheet/cell | XML/JSON pointer | text offsets | image region
extraction_method: deterministic | text | vision | llm_normalize
model_step_id: UUID | null
review_status: pending | accepted | rejected | edited
reviewed_by: user_id | null
reviewed_at: datetime | null
```

An LLM-normalized fact MUST NOT enter a ready revision until accepted or edited by a human. Proposal decisions apply only while the revision is `awaiting_confirmation`; `confirm` then seals that revision as `ready`. A ready revision is never mutated.

### 13.5 EvidenceChunk and Citation

Extracted text is normalized to UTF-8 and split into immutable chunks of at most 6,000 characters with 500-character overlap.

```text
chunk_id = sha256(artifact_sha256 + normalized_start + normalized_end + text)

Citation:
  source_kind: evidence | authoritative_reference | derived_inference
  source_id: artifact_id | authority_id | run_step_id
  source_sha256: sha256
  chunk_id: string | null
  start_offset: integer | null
  end_offset: integer | null
  page_or_section: string | null
```

The application renders citation excerpts from stored offsets. The model MUST NOT supply the authoritative excerpt string.

An `evidence` citation supports a package fact. An `authoritative_reference` citation explains a requirement. A `derived_inference` citation identifies upstream analysis and MUST NOT be presented as direct evidence.

### 13.6 Path-specific assessment records

FISMA and Rev. 5 records use:

```text
control_id
control_title
authoritative_requirement_ref
organization_defined_parameters
implementation_statement
responsibility: customer | inherited | shared
linked_evidence_ids
```

FedRAMP 20x records preserve official CPO, SDR, OCR, FedRAMP rule, KSI, SCG-reference, and assessment fields. The official schemas are interchange contracts; an internal canonical record MUST retain all source values and back-references without loss.

Independent assessor fields are tagged `owner=assessor` and are never model-generated.

### 13.7 AnalysisRun

```text
run_id: UUID
package_revision_id: UUID
parent_run_id: UUID | null
run_type: full | targeted | deterministic_only
status: queued | running | succeeded | failed | cancelled | policy_blocked
requested_by: user_id
requested_at: datetime
started_at: datetime | null
completed_at: datetime | null
authority_manifest_id: string
analysis_profile_sha256: sha256
config_fingerprint: sha256
prompt_bundle_sha256: sha256
model_profile: string
artifact_manifest_sha256: sha256 | null
error_code: string | null
error_retryable: boolean | null
```

Run attempts and steps are child records. Retrying a transient operation creates a new attempt record without changing `run_id`. Analyzer jobs and `JobAttempt` rows queue and bound execution of one `(run_id, step_key)` unit; see Section 20.

### 13.8 MatrixRow

```text
assessment_item_type: nist_control | fedramp_rule | fedramp_ksi
assessment_item_id: string
model_proposed_status: supported | partial | unsupported | insufficient_evidence
system_status: supported | partial | unsupported | insufficient_evidence
finding_summary: string
gaps: string[]
assessor_questions: string[]
citations: Citation[]
context_complete: boolean
producing_run_id: UUID
source_run_id: UUID
```

Definitions:

| Status | Definition |
| --- | --- |
| `supported` | Reviewed context directly supports all material claim elements; required citations exist; no contradiction or incomplete context remains |
| `partial` | Some support exists but material elements are missing, stale, weak, or not fully reviewed |
| `unsupported` | Supplied evidence contradicts the claim or affirmatively shows the implementation is absent |
| `insufficient_evidence` | No usable evidence or context exists to decide |

Deterministic rules:

- No usable linked evidence forces `insufficient_evidence`.
- Incomplete evidence context MUST NOT produce `supported`.
- All relevant evidence stale MUST NOT produce `supported`.
- Broken references make the revision invalid; they do not become model findings.
- The system MAY make a status more conservative but MUST NOT make it more favorable than the model proposal.
- Matrix output MUST contain exactly one row per expected assessment item, no duplicates, no extras.
- One repair attempt is allowed for malformed model output. A second failure fails the run.

### 13.9 ReviewRevision and Disposition

Human review is separate from immutable model output.

```text
review_revision_id: UUID
run_id: UUID
version: integer
status: draft | submitted | superseded

Disposition:
  matrix_row_id: UUID
  decision: pending | accepted | edited | rejected | evidence_requested | weakness_confirmed
  edited_summary: string | null
  notes: string | null
  version: integer
  decided_by: user_id
  decided_at: datetime
```

Updates require `If-Match` against the current version.

Submitting a review requires an explicit `draft -> submitted` operation. Submission is allowed only when the review still has the supplied ETag, contains exactly one disposition for every matrix row, has no `pending` disposition, and passes referential-integrity checks. An export draft may be created only from a submitted review revision.

### 13.10 POA&M routing

| Condition | Action |
| --- | --- |
| `insufficient_evidence` | Create evidence request only |
| `partial` or `unsupported` | Create review candidate |
| `weakness_confirmed` | Permit POA&M draft candidate |

Owner, severity, due date, milestones, and risk MUST remain unknown until supplied or explicitly accepted by a human.

### 13.11 ExportDraft, Approval, and Export

```text
ExportDraft:
  export_draft_id: UUID
  review_revision_id: UUID
  payload_manifest_sha256: sha256
  destination_type: download
  status: draft | pending_approval | approved | rejected | expired | superseded | exported

Approval:
  approval_id: UUID
  export_draft_id: UUID
  payload_manifest_sha256: sha256
  submitted_by: user_id
  decided_by: user_id | null
  decision: pending | approved | rejected
  expires_at: datetime
  reason: string | null
```

The submitter MUST NOT approve the same export. Approval and export-draft expiry both use `APPROVAL_EXPIRY_DAYS` (normative default seven days per HS-010): `pending_approval -> expired` at `submitted_at + APPROVAL_EXPIRY_DAYS`, and `approved -> expired` at `decided_at + APPROVAL_EXPIRY_DAYS`. Any payload or review change supersedes approval. Every download is authorized and audited. V1 exports only a downloadable ZIP; it does not write to an external system.

### 13.12 AuditEvent

```text
audit_event_id: UUID
occurred_at: UTC datetime
actor_type: user | service
actor_id: string
action: enum
object_type: enum
object_id: string
outcome: succeeded | denied | failed
reason_code: string | null
metadata: redacted JSON
previous_event_hash: sha256
event_hash: HMAC-SHA-256
```

Audit events are insert-only for the application role. Raw prompts, model credentials, session tokens, and sensitive source text MUST NOT appear in operational logs or audit metadata.

## 14. Lifecycle and legal transitions

### 14.1 Package revision

```text
uploading -> scanning -> extracting -> awaiting_confirmation -> ready
```

Allowed terminal alternatives:

```text
uploading | scanning | extracting -> invalid
scanning -> quarantined
any non-terminal state -> archived
ready -> archived
```

A ready revision never returns to an editable state.

### 14.2 Run

```text
queued -> running -> succeeded
queued -> cancelled
running -> cancelled
queued | running -> policy_blocked
running -> failed
```

Invalid customer input affects the package revision. A transient model, database, storage, or network error affects the run and MUST NOT quarantine otherwise valid source material.

### 14.3 Review and export

```text
review draft -> review submitted
export draft -> pending_approval -> approved -> exported
pending_approval -> rejected
pending_approval | approved -> expired
any changed payload -> superseded
```

Illegal transitions return HTTP 409 and create a denied audit event.

## 15. Intake contract

### 15.1 Upload protocol

V1 supports:

1. Streaming multipart upload of individual files into a new PackageRevision.
2. A ZIP package uploaded as a stream and extracted under archive limits.
3. Developer-only file drop for the historical CLI.

The API MUST NOT buffer a complete file or package in memory.

The server generates storage keys. User filenames are display metadata only.

#### 15.1.1 P1.1 upload, finalize, and confirm boundaries

The System + PackageRevision HTTP slice defines intake boundaries only; it does not implement malware scanning, extraction, or synthetic worker processing. Customer extraction remains blocked while **HS-005** is open.

- **Upload** is legal only while the revision is `uploading`. Bytes become durable on disk before any database reference. Each successful upload increments `revision_version` once and initializes `malware_scan_status=pending` and `extraction_status=pending` on the new `SourceArtifact`. P1.1 accepts every published `artifact_kind` enum value but only `application/json` and `text/plain` declared media types pending scan/extract (**HS-005** blocks production extraction).
- **Finalize** is legal only while the revision is `uploading`. The server writes a durable validated content manifest, atomically sets `content_manifest_sha256`, performs `uploading -> scanning`, and increments `revision_version` once. It does not claim scan or extraction completion.
- **Confirm** is legal only while the revision is `awaiting_confirmation`, requires current `If-Match` (`"v{revision_version}"`), and when a `PackageRevisionDraft` exists seals canonical package bytes into `sealed_package_contents`, binds `package_content_sha256` and `system_context_snapshot_id`, and performs `awaiting_confirmation -> ready` without per-leaf `FactProposal` decisions. When no draft exists, confirm succeeds only when every `FactProposal` is non-`pending` (legacy compatibility). Confirm increments `revision_version` once.

Scanning, extraction, and synthetic processing are separate from the P1.1 HTTP amendment.

#### 15.1.2 P1.2 development synthetic JSON intake boundary

The P1.2 intake worker is a deliberately bounded development path. It runs only
with `runtime_profile=dev_local`, claims only revisions whose
`data_origin=synthetic`, and requires every source artifact to have both
declared and detected media type `application/json`. Non-synthetic,
non-JSON, and production-profile revisions are not eligible for this path.

Each claimed revision advances one transaction at a time under
`SELECT ... FOR UPDATE SKIP LOCKED`:

1. `scanning -> extracting` marks pending artifacts `malware_scan_status=clean`
   using a deterministic synthetic result. This is not a malware scanner,
   does not call a scanner dependency, and does not close **HS-005**.
2. `extracting -> awaiting_confirmation` parses durable UTF-8 JSON
   deterministically and creates pending `FactProposal` rows with RFC 6901
   pointers, matching JSON-pointer source locators,
   `extraction_method=deterministic`, and `model_step_id=null`.

The worker creates one proposal per addressable JSON leaf (including nested
empty objects or arrays), treats the source pointer as the canonical target
pointer for this known synthetic shape, and rejects duplicate target pointers.
Every transition increments `revision_version` exactly once and commits its
artifact/proposal side effects with an `outcome=succeeded` audit event. Invalid
JSON or duplicate canonical pointers performs the legal
`extracting -> invalid` transition without partial proposals. The worker makes
zero model calls.

The current CLI drains all eligible transitions and exits. It has no production
systemd unit, production scanner, customer extraction path, OIDC/session
dependency, or capability flag.

### 15.2 Allowed inputs

| Category | Formats |
| --- | --- |
| Canonical/manifest | JSON, UTF-8 text, JSON gzip |
| FedRAMP 20x | Official JSON package artifacts and supporting JSON |
| OSCAL | JSON and XML |
| Documents | PDF, DOCX, XLSX, TXT, Markdown |
| Scanner exports | Nessus XML, SARIF JSON, supported STIG JSON/XML |
| Architecture | PNG, JPEG, WebP, sanitized SVG, PDF pages, supported structured exports |
| Attestation exports | JSON, supported in-toto statement/bundle exports |

Executable files, macro-enabled Office files, arbitrary archives inside archives, and unsupported extensions are rejected.

### 15.3 Default limits

| Limit | Default |
| --- | --- |
| Package bytes | 2 GB |
| Single file bytes | 100 MB |
| Files per revision | 500 |
| Assessment items | 500 |
| Evidence items | 2,000 |
| PDF pages per file | 200 |
| Extracted text characters per file | 2,000,000 |
| Concurrent analysis runs | 2 |
| Model calls per run | 120 |

Limits are validated at startup and enforced in code. Limit failure is explicit; content is not silently truncated.

### 15.4 Extraction safety

Production extraction MUST:

- Run under a dedicated unprivileged service identity.
- Have no network access.
- Use systemd hardening, private temporary storage, no-new-privileges, memory/CPU/time limits, and a read-only host filesystem.
- Validate detected MIME against allowed type.
- Reject path traversal, symlinks, hard links, absolute paths, and duplicate normalized archive paths.
- Enforce compressed/uncompressed byte, member count, nesting, and decompression-ratio limits.
- Disable XML external entities and network resolution.
- Reject Office macros and never evaluate spreadsheet formulas.
- Strip SVG script and external references; never render raw SVG inline in the portal.
- Run a customer-approved malware scanner before extraction and fail closed if the scanner is unavailable.
- Quarantine infected content; invalid/unreadable content is marked invalid, not infected.

Text-only PDF extraction is deterministic when possible. OCR or image understanding uses the configured vision endpoint and the same routing policy. If vision is unavailable or blocked, extraction fails visibly or awaits a human-provided extract.

## 16. Normalization and preflight

Known formats use deterministic parsers. LLM normalization is allowed only for variable customer shapes after routing policy passes.

Normalization produces proposals and provenance; it does not directly create trusted canonical facts.

Preflight has two outputs:

```text
analysis_eligible: boolean
export_eligible: boolean
```

Analysis blockers:

- Failed authentication/authorization
- Invalid path/profile identity
- Package limit violation
- Malware or unsafe archive
- Blocked or unknown model-routing labels when model work is required
- Duplicate canonical IDs
- Broken references required to form assessment items
- Unconfirmed model-derived facts required by the requested analysis

Export blockers additionally include:

- Official schema failure
- Missing applicable mandatory authority fields
- Missing imported assessor-owned fields
- Missing required KSI methods or metric history
- Unresolved review dispositions required by the selected output
- Missing approval

Missing evidence, stale evidence, orphan evidence, and missing optional narrative are readiness findings, not automatic analysis blockers.

An informational readiness score is:

```text
passed_applicable_checks / total_applicable_checks
```

It MUST NOT determine eligibility. `PREFLIGHT_BLOCK_THRESHOLD` is deprecated.

## 17. Analysis workflow

```text
ready PackageRevision
  -> create run and immutable staging directory
  -> evaluate routing policy
  -> load pinned authority and analysis profile
  -> deterministic checks and assessment-item inventory
  -> deterministic evidence chunk selection
  -> bounded structured model calls
  -> schema validation; one repair attempt where allowed
  -> citation, completeness, and status-ceiling validation
  -> deterministic rollups and semantic readiness checks
  -> draft artifact generation
  -> official schema validation where applicable
  -> write immutable files and manifest
  -> commit succeeded state and audit events
```

No-evidence assessment items are assigned `insufficient_evidence` deterministically and do not require a model call.

### 17.1 Evidence context budgeting

- Model context size is explicit qualified configuration.
- Reserve configured output tokens plus 2,048 tokens for instructions and schema overhead.
- Include all linked evidence chunks when they fit.
- When they do not fit, rank chunks deterministically using package-scoped PostgreSQL full-text search and fill the remaining budget.
- Record every omitted artifact/chunk and set `context_complete=false`.
- A row with incomplete context MUST NOT be `supported`.
- Batches contain at most ten assessment items and MUST shrink to fit the configured context budget.
- If the minimum single-item fact bundle cannot fit, fail with `context_limit_exceeded`; do not truncate silently.

## 18. Model contract

### 18.1 Allowed model steps

| Step | Allowed purpose |
| --- | --- |
| `normalize_proposal` | Propose field mappings with source provenance |
| `sufficiency_matrix` | Compare one assessment item and claim to supplied evidence |
| `consistency_brief` | Identify cited contradictions among supplied artifacts |
| `narrative_flags` | Identify missing elements against a pinned requirement |
| `provider_draft` | Draft provider-owned prose from supplied facts |
| `ksi_summary` | Summarize imported KSI methods, evidence, and metrics |
| `ocr_summary` | Summarize explicitly supplied report-period facts |
| `package_chat` | Answer bounded questions over one authorized package revision |

### 18.2 Prohibited model work

The model MUST NOT:

- Authorize, certify, accept risk, or set official status.
- Generate independent assessor verification, validation, findings, or summary.
- Infer that no incident, vulnerability, customer agency, or significant change exists.
- Select or tailor an official baseline.
- Decide inheritance.
- Generate a POA&M weakness without human confirmation.
- Execute a query, tool, shell command, URL, or write action.
- Retrieve open-web content.

### 18.3 Request and response metadata

Each model step records:

```text
step_id
run_id
step_type
schema_id
prompt_version
prompt_sha256
fact_bundle_sha256
endpoint_profile
endpoint_host
model_requested
model_reported
temperature
input_limit
output_limit
timeout_seconds
attempt
provider_request_id
input_tokens
output_tokens
latency_ms
response_sha256
validation_outcome
```

Exact fact bundles and raw responses are protected run artifacts with package access controls. They are not operational log content.

### 18.4 Retry and repair

- Network errors, HTTP 429, and HTTP 5xx are retryable.
- Authentication, authorization, malformed request, policy, and other HTTP 4xx errors are not retryable.
- Retry uses exponential backoff with jitter and honors `Retry-After`, capped by configured attempts and run deadline.
- Schema repair is not a transport retry and is allowed once.
- Retrying MUST NOT duplicate a completed step or export.

### 18.5 Required configuration

Production startup fails on missing or invalid:

```text
TEXT_MODEL_ENDPOINT_URL
TEXT_MODEL_NAME
TEXT_MODEL_CONTEXT_TOKENS
TEXT_MODEL_MAX_OUTPUT_TOKENS
TEXT_MODEL_TIMEOUT_SECONDS
TEXT_MODEL_MAX_RETRIES
TEXT_MODEL_ENDPOINT_PROFILE

VISION_MODEL_ENABLED
VISION_MODEL_ENDPOINT_URL
VISION_MODEL_NAME
VISION_MODEL_CONTEXT_TOKENS
VISION_MODEL_ENDPOINT_PROFILE

MAX_MODEL_CALLS_PER_RUN
MAX_MODEL_INPUT_TOKENS_PER_RUN
MAX_MODEL_OUTPUT_TOKENS_PER_RUN

DATABASE_DSN_CREDENTIAL_REFERENCE
```

Startup MUST perform schema and deterministic semantic validation before serving. At minimum, it rejects unknown fields, prohibited production mock profiles, endpoint userinfo/query/fragment ambiguity, endpoint/allowlist mismatches, non-loopback cleartext model URLs, inconsistent token limits, missing enabled-capability dependencies, malformed credential references, and unsafe production paths. Validation does not replace DNS/egress enforcement or dependency readiness checks.

`DATABASE_DSN_CREDENTIAL_REFERENCE` is a protected deployment reference to a full SQLAlchemy PostgreSQL DSN supplied through systemd credentials or a root-owned file. The runtime JSON document contains only the reference; startup code resolves it without logging the value. No DSN, host, user, password, or credential-bearing environment variable may appear in repository files, examples, or operational logs.

Endpoint HTTPS is required except for an explicitly configured loopback internal endpoint. Redirects are forbidden. Host and port must match a deployment allowlist. Egress firewalling is a production requirement.

## 19. Search and package assistant

V1 search uses PostgreSQL full-text search over one PackageRevision. Cross-package search is out of scope.

Embedding retrieval is disabled until a separate embedding endpoint, data-routing policy, and evaluation are approved.

Package chat:

- Is scoped to one authorized PackageRevision and one selected run/review revision.
- Uses at most eight retrieved chunks per response unless the qualified context budget permits fewer.
- Requires typed citations for factual claims.
- Has configurable per-user rate, input-length, turn, and daily token limits.
- Stores messages under package retention and access policy.
- Has no tools and no write capability.
- Refuses authorization, certification, risk acceptance, official compliance, and unsupported out-of-package questions.

## 20. Job queue, durability, and recovery

V1 uses Postgres, not Redis or a generic message platform.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`.

Required job fields:

```text
job_id
run_id
step_key
step_idempotent
status
attempt_count
available_at
lease_owner
lease_expires_at
heartbeat_at
last_error_code
```

Closed job `status` values:

```text
available | leased | completed | failed | reconciliation_required
```

`JobAttempt` child records:

```text
attempt_id
job_id
run_id
step_key
attempt_number
status: active | succeeded | failed
lease_owner
started_at
completed_at
error_code
error_retryable
```

`attempt_count` is the durable count of `JobAttempt` rows. It starts at `0`, increments by `1` atomically with each new `JobAttempt` insert on claim, and is bounded at runtime by `TEXT_MODEL_MAX_RETRIES + 1` transport attempts per step. When the budget is exhausted, claim is illegal and the job reaches `failed` only through `leased -> failed` after the final attempt ends.

Uniqueness:

```text
one Job per (run_id, step_key)
one JobAttempt per (job_id, attempt_number)
one active JobAttempt per job_id (partial unique index)
one completed RunStep per (run_id, step_key)
```

Expired-lease recovery terminalizes the active `JobAttempt` as `failed` with `error_code=job_lease_lost`, `error_retryable=true`, and `completed_at` set when that row exists, and always sets `job.last_error_code=job_lease_lost` even when no active attempt row exists. Idempotent jobs with remaining transport budget return to `available`. Idempotent jobs at maximum transport budget transition `leased -> failed` and, when the run is `running`, `running -> failed` with `dependency_attempts_exhausted`; they are not requeued. Non-idempotent jobs, atomicity conflicts (expired lease with a completed `RunStep` or with the owning run still `queued`), and expired jobs with no active attempt transition to `reconciliation_required`. When the owning run is already `succeeded`, an outstanding expired job transitions to `reconciliation_required` without mutating the run; when the run is `failed`, `cancelled`, or `policy_blocked`, only the job transitions to `failed`. Schema repair neither creates a `JobAttempt` nor increments `attempt_count`.

The analyzer repository couples `queued -> running` on every claim while the run is still `queued`. The contract permits other claimers to omit that coupling only when explicitly documented outside the analyzer worker path.

Defaults:

- Heartbeat: 30 seconds
- Lease: 5 minutes
- Maximum active analyzer workers: 2
- Maximum transport attempts per model step: `TEXT_MODEL_MAX_RETRIES + 1`; default `TEXT_MODEL_MAX_RETRIES` is 2 (two retries after the first attempt)

Expired leases on idempotent steps are requeued only while `attempt_count < TEXT_MODEL_MAX_RETRIES + 1`. At maximum transport budget, expired idempotent leases transition the job to `failed` and a `running` run to `failed` with `dependency_attempts_exhausted`. Step completion has a unique constraint on `(run_id, step_key)`. Legal job-status transitions, lease operations, and attempt semantics are defined in `docs/contracts/LIFECYCLE_AND_ERRORS.md` Section 2.7.

### 20.1 Filesystem transaction rule

1. Stream source bytes to a generated temporary path while hashing.
2. `fsync`, validate, then atomically rename to content-addressed final storage.
3. Insert the database reference only after final storage exists.
4. Run outputs are written under a temporary run directory.
5. Each file is `fsync`ed and renamed; `artifact-manifest.json` is written last.
6. The run is marked `succeeded` only after manifest durability and database commit.
7. A reconciler removes unreferenced temporary objects and repairs detectable orphan references.

For package mutations covered by the P1.1 API slice, domain state (including `revision_version` when incremented), the idempotency outcome record (including `response_headers` for `ETag` replay), and the append-only audit event commit atomically. Audit HMAC credentials are referenced only through `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE` in runtime JSON and resolve at startup when configured; there is no environment-variable override. If authentication or audit dependencies required to append audit events are unavailable, the operation fails closed.

Reports are never overwritten. Re-analysis creates a new run.

### 20.2 Targeted re-analysis

Targeted re-analysis creates a child run. It MUST NOT modify the parent.

The child run materializes a complete effective matrix:

- Recomputed rows have `producing_run_id=child`.
- Reused rows preserve `source_run_id=parent` and are copied into the child artifact.
- Changed evidence automatically includes every linked assessment item.
- Material profile, authority, or canonical fact changes require a full run.

## 21. Authentication and authorization

### 21.1 Production authentication

Primary authentication is OIDC Authorization Code with PKCE.

- Tokens remain server-side.
- The browser receives a Secure, HttpOnly, SameSite=Lax session cookie.
- Sessions are stored in Postgres.
- Idle timeout: 30 minutes.
- Absolute timeout: 8 hours.
- Mutating requests require CSRF token and validated Origin.
- Signing keys are refreshed and issuer/audience/nonce/state are validated.

SAML is supported only through a customer-approved identity-aware proxy. nginx strips identity headers from external traffic and accepts proxy identity only from the configured trusted upstream with a shared authentication mechanism.

No production local passwords are supported. A one-time bootstrap admin is disabled after IdP configuration.

### 21.2 Roles

| Role | Permissions |
| --- | --- |
| `platform_admin` | Health, users/groups, retention, audit verification, deployment status; no package-content access by default |
| `package_owner` | Create revisions, upload, review, run analysis, submit export for owned packages |
| `control_owner` | View assigned assessment items, upload evidence, respond to evidence requests |
| `reviewer` | Read authorized package content and comment; cannot approve |
| `approver` | View exact submitted payload and approve/reject; cannot approve own submission |

Package access is group-based and default-deny. Every object lookup enforces package authorization, including downloads, chat, search, comments, run steps, and audit views.

Emergency package access by a platform admin requires a break-glass grant with reason, second-person approval where configured, expiry, and audit.

### 21.3 Pre-EP-06 package-route authentication boundary

Package routes require an injected authenticated principal with `actor_id` and `groups`. No request header may self-assert identity; external identity headers are ignored for authorization.

Until OIDC/session runtime exists, the production application returns HTTP 401 `authentication_required` on package routes. Contract and service tests may override or inject the authentication dependency.

Package mutations also require validated CSRF context. No insecure development bypass and no `LOCAL_PASSWORD_AUTH_ENABLED` path exist for these mutations.

Object authorization for the P1.1 slice is default-deny and package-scoped per
`docs/contracts/LIFECYCLE_AND_ERRORS.md` Section 2.1.3 and
`src/ato_service/route_role_matrix.py`. Until OIDC/session runtime exists,
the production application returns HTTP 401 `authentication_required` on
package routes. Contract and service tests may override or inject the
authentication dependency.

Package mutations also require validated CSRF context. No insecure development
bypass and no `LOCAL_PASSWORD_AUTH_ENABLED` path exist for these mutations.

Unauthorized object access returns HTTP 403 `authorization_denied` without
leaking sensitive object details. Guessed cross-system IDs return 403 when the
object exists but the caller lacks package access, and 404 when absent.

## 22. API contract

The implementation MUST publish OpenAPI 3.1 before API code.

All errors use `application/problem+json` with:

```text
type
title
status
detail
instance
error_code
request_id
field_errors[]
retryable
```

List APIs use opaque cursor pagination with default limit 50 and maximum 100.

`Idempotency-Key` is required on system creation, revision creation, file upload, revision finalization, revision confirmation, run start, review submission, export-draft creation, approval decision, and export delivery. Replay with the same key and normalized request digest returns the original outcome. Reuse with a different digest returns HTTP 409 `idempotency_key_conflict`. Idempotency records for these replay-safe operations are retained for 24 hours from first successful completion; this interval is a protocol invariant, not an operator setting. Expired records may be deleted; key reuse after expiry is allowed only after row removal and does not affect immutable artifacts.

`PackageRevision` resources expose strong ETag `"v{revision_version}"`. `POST /api/v1/package-revisions/{id}/confirm` requires `If-Match`; a missing header returns HTTP 428 `if_match_required` and a stale header returns HTTP 412 `etag_mismatch`. Other mutable review APIs use ETag and `If-Match`; stale writes return 412. Illegal state transitions return 409.

Minimum endpoint groups:

```text
POST/GET  /api/v1/systems
GET       /api/v1/systems/{system_id}

POST/GET  /api/v1/systems/{system_id}/package-revisions
POST      /api/v1/package-revisions/{id}/files
POST      /api/v1/package-revisions/{id}/finalize
GET       /api/v1/package-revisions/{id}/draft
PUT       /api/v1/package-revisions/{id}/draft
POST      /api/v1/package-revisions/{id}/confirm
GET       /api/v1/package-revisions/{id}
GET       /api/v1/package-revisions/{id}/proposals
POST      /api/v1/proposals/{id}/accept
POST      /api/v1/proposals/{id}/reject

POST/GET  /api/v1/package-revisions/{id}/runs
GET       /api/v1/runs/{run_id}
POST      /api/v1/runs/{run_id}/cancel
GET       /api/v1/runs/{run_id}/matrix
GET       /api/v1/runs/{run_id}/artifacts

POST      /api/v1/runs/{run_id}/review-revisions
POST      /api/v1/review-revisions/{id}/submit
PATCH     /api/v1/review-revisions/{id}/dispositions/{row_id}
POST/GET  /api/v1/review-revisions/{id}/comments

POST      /api/v1/review-revisions/{id}/export-drafts
POST      /api/v1/export-drafts/{id}/submit
POST      /api/v1/approvals/{id}/approve
POST      /api/v1/approvals/{id}/reject
GET       /api/v1/exports/{id}/download

GET       /api/v1/package-revisions/{id}/search
POST      /api/v1/package-revisions/{id}/chat

GET       /health/live
GET       /health/ready
```

## 23. Output contracts

### 23.1 Export bundle

Approved ZIP layout:

```text
manifest.json
README.txt
human/
machine/
provenance/
validation/
```

`manifest.json` contains:

```text
schema_version
export_id
profile_id
system_id
package_revision_id
run_id
review_revision_id
approval_id
created_at
ai_disclosure
authority_manifest_id
files[]: path, media_type, sha256, size_bytes, official_schema_id | null
```

ZIP paths are generated from allowlisted artifact IDs, not user filenames. The bundle MUST be reproducible from the same approved review revision, except for export timestamp and export ID.

### 23.2 FedRAMP 20x Program outputs

Required package-preparation outputs:

```text
machine/cpo.json
human/cpo.md
machine/sdr.json
human/sdr.md
machine/ocr.json
human/ocr.md
human/scg-readiness.md
machine/fedramp-readiness.json
human/fedramp-readiness.md
machine/assessment-matrix.json
human/assessment-matrix.md
```

Official JSON files validate against pinned official schemas. Missing assessor-owned or operational facts remain readiness blockers and are not invented.

Auxiliary outputs MAY include KSI summary, package delta, significant-change input brief, evidence requests, and human-confirmed internal POA&M candidates. They MUST be labeled product analysis, not required FedRAMP package material unless the authority catalog says otherwise.

### 23.3 FISMA security-only outputs

```text
human/ssp-security-draft.md
machine/ssp-security-draft.json
human/sar-input-pack.md
machine/sar-input-pack.json
human/poam-draft.md
machine/poam-draft.json
human/security-readiness.md
machine/security-readiness.json
human/assessment-matrix.md
machine/assessment-matrix.json
```

Qualified OSCAL variants MAY be added when the customer template/toolchain is provided and validation passes.

## 24. Portal behavior

Required screens:

- Systems and package revisions
- Upload and extraction status
- Normalization proposals
- Run summary and readiness
- FedRAMP rules/KSI or FISMA control matrix
- Evidence index and package-scoped search
- Architecture artifacts
- Draft package materials
- Review dispositions and comments
- Approval queue
- Export history
- Audit trail
- Package assistant

Every screen has explicit loading, empty, permission-denied, dependency-degraded, validation-failed, and retry states.

Markdown is sanitized; raw HTML is disabled. A restrictive Content Security Policy is required. Raw SVG is never rendered inline. Evidence downloads use safe content type and `Content-Disposition: attachment`.

## 25. Audit, retention, purge, and backup

### 25.1 Audit integrity

- Audit writes use an insert-only database role.
- Events form an HMAC-SHA-256 chain.
- The chain key is supplied through protected deployment credentials.
- A daily chain root and artifact-manifest index are copied to protected backup.
- An operator command and runbook verify event and artifact integrity.

### 25.2 Retention

Default retention is seven years for package revisions, run artifacts, review records, approvals, exports, chat, and audit events unless customer policy overrides it.

Each retained object supports `legal_hold=true`. Purge MUST:

1. Require platform-admin initiation and a second confirmation.
2. Refuse objects under legal hold.
3. Remove primary blobs, search indexes, derived chunks, prompts/responses, and exports.
4. Write a tombstone and non-sensitive audit event.
5. Allow encrypted backups to age out under the documented backup schedule.

Purge MUST NOT claim immediate removal from immutable backup media.

### 25.3 Backup and recovery

Targets:

```text
RTO: 4 hours
RPO: 1 hour
```

Meeting RPO requires:

- Postgres WAL archiving.
- At least hourly package-filesystem snapshots.
- Daily coordinated full backup.
- Encrypted off-host target.
- Customer-owned backup and encryption keys.
- 90-day online backup retention by default.
- Quarterly restore drill and integrity verification.

Daily-only backup is not sufficient for a one-hour RPO.

## 26. On-prem production contract

Target:

- RHEL 9-compatible x86_64 host
- SELinux enforcing
- Python 3.12
- PostgreSQL 16
- nginx
- systemd
- React/Vite static portal
- Single-node v1; high availability is not claimed

This list is the P7 target topology. The current repository ships an API-only deployment scaffold for the implemented `ato_service` health/runtime boundary. It does not claim a portal, analyzer worker, timer, model host, production identity, or completed RHEL validation. Those assets MUST NOT be added before their corresponding runtime and acceptance tests exist.

Target services:

```text
ato-api.service
ato-analyzer.service
nginx.service
postgresql.service
customer malware scanner
```

External inbound port: 443 only. API and Postgres listen on loopback or Unix sockets. Outbound access is allowlisted for IdP, model endpoint, and approved update/backup destinations.

Identities and paths:

```text
service user: ato
config: /etc/ato-analyzer/runtime-config.json
credentials: systemd credentials or root-owned files
application: /opt/ato-analyzer
data: /var/ato-packages
logs: journald
```

Config files are `root:ato` and mode `0640` or stricter. Secrets MUST NOT appear in environment examples, command lines, process listings, logs, or reports.

Application code and virtual environments are root-owned and service-readable. Writable access is limited to declared data/staging paths. Installers MUST preserve existing customer configuration and credentials, install placeholder proxy/config files inactive, and require explicit migration, start, and smoke actions. Each service unit loads only the credentials consumed by that implemented process.

Health:

- `/health/live` reports process liveness only.
- `/health/ready` verifies database, writable storage, authority manifest, job subsystem, and required configuration; it does not call the model.
- Dependency degradation is visible in metrics and operator UI.

Required metrics:

- Queue depth and oldest age
- Run/step duration and outcomes
- Model call count, latency, tokens, retry/failure count
- Upload/extraction/quarantine outcomes
- Storage bytes and disk watermarks
- Database connection and migration state
- Authentication/authorization denials
- Approval/export outcomes
- Backup and audit-verification status

At 80% data-volume use, warn. At 90%, reject new uploads and new runs while preserving reads and administrative recovery.

## 27. Scale and performance

V1 target:

| Metric | Target |
| --- | --- |
| Concurrent portal users | 50 |
| Active systems/packages | 100 |
| Concurrent analysis runs | 2 |
| Reference package | 150 assessment items, 500 evidence items |
| Maximum qualified FISMA package | 500 assessment items |
| Full reference analysis p95 | Under 45 minutes |
| Portal read API p95 | Under 500 ms excluding file transfer |

No hardware purchase or local-model sizing claim is made until a qualified benchmark runs on the intended model and host.

## 28. Verification and release gates

### 28.1 Deterministic tests on every change

- Schema positive and negative fixtures
- Boundary, missing, empty, duplicate, and malformed inputs
- Package limits and malicious archive/XML/Office/SVG fixtures
- Policy-before-model ordering with `llm_call_count=0`
- Legal/illegal state transitions
- Job lease expiry, retry, crash, replay, and partial-write recovery
- Exact assessment-item matrix coverage
- Citation hash and offset validation
- Status ceilings and POA&M routing
- RBAC matrix, object-level authorization, CSRF, self-approval denial
- Idempotency and optimistic-concurrency conflicts
- Official schema and semantic authority validation
- Backup/restore and audit-chain verification
- Portal XSS and unsafe-download regression tests
- Runtime/deployment contract synchronization across JSON schema/examples, startup validation, systemd/nginx, install/smoke scripts, operator docs, and `tests/test_deployment_contract.py`

### 28.2 AI qualification

Before any customer pilot:

| Metric | Gate |
| --- | --- |
| Expected row coverage | 100% |
| Citation locator validity | 100% |
| Critical false-supported cases | 0 |
| Supported precision on adjudicated holdout | At least 95% |
| Critical normalization fields | 100% precision and recall |
| Other normalization fields | At least 95% precision and recall |
| Assessor status exact agreement | At least 80% |
| Weighted status agreement | Cohen's weighted kappa at least 0.70 |
| Prompt-injection policy suite | 100% pass |

Minimum holdout:

- At least 100 assessment items per supported primary profile.
- At least three distinct synthetic or approved sanitized packages per profile.
- Two qualified SME labels with adjudication.
- A written label guide.
- No holdout examples used for prompt development.

Live model qualification is mandatory when the model snapshot, endpoint behavior, prompt, output schema, authority catalog, context-selection algorithm, or status policy changes. It produces an immutable evaluation record. Mocked tests remain the default PR path.

### 28.3 Security gate before real customer data

Required:

- Threat model reviewed.
- IdP integration and RBAC tests passed.
- Malware scanning operational.
- External endpoint policy approved.
- TLS, secrets, egress, SELinux, backup, restore, retention, and audit verification tested.
- No known critical or high vulnerabilities without documented acceptance by the customer authority.

## 29. Hard stops

| ID | Missing input or condition | Work that MUST stop |
| --- | --- | --- |
| HS-001 | Reviewed authority snapshot or digest mismatch | Authority-dependent implementation/release |
| HS-002 | Agency FISMA template pack | Claim of agency field parity or customer-ready FISMA export |
| HS-003 | IdP issuer/client/group map | Production identity deployment |
| HS-004 | Approved model endpoint data policy | Any real customer model call |
| HS-005 | Production malware scanner | Customer file extraction |
| HS-006 | SME label guide and adjudicated holdout | AI qualification or pilot claim |
| HS-007 | Named GRC API, field map, credentials, test tenant | Vendor writeback |
| HS-008 | Backup target and key ownership | Production readiness |
| HS-009 | Assessor-owned FedRAMP inputs | Claim of complete Class C package readiness |
| HS-010 | Customer retention/approval override | Use defaults; do not invent customer-specific policy |

Official FedRAMP schemas are the standard contract; customer-approved CPO/SDR templates are not required to implement the standard JSON outputs. Customer-specific renderings are optional template packs.

Open hard stops are recorded in `docs/requirements/hard-stops.yaml`. HS-001 blocks authority-dependent implementation and release only. P0 core safety work may proceed after the P-1 gate in `docs/P1_GATE_RECORD.md` without qualified authority review.

## 30. Implementation epics

| Epic | Goal | Primary acceptance |
| --- | --- | --- |
| `EP-00-contracts` | Publish machine and runtime/operator contracts and synchronize docs | P-1 gate recorded; schemas/OpenAPI/threat/eval/ops/config contracts published |
| `EP-01-core-safety` | Harden the `ato_service` safety foundation | Policy ordering, validated runtime config, limits, exact rows, immutable run artifacts, failure taxonomy |
| `EP-02-package-foundation` | Add versioned packages, provenance, Postgres jobs/state | FISMA fixture uploads, confirms, runs, retries, and replays without mutation |
| `EP-03-fedramp-20x` | Add Program Class C package model | CPO/SDR/OCR/SCG/KSI fixture passes official and semantic checks |
| `EP-04-secure-intake` | Add OSCAL/doc/scanner/diagram intake | Malicious fixtures fail closed; source provenance retained |
| `EP-05-draft-artifacts` | Generate FedRAMP and FISMA outputs | Paired outputs agree; assessor/unknown facts not invented |
| `EP-06-review-portal` | Add OIDC portal, review, approval, ZIP export | End-to-end auth/review/approval/export tests pass |
| `EP-07-advanced-analysis` | Add consistency, delta, KSI/OCR summaries, targeted runs, chat | AI qualification and refusal/injection tests pass |
| `EP-08-onprem-release` | Complete and operate the on-prem package on RHEL 9 | Deployment contracts plus live install, upgrade, rollback, failure, backup, restore, and smoke drills pass |

## 31. Build sequence

| Phase | Files/capability | Exit gate |
| --- | --- | --- |
| P-1 | Spec, internal JSON Schemas, authority manifest, OpenAPI, threat model, AI eval spec, operations/config contracts | `EP-00-contracts` complete; recorded in `docs/P1_GATE_RECORD.md` |
| P0 | `ato_service` safety foundation, validated runtime config, API-only deployment-contract baseline, and regressions | Deterministic foundation, runtime/deployment contract, policy/replay/crash/completeness tests pass |
| P1 | Postgres state, jobs, PackageRevision, provenance, immutable storage | One canonical FISMA package completes end to end |
| P2 | FedRAMP 20x Program Class C profile | Official Class C synthetic package validates and exposes missing obligations |
| P3 | Secure multi-file intake | PDF/DOCX/XLSX/OSCAL/scanner/diagram fixtures and malicious cases pass |
| P4 | Draft artifact generation | FedRAMP and FISMA paired outputs pass schema and provenance checks |
| P5 | Portal, OIDC, review, approval, ZIP export | Browser and API authorization/concurrency/replay tests pass |
| P6 | Advanced analysis and bounded assistant | AI qualification passes |
| P7 | Complete on-prem release | Static deployment contracts and live RHEL install/upgrade/rollback/restore drills pass |

Do not start a later phase while its required prior contract or gate is incomplete.

The runtime/deployment contract is cross-cutting after P0. Every later phase that adds a setting, capability, process, listener, writable path, credential, or operator action MUST update its runtime schema and semantic validation, redacted example, least-privilege process projection, deployment assets, operator docs, traceability, and deterministic tests in the same reviewable change. Contract tests never substitute for the live-host validation required at P7.

## 32. Requirements traceability

Every normative implementation requirement MUST be entered in `docs/requirements/traceability.yaml` during P-1 with:

```text
requirement_id
spec_section
requirement_text
epic
implementation_files
test_ids
verification_type
status
```

CI MUST fail when:

- A requirement has no implementation owner or verification.
- A referenced test does not exist.
- A supported profile, enum, state, artifact ID, runtime profile, or endpoint profile differs between schema, OpenAPI, config examples, and docs.
- A shared runtime path, port, credential identifier, capability flag, service behavior, or install/smoke action differs between code, deployment assets, examples, operator docs, and deployment-contract tests.

Minimum release-level requirements:

| ID | Requirement | Verification |
| --- | --- | --- |
| R-001 | Draft-only and no authorization decision | Reports, portal, refusal tests |
| R-002 | Single customer per installation | Architecture/schema review |
| R-003 | 20x Program Class C official package contract | Official schema + semantic E2E |
| R-004 | Security-only FISMA boundary | Fixture and disclosure test |
| R-005 | DoD/IC/classified rejection | Negative tests |
| R-006 | Policy before every model call | Routing-order tests |
| R-007 | Immutable package revisions and runs | Replay/mutation tests |
| R-008 | Field-level provenance | Proposal/confirmation tests |
| R-009 | Exact matrix completeness | Missing/duplicate/extra row tests |
| R-010 | Stable citation offsets | Hash/offset tests |
| R-011 | Official payload schema purity | FedRAMP/OSCAL validation |
| R-012 | Assessor-owned fields are import-only | AI contract tests |
| R-013 | Human-confirmed POA&M weakness | Routing tests |
| R-014 | OIDC and object-level authorization | API/E2E auth tests |
| R-015 | No self-approval; hash-bound export | Approval tests |
| R-016 | Idempotency and optimistic concurrency | API conflict tests |
| R-017 | Safe extraction | Malicious fixture suite |
| R-018 | Tamper-evident audit | Chain verification |
| R-019 | One-hour RPO/four-hour RTO | Restore drill |
| R-020 | AI qualification gates | Evaluation record |
| R-021 | Model endpoint configuration swap | Integration test |
| R-022 | RHEL 9 operations | API-only asset contracts now; live install/upgrade/rollback/backup/restore/smoke drills at P7 |
| R-023 | Runtime/deployment contract synchronization | Runtime-config and deployment-contract tests plus phase review |

## 33. Retired Block 1 developer CLI

The historical `ato_analysis` developer CLI and file-drop workflow have been retired. P0 and later phases build on `ato_service`, the published API contract, PostgreSQL state, workers, and the portal.

The durable safety concepts formerly exercised through Block 1 now live in `ato_service` and the frozen contracts:

- Required policy check before model normalization
- No production sensitive-data bypass
- Enforced configured limits
- Exact matrix completeness
- Stable citation validation
- Run-scoped immutable outputs
- Dependency failure separate from quarantine
- New schema/profile names through a documented migration

Historical Block 1 reports are not a compatibility target for the final product runtime.

## 34. Definition of implementation-ready

The plan is implementation-ready only when P-1 has:

1. Synchronized every active repository document.
2. Pinned and hashed official authority sources. Qualified human review is tracked separately by HS-001 and is not required to close P-1.
3. Published internal JSON Schemas.
4. Published OpenAPI 3.1.
5. Published legal state transitions and error taxonomy.
6. Published the threat model.
7. Published the AI label/evaluation guide.
8. Published the operations and recovery contract.
9. Populated traceability for all P0 requirements.
10. Resolved or recorded every hard stop.
11. Published the canonical runtime JSON, capability/safety-flag inventory, secret-reference boundary, and semantic startup-validation contract.
12. Published and contract-tested an API-only deployment scaffold without claiming live-host or full P7 completion.

When these criteria are met, record the outcome in `docs/P1_GATE_RECORD.md`. P0 core safety work may then proceed. Authority-dependent implementation and release remain blocked while HS-001 is open. Customer-specific hard stops remain scoped to the phases that need them.

Job, attempt, pending-approval expiry, disposition decision, and System + PackageRevision API contracts are published in `docs/contracts/LIFECYCLE_AND_ERRORS.md` and `docs/contracts/domain.schema.json`. The implemented portal-first slice now covers OIDC-backed server sessions, systems and revisions, synthetic JSON intake through confirmation, proposal review, and `deterministic_only` analysis runs with durable jobs, exact matrix persistence, artifact manifests, and zero model calls. Component A Diff 1 adds package-editor persistence (`system_context_snapshots`, `package_revision_drafts`, `sealed_package_contents`) and draft/sealed domain contracts without changing confirm behavior. Component A Diff 3 adds intake work lease persistence (`package_revision_intake_work`, `package_revision_intake_attempts`), finalize bootstrap of `malware_scan` work, the `intake_work.py` claim/heartbeat/complete/failure/recovery repository, unified `intake.py` scan/extract orchestration, and `dev_local` worker wiring through `ato-intake-worker` and the WSL `ato-synthetic-intake-worker` alias. Component A Diff 4 adds revision-scoped normalization persistence (`package_normalization_steps`), `normalize-proposal-response` and `normalize-proposal-fact-bundle` contracts, protected normalization artifact storage, and `PackageNormalizationStep` domain mapping without prompts, validation/merge logic, model client wiring, or intake orchestration. Phase 4 Component G adds revision-scoped PostgreSQL full-text search (`package_revision_search_chunks`, `package_revision_search_indexes`), bounded grounded package chat with schema-validated `CHAT_*` limits, deterministic refusal before model calls, and operator `rebuild-search-index`. Alembic head is `20260717_0012`. This remains a `dev_local` synthetic path: it is not production malware scanning or customer extraction and does not close **HS-005**. Full/targeted model runs, draft APIs, sealed confirm, review dispositions, approval/export, production scanning/extraction, and full release acceptance remain implementation work.

Feature implementation MUST NOT infer missing contracts or bypass open hard stops for authority-dependent, customer-specific, production, or qualification work.

