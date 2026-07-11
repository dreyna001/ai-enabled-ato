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
- analyzer job leases and run/step attempts, to the extent defined by the
  technical specification
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
6. Reuse of an `Idempotency-Key` for a different normalized request is a
   conflict and performs no state change.
7. State transitions are server-enforced. A client-supplied state is never
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

### 2.2 FactProposal review

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

The specification defines the closed Disposition decision enum but does not
define a decision-to-decision state graph. This contract therefore does not
invent one. Implementations may validate a requested disposition value and
version while the review is `draft`, but MUST NOT treat a disposition value as
an independently terminal lifecycle without a later normative contract.

### 2.5 ExportDraft

| From | To | Required condition |
| --- | --- | --- |
| `draft` | `pending_approval` | Exact payload and review hashes are sealed and an Approval is created with `decision=pending`. |
| `pending_approval` | `approved` | A different authorized human approves the exact submitted payload before expiry. |
| `pending_approval` | `rejected` | An authorized human rejects the request. |
| `pending_approval` | `expired` | The applicable approval deadline expires. |
| `approved` | `exported` | The hash-bound ZIP is durably created or delivered once under the idempotency contract. |
| `approved` | `expired` | Seven days elapse after approval without a legal export. |
| `draft` | `superseded` | Its bound payload or review changes and a replacement draft is created. |
| `pending_approval` | `superseded` | Its bound payload or review changes. |
| `approved` | `superseded` | Its bound payload or review changes before export. |

`rejected`, `expired`, `superseded`, and `exported` are terminal historical
records. `exported` is never overwritten or superseded. A new payload,
approval cycle, or delivery after a terminal outcome requires a new
`ExportDraft`.

The technical specification permits `pending_approval -> expired` but does not
define the pending-approval deadline. An implementation MUST NOT invent that
deadline. The transition may be enabled only after an applicable customer
policy or later normative contract supplies it. The defined seven-day default
starts after approval and governs `approved -> expired`.

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
historical record and can no longer be acted upon. On approval,
`expires_at` MUST be set to seven days after `decided_at`.

The following attempts are denied without changing either object:

- `submitted_by == decided_by`
- a decision by a user without the `approver` role and object access
- a second decision
- a decision after the owning draft is no longer `pending_approval`
- a decision when the submitted payload hash no longer matches

Self-approval returns `self_approval_denied` with HTTP 403. A stale
hash/ETag precondition returns HTTP 412. An otherwise current request that is
in an illegal lifecycle state returns HTTP 409.

### 2.7 Job leases and attempts

Section 20 requires a job `status` field but does not define its enum or a
persistent job-status transition graph. Neither `domain.schema.json` nor
`openapi.json` defines a Job or Attempt object. Therefore this contract does
not invent values such as `pending`, `leased`, or `dead`. No implementation may
persist or expose such values as P-1 contract states until the schema and
specification are amended.

The following field-level lease operations are the complete operations
inferable from the normative specification:

| Operation | Legal precondition | Required effect |
| --- | --- | --- |
| Claim | `available_at <= now` and the job is not actively leased | Claim atomically with `SELECT ... FOR UPDATE SKIP LOCKED`; set `lease_owner`, `lease_expires_at`, and `heartbeat_at` for the claiming worker. |
| Heartbeat | Caller owns an unexpired lease | Advance `heartbeat_at` and the lease expiry under the configured five-minute lease. |
| Complete step | Caller owns an unexpired lease and `(run_id, step_key)` has no completed record | Commit completion once under the unique constraint; release the lease as part of the durable completion operation. |
| Record transient failure | Caller owns the lease and the error is retryable | Append the failed attempt; compute bounded backoff with jitter, honor `Retry-After`, and make a later attempt available without changing `run_id`. |
| Recover expired lease | Lease is expired and the step is idempotent | Requeue the work for a new lease without duplicating a completed step. |
| Observe expired non-idempotent lease | Lease is expired and the step is not idempotent | Do not requeue or repeat the step automatically; mark reconciliation as required and prevent a success commit until resolved. |

Ownership mismatch, heartbeat after expiry, completion after lease loss, and
requeue of a non-idempotent expired step are illegal. The technical
specification does not define the terminal job-status value or the exact
`attempt_count` increment point, so this contract intentionally defines
neither.

Attempt records obey these legal creation rules:

1. The initial execution creates an attempt child record.
2. A retryable network error, upstream HTTP 429, or upstream HTTP 5xx may
   create a new transport attempt under the same step and `run_id`, subject to
   the configured attempt limit and run deadline.
3. Authentication, authorization, malformed request, policy, and other
   upstream HTTP 4xx failures MUST NOT create a retry attempt.
4. A schema-repair action is not a transport retry and is allowed once.
5. A second schema-validation failure ends repair and transitions the running
   run to `failed`.
6. A completed `(run_id, step_key)` MUST NOT receive another attempt.
7. A retry MUST NOT duplicate a completed export or any completed step.

No attempt may turn a terminal `AnalysisRun` back into an active state.

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
7. Mutable review and proposal writes require a current ETag. Stale writes are
   HTTP 412, not HTTP 409.
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

## 6. Explicitly unresolved contract points

P-1 implementation MUST NOT guess the following:

- the persistent job `status` enum and job-status transitions
- the exact event that increments `attempt_count`
- the automatic deadline for `pending_approval -> expired`
- a Disposition decision-to-decision transition graph

Until the technical specification and machine contracts define these points,
only the narrower behavior stated in this document is legal.
