# Lifecycle and Error Contract

**Status:** Normative P-1 contract  
**Contract version:** 1.0.0  
**Effective date:** 2026-07-10

This document publishes the legal lifecycle and stable error taxonomy required by
Sections 14, 22, and 34 of
[`ATO_TECHNICAL_SPEC.md`](../../ATO_TECHNICAL_SPEC.md). The technical
specification remains the higher-precedence source. The state names in this
contract are the closed enums in
[`domain.schema.json`](domain.schema.json); the HTTP behavior is aligned with
[`openapi.json`](openapi.json).

## 1. Scope and interpretation

This contract covers:

- `PackageRevision`
- `AnalysisRun`
- `ReviewRevision`
- `ExportDraft`
- `Approval`
- `FactProposal` review
- analyzer jobs, `JobAttempt` child records, leases, and run/step completion
  idempotency, as defined by the technical specification and this contract
- API and asynchronous-operation errors

It does not add lifecycle states that are absent from the technical
specification and schemas. When no transition is listed here, it is illegal.
An implementation MUST stop rather than infer a transition, recovery action,
deadline, or status value that is not defined.

The following rules apply to every state machine:

1. State changes MUST be atomic with their durable side effects and audit
   record, or be recoverable by the Section 20 reconciler.
2. A successful transition writes an audit event with `outcome=succeeded`.
3. An illegal transition makes no domain-state change, returns
   `illegal_state_transition` with HTTP 409 when invoked through the API, and
   writes an audit event with `outcome=denied`.
4. Terminal states have no outgoing transition unless one is explicitly
   listed.
5. An idempotent replay with the same `Idempotency-Key` and the same normalized
   request returns the original outcome and does not repeat a transition,
   completed model step, approval decision, or export.
6. Reuse of an `Idempotency-Key` for a different normalized request digest is a
   conflict (`idempotency_key_conflict`, HTTP 409) and performs no state change.
7. Idempotency records for the P1.1 replay-safe operations listed in Section
   2.1.4 are retained for exactly 24 hours from first successful completion.
   Expired records may be deleted. Key reuse after expiry is allowed only after
   the prior row is removed; reuse does not alter immutable artifacts.
8. State transitions are server-enforced. A client-supplied state is never
   authoritative.

## 2. Legal state transitions

### 2.1 PackageRevision

Normal path:

```text
uploading -> scanning
scanning -> extracting
extracting -> awaiting_confirmation
awaiting_confirmation -> ready
```

`content_manifest_sha256` is always present on a `PackageRevision`. It is `null` only while the revision is `uploading` or when an `archived` or `invalid` historical row never reached `scanning`. The `uploading -> scanning` transition requires a durable validated content manifest and atomically sets the SHA-256 digest. A `ready` revision always has a non-null digest.

Terminal alternatives:

| From | To | Required condition |
| --- | --- | --- |
| `uploading` | `invalid` | Invalid customer input is established before scanning. |
| `scanning` | `invalid` | Content is invalid or unreadable but is not malware. |
| `extracting` | `invalid` | Deterministic extraction or required reference validation establishes invalid source content. |
| `scanning` | `quarantined` | The customer-approved malware scanner reports infected content. |
| `uploading` | `archived` | An authorized archive action is requested. |
| `scanning` | `archived` | An authorized archive action is requested. |
| `extracting` | `archived` | An authorized archive action is requested. |
| `awaiting_confirmation` | `archived` | An authorized archive action is requested. |
| `ready` | `archived` | An authorized archive action is requested. |

`invalid`, `quarantined`, and `archived` are terminal. A scanner error or
unavailable scanner MUST NOT produce `quarantined`; the revision remains
`scanning` while a retry is legal, or the operation fails visibly without
mislabeling the source.

A `ready` revision is immutable. Changes to source bytes, canonical facts,
profile, certification class, impact level, data labels, authority links, or
artifact links MUST create a child revision. Archiving a ready revision changes
only lifecycle metadata; it MUST NOT mutate its sealed content or manifest.

No other `PackageRevision` transition is legal. In particular:

- `ready` MUST NOT return to `uploading`, `scanning`, `extracting`, or
  `awaiting_confirmation`.
- `invalid` and `quarantined` MUST NOT be repaired in place.
- A transient model, database, storage, scanner, or network failure MUST NOT
  quarantine otherwise valid source material.

#### 2.1.1 Optimistic concurrency (`revision_version`)

Each `PackageRevision` persists a positive integer `revision_version`. The
initial value is `1` on creation. The server increments `revision_version`
exactly once for:

- each successful source-artifact addition through
  `POST /api/v1/package-revisions/{id}/files`, and
- each successful lifecycle transition of the owning revision.

The strong ETag for a `PackageRevision` is the quoted form `"v{revision_version}"`.
`GET`, revision creation, file upload, finalize, and confirm responses return
the current ETag in the `ETag` response header where applicable. File upload
returns the revision ETag even though the response body is a `SourceArtifact`.

`POST /api/v1/package-revisions/{id}/confirm` requires `If-Match` against the
current revision ETag. A missing header returns HTTP 428 `if_match_required`. A
stale header returns HTTP 412 `etag_mismatch`. Illegal parent state or pending
proposals remain HTTP 409 or HTTP 422 as listed in Section 4; they are not
reported as stale ETags.

#### 2.1.2 Upload, finalize, and confirm boundaries (P1.1)

These rules define the HTTP/service contract for the System + PackageRevision
slice. They do not implement malware scanning, extraction, or synthetic worker
processing. Customer extraction remains blocked while **HS-005** is open.

**Upload** (`POST /api/v1/package-revisions/{id}/files`):

- Legal only while the owning revision is `uploading`.
- Bytes are streamed to a generated temporary path, `fsync`ed, validated, and
  atomically renamed to content-addressed final storage before any database row
  references the blob.
- A successful upload inserts the `SourceArtifact` reference and increments
  `revision_version` exactly once.
- New artifacts initialize `malware_scan_status=pending` and
  `extraction_status=pending`. The API does not perform scan or extraction.
- `artifact_kind` accepts every value in the published domain enum, but P1.1
  accepts only declared `Content-Type` values `application/json` and
  `text/plain` until scanning and extraction workers exist (**HS-005** remains
  open for production extraction).
- At most one `SourceArtifact` row exists per `(package_revision_id, sha256)`.

**Finalize** (`POST /api/v1/package-revisions/{id}/finalize`):

- Legal only while the revision is `uploading`.
- The server writes a durable validated `content-manifest.json`, then performs
  the `uploading -> scanning` transition atomically with manifest digest
  assignment and `revision_version` increment.
- No scan or extraction work is claimed complete by this operation.

**Draft read/save** (`GET` / `PUT /api/v1/package-revisions/{id}/draft`):

- `GET` is legal for owner and viewer groups while a draft row exists.
- `PUT` is legal only while the revision is `awaiting_confirmation`, a draft
  row exists, and the caller is in the owner group.
- `PUT` requires current `If-Match`, `Idempotency-Key`, CSRF validation, and
  a schema-valid full `document` payload. Successful save increments
  `revision_version` once and returns the draft view with a new ETag.
