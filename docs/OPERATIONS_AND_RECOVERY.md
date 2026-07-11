# ATO Evidence Analysis Portal Operations and Recovery Contract

**Status:** P-1 target operations contract  
**Applies to:** Eventual `onprem_production` deployment on one customer RHEL 9-compatible host  
**Normative source:** [`../ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md)  
**Security source:** [`THREAT_MODEL.md`](THREAT_MODEL.md)

This document defines the required production behavior; it does not claim that a deployment exists or that customer-specific choices have been supplied. The technical specification prevails on conflict.

## 1. Supported topology and trust boundaries

- One installation serves one customer enterprise. It may contain many systems, revisions, runs, and users, but it is not multi-tenant.
- V1 is a single-node, RHEL 9-compatible x86_64 deployment with SELinux enforcing, Python 3.12, PostgreSQL 16, nginx, systemd, and a React/Vite static portal. High availability is not claimed.
- PostgreSQL is authoritative for identities, ACLs, lifecycle state, job leases, and the audit index. The protected filesystem is authoritative for immutable blob bytes. Recovery requires both.
- External inbound exposure is HTTPS on port `443` only. The API and PostgreSQL listen on loopback or Unix sockets.
- Outbound access is allowlisted only for the customer IdP, configured model endpoints, and approved update and backup destinations. Extraction of uploaded content has no network access.

Trust boundaries are:

1. Browser -> nginx HTTPS.
2. nginx -> FastAPI, including trusted identity-proxy headers when configured.
3. API/worker -> PostgreSQL.
4. API/worker -> protected package filesystem.
5. Extraction worker -> untrusted uploaded content.
6. Worker -> customer-approved malware scanner.
7. Worker -> configured text endpoint.
8. Worker -> configured vision endpoint when enabled.
9. Runtime -> vendored authority files.
10. Primary host -> encrypted off-host backup destination.
11. Human review state -> approved export.

Every crossing requires the applicable authentication or service identity, validation, timeout, size limit, least privilege, and material-event audit. Model endpoints have inference access only and no direct database, filesystem, tool, or write access.

## 2. Services, identities, paths, and ports

| Component | Required service/exposure | Operational contract |
| --- | --- | --- |
| Edge and portal | `nginx.service`, inbound `443` | TLS termination, security headers, request-size enforcement, static portal delivery, and API proxying |
| API | `ato-api.service`, loopback or Unix socket | Authentication, object authorization, request validation, state transitions, and streaming transfer |
| Analyzer | `ato-analyzer.service`, no inbound listener required | PostgreSQL-backed jobs, deterministic validation, bounded model calls, and immutable report generation |
| Database | `postgresql.service`, loopback or Unix socket | Authoritative mutable state, sessions, job leases, and audit index |
| Malware scanner | Customer-provided service or integration | Must be approved and available before customer file extraction; scanning fails closed |

This table is the target topology. The current scaffold ships only `ato-api.service` and an inactive health-only nginx template. Worker, portal, timer, scanner, model-hosting, and active proxy assets are added only with their implemented runtime and acceptance tests.

The application service user is `ato`. Production extraction must execute under a dedicated unprivileged service identity with systemd hardening, private temporary storage, no new privileges, resource limits, a read-only host filesystem, and no network access. Customer-managed service identity names are not defined here.

Required paths:

```text
configuration: /etc/ato-analyzer/runtime-config.json
application:   /opt/ato-analyzer
data:          /var/ato-packages
logs:          journald
```

Configuration files are owned by `root:ato` with mode `0640` or stricter. Non-secret settings live in `/etc/ato-analyzer/runtime-config.json` (selected by `ATO_RUNTIME_CONFIG_PATH`); there is no shell-sourced `config.env`. Credentials use systemd credentials or root-owned files. No internal API, database, metrics, scanner, or model port is assigned by this contract; customer-approved values must not create additional public listeners.

Current API-only scaffold assets and operator commands: [`CONFIGURATION.md`](CONFIGURATION.md) and [`../deployment/README.md`](../deployment/README.md).

## 3. Configuration and secrets

Runtime location and endpoint trust remain separate settings. Production uses `onprem_production`; endpoint profiles are `mock`, `external_openai`, or `internal_openai_compatible`. Text and vision endpoints are configured independently and may share a URL/model only after both capabilities qualify.

Production startup fails on any missing or invalid required model setting:

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

`DATABASE_DSN_CREDENTIAL_REFERENCE` references a full SQLAlchemy PostgreSQL DSN supplied through systemd credentials or a root-owned file. The runtime JSON document contains only the reference; startup code resolves it without logging the value. No DSN, host, user, password, or credential-bearing environment variable may appear in repository files, examples, command lines, process listings, operational logs, audit metadata, reports, or support bundles.

Endpoint URLs and credentials are deployment configuration and must not be editable through the portal or API. HTTPS is required except for an explicitly configured loopback internal endpoint. Redirects are forbidden, and endpoint host and port must match the deployment allowlist.

Startup validation also enforces these default limits:

| Limit | Default |
| --- | ---: |
| Package bytes | 2 GB |
| Single file bytes | 100 MB |
| Files per revision | 500 |
| Assessment items | 500 |
| Evidence items | 2,000 |
| PDF pages per file | 200 |
| Extracted text characters per file | 2,000,000 |
| Concurrent analysis runs | 2 |
| Model calls per run | 120 |

TLS keys, OIDC credentials, model credentials, audit HMAC keys, and backup keys must never appear in repository files, environment examples, command lines, process listings, operational logs, audit metadata, reports, or support bundles. Logs and support data use explicit field allowlists rather than relying only on pattern redaction.

## 4. Startup, readiness, and liveness

Systemd starts only the configured service set and applies least privilege. The API and analyzer must fail startup rather than accept work with invalid required configuration, invalid limits, or an unavailable or digest-invalid required authority manifest.

- `GET /health/live` reports process liveness only.
- `GET /health/ready` verifies the database, writable storage, authority manifest, job subsystem, and required configuration. It must not call a model endpoint.
- Readiness probe keys are a closed set:

| Probe key | Verifies |
| --- | --- |
| `database` | PostgreSQL connectivity |
| `storage` | Writable package storage |
| `authority_manifest` | Pinned authority manifest bytes and approval status |
| `jobs` | PostgreSQL `jobs` table queryability and presence of `status=reconciliation_required` rows; does not verify analyzer-worker process liveness |
| `configuration` | Required runtime configuration and DSN reference resolution |

- When the pinned authority manifest is present and digest-valid but not `approved`, `authority_manifest` reports `degraded` and aggregate readiness returns HTTP `503` with `error_code: reconciliation_required`. The current repository manifest is `draft` while **HS-001** is open; local readiness therefore stays degraded/503 until qualified authority review closes HS-001.
- A failed readiness check prevents new work from being reported as available but must not be represented as process death.
- Model, scanner, backup, and other dependency degradation is visible through metrics and the operator UI. Scanner unavailability blocks extraction without labeling content infected.
- At the 90% data-volume threshold, admission rejects new uploads and runs while reads and administrative recovery remain available.

## 5. Immutable storage and database transaction rules

A `ready` package revision is immutable. Source, canonical fact, profile, label, or link changes create a child revision. Reports are never overwritten; re-analysis creates a new run, and targeted re-analysis creates a child run without modifying its parent.

Source persistence follows this order:

1. Stream bytes to a generated temporary path while computing SHA-256.
2. `fsync`, validate, and atomically rename to content-addressed final storage.
3. Insert the PostgreSQL reference only after final storage exists.

Run persistence follows this order:

1. Write outputs under a temporary run directory.
2. `fsync` and atomically rename each file.
3. Write `artifact-manifest.json` last.
4. Mark the run `succeeded` only after manifest durability and the database commit.

The application must never report success from a database row alone or from files lacking a durable manifest. State changes and their material audit outcomes must remain consistent. Illegal transitions return HTTP `409` and write a denied audit event. `PackageRevision` mutations covered by the P1.1 API slice commit domain state (including `revision_version` when incremented), the idempotency outcome record, and the append-only audit event atomically. Mutable review updates require `If-Match`; stale writes return `412`. `POST /api/v1/package-revisions/{id}/confirm` without `If-Match` returns `428`.

Package revision finalization writes the durable `content-manifest.json` before the database transition to `scanning`. If that database transaction rolls back after the manifest is committed, the revision remains `uploading` with `content_manifest_sha256` still `null` while an on-disk manifest from the failed attempt remains. Retrying finalize with a changed artifact set would otherwise conflict forever because the writer treats any different existing manifest as immutable. Recovery is explicit and narrow: while holding the `PackageRevision` row `FOR UPDATE`, with status `uploading` and `content_manifest_sha256 IS NULL`, finalize may pass `replace_unreferenced_existing=true` to the manifest writer so it atomically replaces only that unreferenced orphan via the same temp → `fsync` → `os.replace` → directory-`fsync` sequence. The writer never unlinks the final path first; identical canonical bytes remain a no-op. All other callers keep the default `false` and receive `state_artifact_inconsistent` on conflict. A revision that already has a database digest, or any non-finalize caller, must not use replacement.

Every immutable object contains or is referenced by a SHA-256 digest. Stored JSON contains `schema_version`. User display names and external IDs are never filesystem paths.

## 6. Worker leases, replay, and idempotency

V1 uses PostgreSQL, not Redis or a generic queue. Workers claim jobs using `SELECT ... FOR UPDATE SKIP LOCKED`.

Required job fields are:

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

Lease defaults are:

- Heartbeat: 30 seconds.
- Lease: 5 minutes.
- Maximum active analyzer workers: 2.
- Model transport attempts: configured; default is two retries after the first attempt.

Expired idempotent leases are requeued only while transport budget remains. At maximum transport budget, expired idempotent leases transition the job to `failed` and a `running` run to `failed` with `dependency_attempts_exhausted`. An expired leased job with a completed `RunStep`, with no active `JobAttempt`, or while the owning run is still `queued`, is an atomicity conflict and transitions to `reconciliation_required`; it must not silently succeed or requeue and must not mutate a `queued` run. Every expired-lease recovery sets `last_error_code=job_lease_lost`, including when no active attempt row exists. When the owning run is already `succeeded`, an outstanding expired job transitions to `reconciliation_required` without mutating the run; when the run is `failed`, `cancelled`, or `policy_blocked`, only the job transitions to `failed`. Step completion has a unique constraint on `(run_id, step_key)`. At most one `active` `JobAttempt` exists per job (partial unique index). The analyzer repository couples `queued -> running` on claim when the run is still `queued`. A transient retry creates a new attempt record without changing `run_id`; it must not duplicate a completed step or export.

Network errors, HTTP `429`, and HTTP `5xx` use exponential backoff with jitter, honor `Retry-After`, and remain bounded by configured attempts and the run deadline. Authentication, authorization, malformed requests, policy failures, and other HTTP `4xx` responses are not transport-retryable. One schema-repair attempt is separate from transport retry.

`Idempotency-Key` is required on system creation, revision creation, file upload, revision finalization, revision confirmation, run start, export-draft creation, approval decision, and export delivery. Replay with the same key and normalized request digest returns the original outcome without repeating side effects, including stored `response_headers` such as `ETag`. Reuse with a different digest returns HTTP `409` `idempotency_key_conflict`. Concurrent replay-safe requests for the same principal, operation, and key are serialized with a transaction-scoped PostgreSQL advisory lock. Idempotency records are retained for 24 hours from first successful completion as a protocol invariant, not an operator setting. Expired records may be deleted; key reuse after expiry is allowed only after row removal and does not affect immutable artifacts. Export delivery has a unique durable record. V1 performs no external writeback.

Package routes require an injected authenticated principal with `actor_id` and `groups`; no request header may self-assert identity. Until OIDC/session runtime exists, the production application returns HTTP `401` `authentication_required`. Package mutations also require validated CSRF context; no insecure development bypass or `LOCAL_PASSWORD_AUTH_ENABLED` path exists. Reads require `System.owner_group` or `viewer_groups` membership; mutations require `owner_group` membership. Unauthorized access returns HTTP `403` `authorization_denied` without leaking sensitive object details. `AUDIT_HMAC_KEY_CREDENTIAL_REFERENCE` resolves at startup when configured (no env override); unavailable authentication or audit dependencies fail closed. Production malware scanning and customer file extraction remain blocked while **HS-005** is open; finalize transitions only to `scanning`.

## 7. Logging, audit, metrics, and disk thresholds

Operational logs go to journald. They include correlation identifiers, bounded error codes, outcomes, and dependency state, but never raw prompts, model responses, source text, credentials, session tokens, or authorization headers.

Audit writes use an insert-only application role. Events form an HMAC-SHA-256 chain using a protected deployment credential. A daily chain root and artifact-manifest index are copied to protected backup. An operator command and runbook must verify both event-chain and artifact integrity.

Required metrics cover:

- Queue depth and oldest age.
- Run and step duration and outcomes.
- Model call count, latency, tokens, retries, and failures.
- Upload, extraction, and quarantine outcomes.
- Storage bytes and disk watermarks.
- Database connections and migration state.
- Authentication and authorization denials.
- Approval and export outcomes.
- Backup and audit-verification status.

At 80% data-volume use, operators are warned. At 90%, the system rejects new uploads and new runs while preserving reads and administrative recovery. No automatic deletion is permitted to clear a watermark.

## 8. Backup, recovery objectives, and restore

Targets are:

```text
RTO: 4 hours
RPO: 1 hour
```

The production backup design requires PostgreSQL WAL archiving, at least hourly package-filesystem snapshots, a daily coordinated full backup, an encrypted off-host target, customer-owned backup and encryption keys, and 90-day online backup retention by default. Daily-only backup does not meet the RPO.

The restore runbook must:

1. Restore to an isolated or approved recovery host using the customer-owned keys.
2. Select a PostgreSQL recovery point and matching package-filesystem snapshot according to the qualified backup technology.
3. Apply WAL recovery, then run database/filesystem reconciliation before accepting writes.
4. Verify blob and artifact-manifest hashes, the audit chain, the daily chain root, and the manifest index.
5. Verify liveness, readiness, authorized reads, queue state, and duplicate-side-effect protections.
6. Record achieved RPO and RTO before promotion.

Missing keys, corrupted snapshots, point-in-time recovery, and a restored audit-chain verification are mandatory drill cases. RPO and RTO must not be claimed as met until demonstrated. Restore and integrity verification occur quarterly after release.

## 9. Retention, legal hold, and purge

Default retention is seven years for package revisions, run artifacts, review records, approvals, exports, chat, and audit events unless customer policy overrides it. Approval expires seven days after approval unless an approved customer policy changes that contract. Online backup retention defaults to 90 days.

Each retained object supports `legal_hold=true`. Purge must:

1. Require platform-admin initiation and a second confirmation.
2. Refuse any object under legal hold.
3. Remove primary blobs, search indexes, derived chunks, prompts/responses, and exports.
4. Write a tombstone and a non-sensitive audit event.
5. Allow encrypted backups to age out under the documented backup schedule.

Purge must not claim immediate removal from immutable backup media. Retention and approval overrides are customer policy inputs; absent an override, the defaults apply and no customer-specific policy may be invented.

## 10. Install, upgrade, and rollback principles

- Release artifacts and dependencies must be pinned, scanned, signed, and accompanied by an SBOM. Production never fetches or silently updates authority content at runtime.
- Installation must validate the supported host, SELinux enforcement, service identity and permissions, paths, public port restriction, loopback/socket bindings, egress allowlist, required configuration, authority digests, storage access, database access, and scanner dependency before customer processing.
- Install and upgrade procedures must be repeatable and must leave services either on the prior working release or on the fully validated new release.
- Before upgrade, take and verify a recoverable backup. Stop new work, drain or safely stop workers, record queue and lease state, and do not expose readiness while database migration state is incomplete.
- Authority changes require review, regression tests, applicable live-model evaluation, and a release note.
- Rollback must not mutate ready revisions, parent runs, source blobs, artifact manifests, reviews, approvals, exports, or audit history. Application rollback is permitted only when the prior release is compatible with persisted state; otherwise use the tested restore procedure.
- After install, upgrade, or rollback, run health, authorization, queue, immutable-storage, audit, and backup smoke tests before reopening work.

The repository includes an API-only install/systemd/nginx/smoke scaffold and deterministic deployment-contract tests. Completing upgrade, rollback, backup, restore, monitoring, certificate, SELinux, and live-host procedures remains a P7 deliverable; this contract does not select a customer package repository, certificate process, backup product, or monitoring product.

## 11. Failure taxonomy

| Class | Trigger or example | Required state and handling |
| --- | --- | --- |
| Invalid | Malformed, unreadable, unsupported, over-limit, unsafe archive structure, duplicate canonical IDs, or broken required references | Mark the package revision `invalid`; report a bounded reason; do not quarantine and do not retry as a dependency failure |
| Quarantine | Customer-approved malware scanner reports infected content | Mark the revision `quarantined`; isolate it and prohibit extraction; invalid or unreadable content is not labeled infected |
| Policy | Deterministic routing or other policy denies requested model work | Mark the run `policy_blocked`; record the policy outcome and `llm_call_count=0`; do not retry or bypass |
| Transient | Retryable model/network error, HTTP `429`/`5xx`, or temporary database, storage, scanner, or network dependency failure | Keep valid source out of quarantine; create bounded attempt records, preserve `run_id`, and retry only idempotent work within attempts, lease, and deadline limits |
| Terminal | Non-retryable request/dependency response, failed output validation after the one repair, context that cannot fit, exhausted retry budget, or unrecoverable internal failure | Mark the run `failed`; preserve durable diagnostics and attempts; set `error_retryable` according to the cause; require a new operator/user action rather than automatic continuation |

Authentication and authorization denials are audited and are never treated as transient transport failures. A dependency failure must not be represented as malware, invalid customer evidence, or successful completion.

## 12. Reconciler responsibilities

A startup and periodic reconciler must:

- Remove unreferenced temporary upload and run objects only after proving that no active reference or lease needs them.
- Detect database references to missing immutable files and final files with no database reference.
- Verify that a succeeded run has a durable, hash-valid `artifact-manifest.json`.
- Repair only deterministic, detectable orphan references; otherwise record a visible integrity failure for operator action.
- Requeue expired idempotent leases only while transport budget remains; at maximum budget transition the job to `failed` and a `running` run to `failed` with `dependency_attempts_exhausted`.
- Transition expired leased jobs with completed steps, missing active attempts, non-idempotent expiry, or outstanding leases on `succeeded` runs to `reconciliation_required`.
- When the owning run is `failed`, `cancelled`, or `policy_blocked`, transition only the expired job to `failed` without mutating the run.
- Preserve quarantine, legal holds, immutable revisions/runs, review history, approvals, exports, and audit history.
- Emit bounded operational status for reconciliation outcomes.

The reconciler must not fabricate missing bytes, overwrite reports, convert a failed or incomplete run to success without the required durability sequence, replay a non-idempotent side effect, or perform retention purge implicitly.

## 13. Required runbooks and drills

Before an on-prem release, the following must exist and be exercised:

| Runbook or drill | Minimum evidence |
| --- | --- |
| Install and secure startup | RHEL 9-compatible install, SELinux, permissions, port/binding, egress, readiness, and smoke-test results |
| Upgrade and rollback | Backup verification, worker drain, migration-state handling, successful upgrade, compatible rollback or restore, and smoke tests |
| Worker crash and replay | Kill before and after commit boundaries, lease expiry, idempotent requeue only with remaining transport budget, budget-exhausted idempotent expiry failing the run, atomicity-conflict reconciliation, unique step completion, and no duplicate export |
| Partial-write reconciliation | Fault injection around filesystem and database boundaries and correct orphan handling |
| Backup and restore | Point-in-time PostgreSQL/filesystem restore, missing-key and corruption cases, audit/artifact verification, and measured one-hour RPO/four-hour RTO |
| Audit integrity | Chain, wrong-key, chain-break, restored-backup, daily-root, and manifest-index verification |
| Malware and extraction failure | Scanner unavailable fail-closed behavior, infected quarantine, invalid-file separation, and extraction sandbox checks |
| Identity and authorization | OIDC/group mapping, object authorization, revocation, CSRF, self-approval denial, and approved break-glass flow |
| Model routing and outage | Every effective-label route, endpoint allowlist, zero-call policy blocks, retry bounds, and degraded dependency behavior |
| Disk and administrative recovery | 80% warning, 90% rejection of new uploads/runs, preserved reads, and recovery without automatic deletion |
| Retention, legal hold, and purge | Two confirmations, hold refusal, complete primary purge scope, tombstone, audit, and backup-age-out disclosure |
| Incident diagnostics | Allowlisted, redacted support bundle and evidence-preservation procedure |

Before real customer data, the threat model, IdP/RBAC, malware scanning, endpoint policy, TLS, secrets, egress, SELinux, backup/restore, retention, and audit verification gates must pass, with no known critical or high vulnerability lacking documented acceptance by the customer authority. Restore and integrity drills continue quarterly.

## 14. Incident and support boundaries

The product records bounded application, authorization, queue, model-routing, storage, audit, and backup status. Support diagnostics must use an explicit allowlist and exclude source content, extracted text, raw prompts/responses, tokens, credentials, keys, and raw authorization headers.

The customer owns host, identity, network, malware-scanner, model-endpoint, backup-target, encryption-key, monitoring, and incident-response controls. Customer contacts, severity definitions, notification periods, evidence-transfer methods, and escalation paths must be supplied in deployment-specific documentation and are not defined here.

A compromised host root account may access plaintext while services run, and single-node failure causes an outage. The product does not replace customer incident response, authorize a system, accept risk, or produce official assessor conclusions. Incident, vulnerability, agency, and significant-change facts remain unknown unless explicitly supplied.

## 15. Hard stops

These conditions are unresolved until the named customer input or capability is supplied and verified:

| ID | Missing input or condition | Work that must stop |
| --- | --- | --- |
| `HS-003` | IdP issuer/client/group map | Production identity deployment |
| `HS-004` | Approved model endpoint data policy | Any real customer model call |
| `HS-005` | Production malware scanner | Customer file extraction |
| `HS-008` | Backup target and key ownership | Production readiness |
| `HS-010` | Customer retention/approval override | Use defaults; do not invent customer-specific policy |

No runbook, configuration example, test fixture, or operator action may silently bypass these hard stops.

## 16. Local development startup (current implementation)

This section documents the implemented `ato_service` startup path for developer verification. It does not claim production deployment, customer-specific values, or that live PostgreSQL/Alembic smoke tests ran in default CI.

### 16.1 PowerShell setup and environment

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Have the approved secret-management process provision the database DSN out of
band in a protected UTF-8 file. The file must contain only the SQLAlchemy
PostgreSQL DSN. Never place, echo, or construct the DSN in repository files,
command lines, shell history, logs, or the runtime JSON document. Reference the
provisioned file by absolute path:

```powershell
$env:ATO_DATABASE_DSN_FILE = 'C:\secure\ato-dsn.txt'
$env:ATO_RUNTIME_CONFIG_PATH = 'deployment\config\runtime-config.dev_local.json'
```

Optional overrides:

```powershell
# $env:ATO_AUTHORITY_MANIFEST_PATH = 'docs\contracts\authority-manifest.json'
# $env:ATO_HOST = '127.0.0.1'
# $env:ATO_PORT = '8000'
```

`dev_local` resolves `STORAGE_DATA_PATH` under the project root. Production `onprem_production` uses `DATABASE_DSN_CREDENTIAL_REFERENCE` instead of `ATO_DATABASE_DSN_FILE`; see Section 3.

### 16.2 Migrations, service start, and health URLs

When a live PostgreSQL instance is available:

```powershell
alembic upgrade head
```

Start the API process:

```powershell
ato-service
# equivalent: python -m ato_service
```

Unversioned health endpoints:

```text
GET http://127.0.0.1:8000/health/live
GET http://127.0.0.1:8000/health/ready
```

With the current draft authority manifest, expect `authority_manifest: degraded` and HTTP `503` on `/health/ready` until HS-001 closes and the manifest status becomes `approved`.

### 16.3 Automated verification boundaries

Default pytest selection excludes `integration` tests:

```powershell
python -m pytest -m "not integration"
```

Deployment asset checks:

```powershell
python -m pytest tests/test_deployment_contract.py -q
```

Configuration precedence and capability flags: [`CONFIGURATION.md`](CONFIGURATION.md). Operator install flow: [`../deployment/README.md`](../deployment/README.md).

Optional live-database connectivity (`tests/ato_service/test_db.py`) runs only when `ATO_TEST_DATABASE_URL` is set. Contract verification (`tests/test_contracts.py`) remains network-free and does not prove live Postgres availability or Alembic execution.