- Missing draft returns HTTP 404 `resource_not_found`. Stale `If-Match`
  returns HTTP 412. Illegal parent state returns HTTP 409.

**Confirm** (`POST /api/v1/package-revisions/{id}/confirm`):

- Legal only while the revision is `awaiting_confirmation`.
- Requires current `If-Match`, `Idempotency-Key`, authenticated principal,
  owner-group authorization, and validated CSRF context.
- When a `PackageRevisionDraft` row exists, validates the draft document,
  seals immutable `sealed_package_contents`, binds `package_content_sha256`
  and `system_context_snapshot_id`, and performs
  `awaiting_confirmation -> ready` atomically with `revision_version`
  increment. Package-level confirm does not require per-leaf `FactProposal`
  decisions on the draft path.
- When no draft row exists, succeeds only when every `FactProposal` for the
  revision is non-`pending` (legacy compatibility path) and performs
  `awaiting_confirmation -> ready` without sealed package content.

#### 2.1.3 Authentication and object authorization

Package routes require an injected authenticated principal carrying `actor_id`
and `groups`. No request header may self-assert identity; external
`X-User-Id`, `X-Groups`, or equivalent headers are ignored for authorization.

Until OIDC/session runtime exists (**EP-06**), the production application
returns HTTP 401 `authentication_required` for package routes. Contract and
service tests may override or inject the authentication dependency.

Mutations also require a validated CSRF context (`X-CSRF-Token` plus
server-side Origin validation). No insecure development bypass and no
`LOCAL_PASSWORD_AUTH_ENABLED` path exist for package mutations.

Object authorization is default-deny and package-scoped. Roles map from OIDC
groups through `OIDC_GROUP_ROLE_MAPPING` with the normative defaults in
`src/ato_service/package_rbac.py`. The published per-route matrix is
`src/ato_service/route_role_matrix.py` (`ROUTE_ROLE_MATRIX`).

| Route | Method | Required package role(s) |
| --- | --- | --- |
| `/systems` | `GET` | `viewer` |
| `/systems` | `POST` | `system_owner` (prospective `owner_group`) |
| `/systems/{system_id}` | `GET` | `viewer` |
| `/systems/{system_id}/package-revisions` | `POST` | `system_owner`, `isso` |
| `/systems/{system_id}/package-revisions` | `GET` | `viewer` |
| `/package-revisions/{id}` | `GET` | `viewer` |
| `/package-revisions/{id}/files` | `POST` | `system_owner`, `control_owner` |
| `/package-revisions/{id}/finalize` | `POST` | `system_owner`, `isso` |
| `/package-revisions/{id}/draft` | `GET` | `viewer` |
| `/package-revisions/{id}/draft` | `PUT` | `system_owner`, `isso` |
| `/package-revisions/{id}/confirm` | `POST` | `system_owner`, `isso` |
| `/package-revisions/{id}/proposals` | `GET` | `viewer` |
| `/proposals/{id}/accept` | `POST` | `system_owner`, `isso` |
| `/proposals/{id}/reject` | `POST` | `system_owner`, `isso` |
| `/package-revisions/{id}/runs` | `POST` | `system_owner`, `assessor` |
| `/package-revisions/{id}/runs` | `GET` | `viewer` |
| `/runs/{run_id}` | `GET` | `viewer` |
| `/runs/{run_id}/cancel` | `POST` | `system_owner` |
| `/runs/{run_id}/matrix` | `GET` | `viewer` |
| `/runs/{run_id}/review-revisions` | `POST` | `reviewer` |
| `/review-revisions/{id}/submit` | `POST` | `reviewer` |
| `/review-revisions/{id}/dispositions/{row_id}` | `PATCH` | `reviewer` |
| `/review-revisions/{id}/comments` | `POST` | `reviewer` |
| `/review-revisions/{id}/comments` | `GET` | `viewer` |
| `/review-revisions/{id}/export-drafts` | `POST` | `reviewer` |
| `/export-drafts/{id}/submit` | `POST` | `reviewer` |
| `/approvals/{id}/approve` | `POST` | `approver`, `ao_custodian` |
| `/approvals/{id}/reject` | `POST` | `approver`, `ao_custodian` |
| `/exports/{id}/download` | `GET` | `viewer` |
| `/systems/{system_id}/authorization-decisions` | `POST` | `ao_custodian`, `isso` |
| `/systems/{system_id}/authorization-decisions` | `GET` | `viewer` |
| `/package-revisions/{id}/preflight` | `GET` | `viewer` |
| `/package-revisions/{id}/delta` | `GET` | `viewer` |
| `/package-revisions/{id}/search` | `GET` | `viewer` |
| `/package-revisions/{id}/chat` | `POST` | `viewer` |

`system_owner`, `isso`, and `reviewer` also accept membership in the owning
`System.owner_group` for migration compatibility. `viewer` accepts
`System.owner_group` or `System.viewer_groups`. Export approval denies
`submitted_by == decided_by` with HTTP 403 `self_approval_denied`.

Object lookups enforce package authorization before disclosure or mutation.
Guessed IDs for revisions, runs, reviews, exports, approvals, proposals, and
artifacts MUST NOT leak cross-system data: unauthorized callers receive HTTP
403 `authorization_denied`; missing objects receive HTTP 404
`resource_not_found`. List and detail behavior agree on visibility filters.

#### 2.1.4 Idempotency-required operations (P1.1)

`Idempotency-Key` is required on:

| Operation | Route |
| --- | --- |
| Create system | `POST /api/v1/systems` |
| Create revision | `POST /api/v1/systems/{system_id}/package-revisions` |
| Upload artifact | `POST /api/v1/package-revisions/{id}/files` |
| Finalize upload | `POST /api/v1/package-revisions/{id}/finalize` |
| Save package draft | `PUT /api/v1/package-revisions/{id}/draft` |
| Confirm revision | `POST /api/v1/package-revisions/{id}/confirm` |

Replay semantics:

- same key + same normalized request digest → return the stored HTTP status,
  headers (including `ETag` where applicable), and body without repeating
  domain mutation, filesystem writes, or audit side effects beyond the stored
  idempotency outcome record;
- same key + different digest → HTTP 409 `idempotency_key_conflict`, no state
  change.

Concurrent requests for the same `(principal, operation, Idempotency-Key)` are
serialized with a transaction-scoped PostgreSQL advisory lock before the
idempotency row is read or written. Successful outcomes persist
`response_headers` (for example `ETag`) so replays return the original headers
without recomputing `revision_version`.

Idempotency records are retained for 24 hours from first successful
completion. This retention interval is a protocol invariant, not an operator
setting.

#### 2.1.5 Transaction and audit boundaries (P1.1)

For applicable package mutations, the server commits atomically:

1. domain state change (including `revision_version` when incremented),
2. idempotency outcome record, and
3. append-only audit event.

Filesystem bytes and `content-manifest.json` MUST become durable before database
references that depend on them. Finalize may replace an on-disk orphan manifest
only while the database still proves the revision is `uploading` with
`content_manifest_sha256 IS NULL` and no other caller may use replacement. If
audit HMAC credentials or authentication dependencies required to append audit
events are unavailable, the operation fails closed and reports no success.

#### 2.1.6 Development synthetic JSON worker boundaries (P1.2)

The P1.2 worker is not a production scanner or customer extraction path. It
MUST fail startup unless `runtime_profile=dev_local`, and it claims only
`data_origin=synthetic` revisions whose declared and detected artifact media
types are all `application/json`. It MUST NOT claim customer, redacted
non-production, non-JSON, or production-profile revisions. **HS-005** remains
open.

One claimed transition commits per transaction:

- `scanning -> extracting` marks every pending artifact's synthetic scan result
  `clean`, increments `revision_version`, and writes an
  `outcome=succeeded` service audit event;
- `extracting -> awaiting_confirmation` reads each durable blob through its
  stored size and SHA-256 checks, deterministically emits pending
  `FactProposal` rows with RFC 6901 target/source pointers,
  `extraction_method=deterministic`, and `model_step_id=null`, marks extraction
  `succeeded`, increments `revision_version`, and writes an
  `outcome=succeeded` service audit event.

Claim uses `SELECT ... FOR UPDATE SKIP LOCKED`. Extraction side effects and the
state transition are atomic, so rollback leaves no partial proposals and a
committed transition is not eligible for replay. Existing proposals on an
`extracting` revision are an invariant failure and MUST NOT be duplicated.
Invalid UTF-8/JSON or duplicate canonical pointers produces the legal
`extracting -> invalid` transition with no partial proposals. No model or
external scanner call is permitted in this path.

The stable service audit actions are:

| Transition | Audit action |
| --- | --- |
| `scanning -> extracting` | `package_revision.intake_scan_completed` |
| `scanning -> quarantined` | `package_revision.intake_scan_quarantined` |
| `scanning -> invalid` | `package_revision.intake_scan_invalidated` |
| `extracting -> awaiting_confirmation` | `package_revision.intake_extraction_completed` |
| `extracting -> invalid` | `package_revision.intake_extraction_invalidated` |
| transport retry scheduled | `package_revision.intake_retry_scheduled` |

The legacy P1.2 synthetic-only worker audit actions remain documented for
historical revisions processed before unified intake wiring:

| Transition | Audit action |
| --- | --- |
| `scanning -> extracting` | `package_revision.synthetic_scan_completed` |
| `extracting -> awaiting_confirmation` | `package_revision.synthetic_extraction_completed` |
| `extracting -> invalid` | `package_revision.synthetic_extraction_invalidated` |

A durable size or SHA-256 mismatch establishes invalid source content and uses
`source_type_mismatch` on `extracting -> invalid`. Missing or temporarily
unreadable storage remains `extracting`, rolls back all attempted side effects,
and fails visibly as `storage_unavailable`; it MUST NOT be mislabeled invalid
or quarantined.

### 2.2 FactProposal review (deprecated default path)

Legacy per-leaf `FactProposal` review remains available for bounded read and
migration compatibility on published revisions. The default package editor path
uses `PackageRevisionDraft` GET/PUT and package-level confirm. Proposal list,
accept, and reject routes are deprecated in OpenAPI and are not called by the
portal default workflow.

A proposal decision is legal only while its owning `PackageRevision` is
`awaiting_confirmation`.

| From | To | Required operation |
| --- | --- | --- |
| `pending` | `accepted` | A human accepts the exact proposed value. |
| `pending` | `edited` | A human supplies and accepts an edited value. |
| `pending` | `rejected` | A human rejects the proposal. |

`accepted`, `edited`, and `rejected` are terminal for that proposal. A changed
decision or value requires a new child `PackageRevision` and, where applicable,
a new proposal; it MUST NOT rewrite the reviewed proposal.

For `extraction_method=llm_normalize`, `accepted` or `edited` is required before
the value may enter the canonical facts sealed by
`awaiting_confirmation -> ready`. Rejected values do not enter canonical
facts. Proposal writes require the current ETag through `If-Match`. A stale
ETag returns HTTP 412; a proposal operation against any parent state other than
`awaiting_confirmation` returns HTTP 409.

### 2.3 AnalysisRun

| From | To | Required condition |
| --- | --- | --- |
| `queued` | `running` | A worker has durably claimed the run job and begins work. |
| `running` | `succeeded` | Immutable outputs and `artifact-manifest.json` are durable and the database commit succeeds. |
| `queued` | `cancelled` | An authorized cancellation is accepted before execution. |
| `running` | `cancelled` | An authorized cancellation is accepted during execution and no later success commit occurs. |
| `queued` | `policy_blocked` | Deterministic routing or product policy denies the run before execution. |
| `running` | `policy_blocked` | Deterministic routing or product policy denies model work before the first model call. |
| `running` | `failed` | A non-retryable run error occurs or retry/repair limits are exhausted. |

`succeeded`, `failed`, `cancelled`, and `policy_blocked` are terminal. A
transient step failure does not itself transition the run: it appends a failed
attempt and, when legal, schedules a new attempt under the same `run_id`.

The following are expressly disallowed:

- `queued -> succeeded`
- `queued -> failed`
- any terminal state -> any other state
- retry by returning `failed`, `cancelled`, or `policy_blocked` to `queued`
- re-analysis by mutating an existing run

Re-analysis creates a new run. Targeted re-analysis creates a child run and
does not modify its parent.

`policy_blocked` requires `llm_call_count=0`. Run failure, cancellation, or
dependency failure does not change the owning `PackageRevision` and MUST NOT
quarantine valid source material.

### 2.4 ReviewRevision

| From | To | Required condition |
| --- | --- | --- |
| `draft` | `submitted` | Human review submission succeeds after required deterministic checks. |
| `submitted` | `superseded` | A later review revision replaces the submitted revision. |

`submitted` and `superseded` are immutable. Dispositions and comments may
change only the `draft` revision and require `If-Match` against its current
version. Such edits increment the version but are not lifecycle transitions.
The specification does not define a legal `draft -> superseded` transition, so
it is disallowed.

`POST /api/v1/review-revisions/{id}/submit` is the only operation that performs
`draft -> submitted`. It requires `Idempotency-Key`, `If-Match`, CSRF
validation, exactly one disposition for every matrix row, no `pending`
disposition, and valid row references. Export-draft creation MUST NOT silently
perform submission and is legal only for a submitted review.

#### 2.4.1 Disposition decision graph

Disposition `decision` values are not `ReviewRevision` lifecycle states. They
describe human review intent for one matrix row while the owning review is
`draft`.

Initial state for each row: `pending`.

While `ReviewRevision.status=draft`, the following decision transitions are
legal:

| From | To | Required condition |
| --- | --- | --- |
| `pending` | `accepted`, `edited`, `rejected`, `evidence_requested`, `weakness_confirmed` | Authorized human sets the row decision through the disposition mutation API. |
| `accepted`, `edited`, `rejected`, `evidence_requested`, `weakness_confirmed` | `accepted`, `edited`, `rejected`, `evidence_requested`, `weakness_confirmed` | Authorized human changes the row decision while the review remains `draft`. |

Each legal disposition mutation requires a current `If-Match` against the
review revision version, increments the disposition `version`, and is not a
`ReviewRevision` lifecycle transition. `edited` requires a non-null
`edited_summary`.

The following are illegal and perform no disposition change:

- any mutation when the review is `submitted` or `superseded`
- submission while any row remains `pending`
- a disposition value change without a matching review ETag

Route handlers, portal UX, audit writes, and timer behavior for disposition
mutation are implemented in the EP-06 review portal workstream. This contract
defines the closed decision graph and submission precondition.

### 2.5 ExportDraft

| From | To | Required condition |
| --- | --- | --- |
| `draft` | `pending_approval` | Exact payload and review hashes are sealed and an Approval is created with `decision=pending`. |
| `pending_approval` | `approved` | A different authorized human approves the exact submitted payload before expiry. |
| `pending_approval` | `rejected` | An authorized human rejects the request. |
| `pending_approval` | `expired` | `now >= submitted_at + APPROVAL_EXPIRY_DAYS` without a decision. |
| `approved` | `exported` | The hash-bound ZIP is durably created or delivered once under the idempotency contract. |
| `approved` | `expired` | `now >= decided_at + APPROVAL_EXPIRY_DAYS` without a legal export. |
| `draft` | `superseded` | Its bound payload or review changes and a replacement draft is created. |
| `pending_approval` | `superseded` | Its bound payload or review changes. |
| `approved` | `superseded` | Its bound payload or review changes before export. |

`rejected`, `expired`, `superseded`, and `exported` are terminal historical
records. `exported` is never overwritten or superseded. A new payload,
approval cycle, or delivery after a terminal outcome requires a new
`ExportDraft`.

Both expiry transitions use the runtime `APPROVAL_EXPIRY_DAYS` setting. The
normative default is seven days per **HS-010** until an approved customer
override changes that contract. `submitted_at` is the `Approval.submitted_at`
timestamp set when the draft enters `pending_approval`; `decided_at` is the
approval decision timestamp for `approved -> expired`.

Timer evaluation, background jobs, and approval routes that perform these
transitions belong to **EP-06**. This contract defines only the deadline
formula; implementations MUST NOT invent a different pending-approval window.

### 2.6 Approval

Creation of an Approval sets `decision=pending` and atomically transitions the
owning `ExportDraft` from `draft` to `pending_approval`.

| From | To | Coupled ExportDraft transition |
| --- | --- | --- |
| `pending` | `approved` | `pending_approval -> approved` |
| `pending` | `rejected` | `pending_approval -> rejected` |

`approved` and `rejected` are terminal decisions. The decision and
`payload_manifest_sha256` are immutable after the decision.

An Approval has no `expired` or `superseded` decision value. Expiry or payload
replacement transitions the `ExportDraft`; the Approval remains an immutable
historical record and can no longer be acted upon. On approval, `expires_at` MUST be set to `decided_at + APPROVAL_EXPIRY_DAYS`
using the same runtime setting as the export-draft expiry transitions above.

The following attempts are denied without changing either object:

- `submitted_by == decided_by`
- a decision by a user without the `approver` role and object access
- a second decision
- a decision after the owning draft is no longer `pending_approval`
- a decision when the submitted payload hash no longer matches

Self-approval returns `self_approval_denied` with HTTP 403. A stale
hash/ETag precondition returns HTTP 412. An otherwise current request that is
in an illegal lifecycle state returns HTTP 409.

### 2.8 Package revision intake work, leases, and attempts

Package revision intake work rows are durable Postgres queue rows for one
`(package_revision_id, work_phase)` execution unit. Phases are `malware_scan`
and `deterministic_extract`. `PackageRevisionIntakeAttempt` rows are child
records of a work row. Intake work is worker-internal and is not exposed in the
public OpenAPI surface.

Closed intake-work `status` enum mirrors analyzer jobs (`available`, `leased`,
`completed`, `failed`, `reconciliation_required`). `leased` rows MUST carry
`lease_owner`, `lease_expires_at`, `heartbeat_at`, and `fence_token`.
Non-leased rows MUST clear all lease fields and `fence_token`.

`expected_revision_version` captures the `PackageRevision.revision_version`
observed at claim (or finalize bootstrap). Completion, failure recording, and
`assert_intake_claim_live` MUST reject stale `fence_token` or revision-version
mismatch with visible `intake_lease_lost` semantics.

Finalize (`uploading -> scanning`) atomically inserts one `malware_scan` work row
in `available` status with `expected_revision_version` equal to the post-finalize
revision version. Idempotent finalize replay MUST NOT insert duplicate work.

Claim uses `SELECT ... FOR UPDATE OF package_revision_intake_work SKIP LOCKED`
on the work row only, inserts a new `PackageRevisionIntakeAttempt`, increments
`attempt_count`, assigns a new `fence_token`, and transitions to `leased`.
Expired-lease recovery requeues when transport budget remains; otherwise the work
row becomes `failed`. Recovery with no active attempt becomes
`reconciliation_required`.

### 2.9 Package revision normalization steps

`PackageNormalizationStep` rows are revision-scoped durable records for one
`normalize_proposal` v1 model step per `(package_revision_id, step_key)`. They
are worker-internal and are not exposed in the public OpenAPI surface. This table
is distinct from analyzer `RunStep` rows and does not create `FactProposal`
rows.

Closed normalization-step `status` enum:

| Status | Meaning |
| --- | --- |
| `reserved` | Pre-call reservation with pinned schema/prompt metadata and `input_digest`; `llm_call_count=0`. |
| `running` | Model call in progress after routing policy passes; `started_at` set. |
| `completed` | Structured response validated and persisted with Section 18.3 metadata. |
| `policy_blocked` | Routing policy denied the call before any model invocation; `llm_call_count=0`. |
| `failed` | Terminal step failure with `error_code` and `error_retryable`. |
| `reconciliation_required` | Operator recovery required; automatic retry is forbidden. |

`input_digest` is the SHA-256 of canonical reservation inputs. `fact_bundle_sha256`
is the SHA-256 of the bounded fact-bundle artifact bytes and may differ when
canonicalization semantics differ. Protected prompt, fact-bundle, and raw-response
artifacts live under `revisions/{package_revision_id}/normalization/{step_id}/`
with traversal-safe, bounded, create-new writes. Raw prompt and response bytes
are protected artifacts, not operational log content.

#### 2.9.1 Normalization-step status transition graph

| From | To | Required condition |
| --- | --- | --- |
| _(none)_ | `reserved` | Insert one row per `(package_revision_id, step_key)` with pinned `schema_id`, `prompt_version`, and `input_digest`; `llm_call_count=0`. |
| `reserved` | `policy_blocked` | Routing policy denies the call before any model invocation; `started_at` remains null, `validation_outcome` and stable `error_code` recorded, `error_retryable=false`. |
| `reserved` | `running` | Routing policy passes; protected prompt and fact-bundle artifacts are written; `started_at` set and `llm_call_count` becomes 1 or 2 before the external call. |
| `running` | `completed` | Response validated; Section 18.3 metadata and protected response artifact persisted. `model_reported`, `provider_request_id`, and token counts remain optional. |
| `running` | `failed` | Non-retryable provider, validation, or transport failure; `validation_outcome` and `error_code` required. |
| `running` | `reconciliation_required` | Atomicity or ambiguous crash after logical invocation requires operator recovery. |
| `reserved` | `failed` | Deterministic reservation or artifact preparation failure before model invocation. |

`policy_blocked`, `completed`, `failed`, and `reconciliation_required` are
terminal unless operator recovery explicitly documents a later transition.
`llm_call_count` MUST remain `0` for `reserved` and `policy_blocked`.
`repair_attempted=true` MUST imply `llm_call_count=2`.
Completed steps MUST record `response_sha256`, `validation_outcome`, all three
protected storage keys, and configured endpoint/limit metadata.

### 2.7 Analyzer jobs, leases, and attempts

Analyzer jobs are durable Postgres queue rows for one `(run_id, step_key)`
execution unit. They are worker-internal and are not exposed in the public
OpenAPI surface. `JobAttempt` rows are child records of a job.

Closed job `status` enum (also in `domain.schema.json`):

| Status | Meaning |
| --- | --- |
| `available` | Claimable or waiting for `available_at`; no active lease. |
| `leased` | A worker holds an unexpired lease. |
| `completed` | The step completed once under the `(run_id, step_key)` unique constraint. |
| `failed` | Terminal job failure: non-retryable error or transport retry budget exhausted. |
| `reconciliation_required` | Automatic reclaim is forbidden: non-idempotent expired lease, atomicity conflict (including expired lease while run remains `queued`), missing active attempt, or outstanding expired job after a terminal run outcome. |

`completed`, `failed`, and `reconciliation_required` are terminal job states.

#### 2.7.1 Job-status transition graph

| From | To | Required condition |
| --- | --- | --- |
| `available` | `leased` | Claim: `available_at <= now`, no active unexpired lease, owning `AnalysisRun` is `queued` or `running`, `(run_id, step_key)` has no completed `RunStep`, `attempt_count < TEXT_MODEL_MAX_RETRIES + 1`, and a new `JobAttempt` is inserted. |
| `leased` | `available` | Retryable transient failure with remaining transport budget: terminalize the active `JobAttempt` as `failed`, clear the lease, set `available_at` to bounded backoff, and leave the run `running`. |
| `leased` | `available` | Expired lease on an idempotent step while `attempt_count < TEXT_MODEL_MAX_RETRIES + 1`: terminalize the active `JobAttempt` as `failed` with `job_lease_lost`, clear the lease, and make the job claimable without duplicating a completed step. |
| `leased` | `completed` | Caller owns an unexpired lease, step completion commits once under the `(run_id, step_key)` unique constraint, the active `JobAttempt` ends `succeeded`, and the lease is cleared. |
| `leased` | `failed` | Non-retryable error, exhausted transport budget after the active attempt ends, expired idempotent lease at maximum transport budget, or run deadline exceeded while handling this job. |
| `leased` | `reconciliation_required` | Expired lease on a non-idempotent step; expired leased job with a completed `RunStep` or owning `AnalysisRun` still `queued` (atomicity conflict); expired leased job with no active `JobAttempt`; or an outstanding expired lease while the owning run is already `succeeded`. |

No other job-status transition is legal. In particular:

- there is no `available -> failed` transition; exhausted transport budget makes **claim** illegal and the preceding failure path performs `leased -> failed`
- a terminal job state MUST NOT return to `available` or `leased`
- `completed` MUST NOT be set while `(run_id, step_key)` already has a completed `RunStep`
- reclaiming an idempotent expired lease with remaining transport budget MUST NOT insert a new `JobAttempt` until a new claim occurs
- an idempotent expired lease at maximum transport budget MUST NOT return to `available`; it performs `leased -> failed` and, when the run is `running`, `running -> failed` with `dependency_attempts_exhausted`
- an expired leased job with a completed `RunStep` is an atomicity conflict and MUST transition to `reconciliation_required`; it MUST NOT silently succeed, requeue, or complete
- an expired leased job whose owning `AnalysisRun` is still `queued` is an atomicity conflict and MUST transition to `reconciliation_required` without mutating the run; claim always couples `queued -> running`, so a `leased` job with a `queued` run is inconsistent
- when the owning run is `failed`, `cancelled`, or `policy_blocked`, expired-lease handling transitions only the job to `failed` and MUST NOT mutate the run

The first durable claim for a run's initial step MAY perform the coupled
`AnalysisRun` transition `queued -> running` in the same atomic operation. The
analyzer repository implementation always performs this coupling on claim when
the run is still `queued`; other claimers MAY omit it only when explicitly
documented and tested outside the analyzer worker path.

#### 2.7.2 Lease operations

Defaults come from runtime configuration (Section 20): `JOB_HEARTBEAT_SECONDS`
default 30 and `JOB_LEASE_SECONDS` default 300 (five minutes).

| Operation | Legal precondition | Required effect |
| --- | --- | --- |
| Claim | Job is `available`, `available_at <= now`, no active unexpired lease, no completed `RunStep` for `(run_id, step_key)`, run not terminal, and `attempt_count < TEXT_MODEL_MAX_RETRIES + 1` | `SELECT ... FOR UPDATE SKIP LOCKED`; transition to `leased`; set `lease_owner`, `lease_expires_at = now + JOB_LEASE_SECONDS`, `heartbeat_at = now`; insert a new `JobAttempt` and increment `attempt_count` by 1. If `attempt_count >= TEXT_MODEL_MAX_RETRIES + 1`, claim is illegal and performs no state change. |
| Heartbeat | Caller owns an unexpired `leased` job | Advance `heartbeat_at` and extend `lease_expires_at` to `now + JOB_LEASE_SECONDS`. |
| Complete step | Caller owns an unexpired lease and `(run_id, step_key)` has no completed `RunStep` | Commit completion once under the unique constraint; mark the active `JobAttempt` `succeeded`; transition job to `completed`; clear lease fields. |
| Record transient failure | Caller owns the lease and the error is transport-retryable | Mark the active `JobAttempt` `failed` with the stable error code; set `last_error_code`; if another claim is legal (`attempt_count < TEXT_MODEL_MAX_RETRIES + 1` after this attempt ends) and the run deadline allows, transition to `available` with backoff on `available_at`; otherwise transition to `failed` and, when the run is `running`, `running -> failed` with the applicable terminal error code. |
| Recover expired lease | Job is `leased` with expired lease, `step_idempotent=true`, owning run is `running`, no completed `RunStep`, an active `JobAttempt`, and `attempt_count < TEXT_MODEL_MAX_RETRIES + 1` | Mark the active `JobAttempt` `failed` with `error_code=job_lease_lost`, `error_retryable=true`, and `completed_at=now`; set `last_error_code=job_lease_lost`; clear lease fields; transition to `available` with `available_at <= now`. |
| Fail expired lease at budget | Job is `leased` with expired lease, `step_idempotent=true`, and `attempt_count >= TEXT_MODEL_MAX_RETRIES + 1` | Mark the active `JobAttempt` `failed` with `error_code=job_lease_lost`, `error_retryable=true`, and `completed_at=now`; set `last_error_code=job_lease_lost`; clear lease fields; transition job to `failed`; when the run is `running`, transition run to `failed` with `dependency_attempts_exhausted`. |
| Observe expired non-idempotent lease | Job is `leased` with expired lease and `step_idempotent=false` | Mark the active `JobAttempt` `failed` with `error_code=job_lease_lost`, `error_retryable=true`, and `completed_at=now`; set `last_error_code=job_lease_lost`; clear lease fields; transition to `reconciliation_required`; block success commit until operator reconciliation. |
| Resolve expired lease conflict | Job is `leased` with expired lease and either a completed `RunStep` exists, no active `JobAttempt` exists, or owning `AnalysisRun` is still `queued` | Set `last_error_code=job_lease_lost`; terminalize any active `JobAttempt` when present; clear lease fields; transition to `reconciliation_required` without mutating the run; never requeue or complete. |
| Observe expired job on terminal run | Job is `leased` with expired lease and owning run is terminal | Set `last_error_code=job_lease_lost`; if run is `succeeded`, transition job to `reconciliation_required` without mutating the run. If run is `failed`, `cancelled`, or `policy_blocked`, transition job to `failed` without mutating the run. |

Every expired-lease recovery path sets `job.last_error_code=job_lease_lost`, including when no active `JobAttempt` row exists.

Ownership mismatch, heartbeat after lease loss, completion after lease loss, and
automatic requeue of a non-idempotent expired step are illegal. A worker that
loses its lease MUST NOT commit completion and receives `job_lease_lost`.

#### 2.7.3 `attempt_count` and `JobAttempt` semantics

- `attempt_count` is the durable count of `JobAttempt` child rows for the job.
- It starts at `0` when the job row is created.
- It increments by exactly `1` atomically with each new `JobAttempt` insert.
- The increment occurs on **claim** (`available -> leased`), not on backoff scheduling alone.
- Maximum transport attempts per step is `TEXT_MODEL_MAX_RETRIES + 1` (default: two retries after the first attempt). When `attempt_count >= TEXT_MODEL_MAX_RETRIES + 1`, claim is illegal; the job reaches `failed` only through `leased -> failed` after the final active attempt ends without a legal reclaim.
- Schema repair is not a transport retry. It neither creates a `JobAttempt` nor increments `attempt_count`; it reuses the active attempt and is governed by the single repair rule on the run step.

`JobAttempt` creation rules:

1. The first claim creates `JobAttempt` number `1`.
2. Each later claim after a retryable transient failure creates the next numbered `JobAttempt`.
3. Retryable network errors, upstream HTTP 429, and upstream HTTP 5xx may justify a later claim subject to the transport budget and run deadline.
4. Authentication, authorization, malformed request, policy, and other upstream HTTP 4xx failures MUST NOT schedule another transport claim.
5. A schema-repair action is not a transport retry. It neither creates a `JobAttempt` nor increments `attempt_count`; it is allowed once on the active attempt.
6. A second schema-validation failure ends repair and transitions the running run to `failed`.
7. A completed `(run_id, step_key)` MUST NOT receive another `JobAttempt` or claim.
8. A retry MUST NOT duplicate a completed export or any completed step.

No `JobAttempt` or job reclaim may turn a terminal `AnalysisRun` back into an
active state.

#### 2.7.4 Uniqueness and claim preconditions

The following uniqueness rules are mandatory:

1. At most one `Job` row exists per `(run_id, step_key)`.
2. At most one `JobAttempt` row exists per `(job_id, attempt_number)`.
3. At most one `active` `JobAttempt` row exists per `job_id` (partial unique index).
4. At most one completed `RunStep` exists per `(run_id, step_key)`.
5. Claim is illegal when a completed `RunStep` already exists for the job's `(run_id, step_key)`.

Violations are integrity failures and MUST NOT be repaired by inserting duplicate jobs, attempts, or completed steps.

## 3. Error response contract

### 3.1 Media type and fields

Every API error response MUST use `Content-Type:
application/problem+json` and conform to
`domain.schema.json#/$defs/Problem`:

```json
{
  "type": "https://ato.local/problems/illegal_state_transition",
  "title": "Illegal state transition",
  "status": 409,
  "detail": "The requested transition is not legal from the current state.",
  "instance": "/api/v1/runs/2f356c35-9fda-4d60-a90e-7d42cdfe5d34/cancel",
  "error_code": "illegal_state_transition",
  "request_id": "a2d9dc6f-b291-4d9f-b297-5cb5b45cc791",
  "field_errors": [],
  "retryable": false
}
```

Field rules:

- `type` is `https://ato.local/problems/{error_code}`.
- `title` is stable, short, and contains no object data.
- `status` equals the HTTP response status.
- `detail` is safe for an authorized client and MUST NOT expose stack traces,
  filesystem paths, SQL, credentials, raw model content, or sensitive source
  text.
- `instance` identifies the request target, not a local filesystem object.
- `error_code` is one of the stable lower_snake_case codes in Section 4.
- `request_id` is the server-generated UUID correlated with redacted logs and
  audit metadata.
- `field_errors` is empty unless one or more request fields failed validation.
  Each item contains a bounded `path`, stable lower_snake_case `code`, and safe
  `message`.
- `retryable=true` means the failed operation can be retried without changing
  its semantic input after the stated delay or dependency recovery. It does
  not override idempotency, attempt, deadline, or state rules.
- HTTP 429 and retryable HTTP 503 responses SHOULD include `Retry-After`.

An asynchronously accepted operation may return HTTP 202 and later store an
error on its owning object. A subsequent successful GET still returns HTTP 200
with that object. The taxonomy HTTP status is used when the error itself is
returned as a Problem response; it does not replace the resource GET status.

### 3.2 HTTP 409 versus 412

Use HTTP 409 when the submitted request is current but conflicts with domain
state or another request:

- the requested lifecycle transition is illegal
- an idempotency key is reused with different semantic input
- a completed step or decided approval would be repeated

Use HTTP 412 only when a supplied resource precondition evaluates false:

- `If-Match` does not equal the current ETag
- the exact payload/review hash bound to an approval no longer matches

The server MUST NOT report a stale ETag as 409. The server MUST NOT report an
illegal transition as 412 merely because the resource has a version. A missing
required `If-Match` is HTTP 428 `if_match_required`; it is not a stale
precondition. On 412, the client must fetch the current representation and
decide whether to form a new request. The server never merges a stale mutation
automatically.

## 4. Stable error taxonomy

The `Audit outcome` column is the required outcome for the triggering
operation. `LLM zero` means the affected operation or newly created run MUST
have made zero model calls when the error is established. `No` means model
calls may already have occurred; it never authorizes an additional call after
the error.

### 4.1 Request, identity, and invalid-input errors

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `malformed_request` | 400 | No | Request | None; request is rejected before domain mutation. | `denied` | Yes |
| `invalid_identifier` | 400 | No | Request | None. | `denied` | Yes |
| `idempotency_key_required` | 400 | No | Request | None. | `denied` | Yes |
| `authentication_required` | 401 | No | Request | None. | `denied` | Yes |
| `authorization_denied` | 403 | No | Target object | None; object existence details remain undisclosed where required. | `denied` | Yes |
| `csrf_validation_failed` | 403 | No | Request | None. | `denied` | Yes |
| `resource_not_found` | 404 | No | Requested resource | None. | `denied` | Yes |
| `request_schema_invalid` | 422 | No | Request | None; populate `field_errors`. | `denied` | Yes |
| `unsupported_authorization_path` | 422 | No | Request or `PackageRevision` | None; unsupported DoD, IC, classified, or out-of-scope authorization inputs are rejected before mutation. | `denied` | Yes |
| `customer_enterprise_mismatch` | 422 | No | `System` | None; installation serves exactly one configured customer enterprise. | `denied` | Yes |
| `unsupported_media_type` | 415 | No | `PackageRevision` | Reject artifact; revision remains `uploading` unless another listed invalid transition is independently established. | `denied` | Yes |
| `source_size_limit_exceeded` | 413 | No | `PackageRevision` | Reject bytes; if the revision limit is established, legal active state -> `invalid`. | `denied` | Yes |
| `package_limit_exceeded` | 413 | No | `PackageRevision` | Legal `uploading`, `scanning`, or `extracting` state -> `invalid`; no truncation. | `denied` | Yes |
| `source_type_mismatch` | 422 | No | `PackageRevision` | Legal active state -> `invalid`; never `quarantined`. | `denied` | Yes |
| `unsafe_archive` | 422 | No | `PackageRevision` | Legal active state -> `invalid`; unsafe members are not extracted. | `denied` | Yes |
| `source_parse_failed` | 422 | No | `PackageRevision` | `extracting -> invalid`; never `quarantined`. | `failed` | Yes |
| `duplicate_canonical_id` | 422 | No | `PackageRevision` | Legal active state -> `invalid`. | `failed` | Yes |
| `broken_reference` | 422 | No | `PackageRevision` | Legal active state -> `invalid`; do not turn the defect into a model finding. | `failed` | Yes |
| `unconfirmed_fact_proposals` | 422 | No | `PackageRevision` | Remains `awaiting_confirmation`; confirmation and dependent analysis are blocked. | `denied` | Yes |
| `analysis_not_eligible` | 422 | No | `PackageRevision` | No run is created, or an already-created queued run follows only a separately listed legal transition. | `denied` | Yes |
| `export_not_eligible` | 422 | No | `ExportDraft` | Remains `draft`; no Approval is created. | `denied` | Yes |

### 4.2 Malware quarantine

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `malware_detected` | 422 | No | `PackageRevision` | `scanning -> quarantined`; extraction stops. | `denied` | Yes |
| `malware_scan_unavailable` | 503 | Yes | `PackageRevision` | Remains `scanning`; fail closed and do not extract or quarantine. | `failed` | Yes |
| `malware_scan_failed` | 503 | Yes | `PackageRevision` | Remains `scanning`; fail closed and do not extract or quarantine. | `failed` | Yes |

For `malware_detected`, the triggering intake/finalization operation is
`denied`; the separate quarantine action is audited as `succeeded` with the
same reason code.

### 4.3 Policy denials

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `model_routing_denied` | 403 | No | `AnalysisRun` | Legal `queued` or `running` -> `policy_blocked`; package unchanged. | `denied` | Yes |
| `classified_data_unsupported` | 403 | No | `AnalysisRun` | Legal `queued` or `running` -> `policy_blocked`; no product bypass exists. | `denied` | Yes |
| `model_policy_not_approved` | 403 | No | `AnalysisRun` | Legal `queued` or `running` -> `policy_blocked`. | `denied` | Yes |
| `self_approval_denied` | 403 | No | `Approval` | Approval remains `pending`; ExportDraft remains `pending_approval`. | `denied` | Yes |
| `prohibited_model_action` | 403 | No | Request or `AnalysisRun` | No write or prohibited model step occurs; an active run follows only a separately listed legal transition. | `denied` | Yes |
| `hard_stop_active` | 503 | No | Affected operation | No prohibited work starts; owning objects remain unchanged. | `denied` | Yes |

`hard_stop_active` identifies a Section 29 hard stop. Its Problem `detail` and
redacted audit metadata SHOULD identify the applicable `HS-###` identifier but
MUST NOT include secrets or sensitive source content.

### 4.4 Transient dependency failures

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `model_dependency_unavailable` | 503 | Yes | Run step attempt | Append failed attempt; run remains `running` while retry budget and deadline allow. | `failed` | No |
| `model_rate_limited` | 503 | Yes | Run step attempt | Append failed attempt; honor upstream `Retry-After`; run remains `running` while retry is legal. | `failed` | No |
| `model_timeout` | 503 | Yes | Run step attempt | Append failed attempt; run remains `running` while retry is legal. | `failed` | No |
| `database_unavailable` | 503 | Yes | Request, job, or `AnalysisRun` | No uncommitted transition is reported as successful; retry after recovery. | `failed` | No |
| `storage_unavailable` | 503 | Yes | Request or `AnalysisRun` | No success transition; temporary output remains subject to reconciliation. | `failed` | No |
| `network_dependency_unavailable` | 503 | Yes | Attempt | Append failed attempt when durable; retry only within the bounded policy. | `failed` | No |
| `job_lease_lost` | 409 | Yes | Job/attempt | Worker MUST NOT commit completion; idempotent work may be reclaimed under a new lease. | `failed` | No |

When transport retries are exhausted, the transient code is retained on the
last attempt and the run uses the terminal `dependency_attempts_exhausted`
error below.

### 4.5 Terminal run failures

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `dependency_attempts_exhausted` | 503 | No | `AnalysisRun` | `running -> failed`; package remains unchanged. | `failed` | No |
| `model_response_schema_invalid` | 422 | No | `AnalysisRun` | After the one allowed repair fails, `running -> failed`. | `failed` | No |
| `citation_validation_failed` | 422 | No | `AnalysisRun` | `running -> failed`; invalid output is not published. | `failed` | No |
| `matrix_coverage_invalid` | 422 | No | `AnalysisRun` | `running -> failed` on missing, duplicate, or extra rows. | `failed` | No |
| `status_ceiling_violated` | 422 | No | `AnalysisRun` | `running -> failed`; overly favorable output is not published. | `failed` | No |
| `official_schema_validation_failed` | 422 | No | `AnalysisRun` or `ExportDraft` | Run: `running -> failed`. Export: remains `draft` and cannot be submitted. | `failed` | No |
| `semantic_validation_failed` | 422 | No | `AnalysisRun` or `ExportDraft` | Run: `running -> failed`. Export: remains `draft`. | `failed` | No |
| `context_limit_exceeded` | 422 | No | `AnalysisRun` | `running -> failed`; the minimum fact bundle is not silently truncated. | `failed` | No |
| `model_call_limit_exceeded` | 422 | No | `AnalysisRun` | `running -> failed`; no additional call is made. | `failed` | No |
| `model_input_token_limit_exceeded` | 422 | No | `AnalysisRun` | `running -> failed`; no additional call is made. | `failed` | No |
| `model_output_token_limit_exceeded` | 422 | No | `AnalysisRun` | `running -> failed`; no additional call is made. | `failed` | No |
| `run_deadline_exceeded` | 504 | No | `AnalysisRun` | `running -> failed`; pending attempts stop. | `failed` | No |
| `artifact_manifest_commit_failed` | 500 | No | `AnalysisRun` | `running -> failed`; run MUST NOT become `succeeded`. | `failed` | No |

`retryable=No` for a terminal run means that run is immutable and cannot be
resumed. A user may request a new run after correcting input or after a
dependency recovers, subject to normal eligibility and idempotency rules.

### 4.6 Conflicts

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `illegal_state_transition` | 409 | No | Target object | None. | `denied` | Yes |
| `idempotency_key_conflict` | 409 | No | Idempotency record | None; original outcome remains authoritative. | `denied` | Yes |
| `step_already_completed` | 409 | No | Job/run step | None; completed result is not duplicated. | `denied` | Yes |
| `approval_already_decided` | 409 | No | `Approval` | None; original decision remains authoritative. | `denied` | Yes |
| `export_already_completed` | 409 | No | `ExportDraft` | None; prior export remains authoritative. | `denied` | Yes |

### 4.7 Preconditions

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `if_match_required` | 428 | No | Target mutable resource | None. | `denied` | Yes |
| `etag_mismatch` | 412 | Yes | Target mutable resource | None; current version remains authoritative. | `denied` | Yes |
| `approval_payload_mismatch` | 412 | No | `Approval` and `ExportDraft` | No decision; if payload/review changed, the draft follows its legal transition to `superseded`. | `denied` | Yes |
| `approval_expired` | 409 | No | `Approval` and `ExportDraft` | No decision or export; ExportDraft is or becomes `expired` only through a listed transition. | `denied` | Yes |
| `export_expired` | 410 | No | `ExportDraft` | Download is denied; the terminal `expired` state is unchanged. | `denied` | Yes |

`etag_mismatch` is retryable only after fetching the current representation and
forming a request against its ETag. It does not authorize blind automatic
replay.

### 4.8 Rate and configured-limit failures

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `request_rate_limit_exceeded` | 429 | Yes | Request/principal | None; include `Retry-After`. | `denied` | Yes |
| `concurrent_run_limit_exceeded` | 429 | Yes | Run request | No run is created; include `Retry-After` when known. | `denied` | Yes |
| `chat_turn_limit_exceeded` | 429 | No | Chat request/principal | No chat call or message write. | `denied` | Yes |
| `chat_token_limit_exceeded` | 429 | No | Chat request/principal | No chat call or message write. | `denied` | Yes |
| `storage_high_watermark` | 503 | Yes | Upload or run request | At 90 percent use, no new upload or run is created; reads remain available. | `denied` | Yes |

Package byte/file/page/text limits and per-run model budgets use the more
specific codes in Sections 4.1 and 4.5. Limit failures MUST be explicit and
MUST NOT silently truncate content.

### 4.9 Internal corruption and reconciliation

| Error code | HTTP | Retryable | Owning object | State effect | Audit outcome | LLM zero |
| --- | ---: | --- | --- | --- | --- | --- |
| `artifact_digest_mismatch` | 500 | No | Artifact or manifest owner | Do not serve, approve, export, or mark success; preserve evidence for reconciliation. | `failed` | No |
| `artifact_manifest_missing` | 500 | No | `AnalysisRun` | Run cannot become `succeeded`; an already-observed inconsistency requires reconciliation. | `failed` | No |
| `state_artifact_inconsistent` | 500 | No | Affected domain object | Block mutating follow-on work; do not guess or quarantine valid source. | `failed` | No |
| `orphan_database_reference` | 500 | No | Referencing object | Block dependent work and require reconciliation; no invented lifecycle transition. | `failed` | No |
| `orphan_storage_object` | 500 | No | Storage object | Reconciler may remove only an unreferenced temporary object under Section 20.1; domain state is unchanged. | `failed` | No |
| `audit_chain_invalid` | 500 | No | Audit subsystem | Block claims of verified audit integrity and require operator reconciliation. | `failed` | No |
| `reconciliation_required` | 503 | Yes | Affected object/subsystem | Mutating follow-on work remains blocked until deterministic reconciliation succeeds. | `failed` | No |

Internal errors MUST expose only a safe Problem response. Detailed diagnostics
belong in protected, redacted operational logs. The reconciler may perform only
the repairs expressly authorized by Section 20.1: remove unreferenced temporary
objects and repair detectable orphan references. It MUST NOT fabricate
artifacts, rewrite immutable reports, silently change review or approval
decisions, or quarantine valid source material.

## 5. Cross-object invariants

The following invariants are mandatory:

1. A ready `PackageRevision` is never mutated. Any source, canonical fact,
   profile, label, or link change creates a child revision.
2. A run never mutates its package revision or parent run. Re-analysis creates
   a new run.
3. A transient dependency failure and a terminal run failure never quarantine
   otherwise valid source. Only a positive malware result during
   `scanning` permits `quarantined`.
4. Routing policy runs before the first normalization, vision, embedding,
   chat, or analysis model call. A policy-blocked run records
   `llm_call_count=0`.
5. A human must accept or edit every required LLM-normalized proposal before
   its value enters a ready revision.
6. A review revision does not modify immutable model output.
7. Mutable review, proposal, and package-confirmation writes require a current
   ETag. Stale writes are HTTP 412, not HTTP 409. Missing required `If-Match`
   on confirm is HTTP 428.
8. The export submitter cannot approve the same export.
9. Approval is bound to the exact `payload_manifest_sha256`. Any payload or
   review change invalidates the pending or approved use and transitions the
   eligible ExportDraft to `superseded`.
10. Approval permits export only. It does not make draft content official,
    authorize a system, certify compliance, or accept risk.
11. A run becomes `succeeded` only after immutable files, hashes, and
    `artifact-manifest.json` are durable and the database commit succeeds.
12. A completed step and an export are not duplicated by retry, replay, lease
    recovery, or idempotent request handling.
13. Illegal transitions, stale preconditions, policy denials, and
    self-approval attempts create denied audit events without the requested
    mutation.
14. Errors and audit metadata never contain raw prompts, model credentials,
    session tokens, authorization headers, stack traces, or sensitive source
    text.

## 6. Phase boundaries after P1.0 contract closure

The following items are now defined in this contract and `domain.schema.json`
but are not yet implemented in persistence, workers, or portal routes:

| Topic | Contract location | Implementation owner |
| --- | --- | --- |
| Job and `JobAttempt` persistence | Section 2.7; `Job`, `JobAttempt` schemas | P1 Postgres jobs foundation (partial) |
| Claim, heartbeat, completion, lease recovery | Section 2.7.2 | P1 analyzer repository and reconciler |
| `pending_approval -> expired` timer | Section 2.5 | EP-06 operator CLI `expire-approvals` |
| Disposition mutation routes and UX | Section 2.4.1 | EP-06 review/export routes and portal workbench |

Implementations MUST NOT infer values, deadlines, or transitions outside this
contract.
