# ATO Evidence Analysis Portal Threat Model

**Status:** P-1 security contract synchronized with delivered portal/API/worker controls (Phase 6)  
**Applies to:** One single-customer on-prem installation  
**Normative product contract:** [`../ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md)  
**Implemented controls (code-tested):** Authlib OIDC JWT validation, package-scoped RBAC, CSRF/origin mutation gate, model routing before calls, extraction safety limits, bounded package chat refusal, audit HMAC chain verification, validation-drill record redaction. Live customer IdP, scanner, and model-policy drills remain **customer-gated**.

## 1. Security objectives

1. Prevent an unauthorized user from learning or changing package content.
2. Prevent data from reaching a model endpoint that is not approved for its origin and sensitivity.
3. Preserve source bytes, provenance, review history, approval scope, and audit integrity.
4. Treat every uploaded file, identity assertion, API value, external response, and model response as untrusted.
5. Prevent AI output from becoming a fact, official decision, weakness, or export without deterministic checks and the required human action.
6. Recover from crashes and dependency failures without duplicate exports, lost state, or false success.

## 2. Protected assets

- Customer source files and extracted text
- CPO, SDR, OCR, OSCAL, FISMA, KSI, assessment, and vulnerability material
- Canonical facts and provenance
- Model fact bundles and raw responses
- Matrix findings and human dispositions
- Approved export bundles
- OIDC sessions, CSRF tokens, model credentials, TLS keys, audit HMAC keys, and backup keys
- Authority source snapshots and analysis profiles
- Audit events and artifact manifests
- Availability of the portal, queue, database, and storage

## 3. Actors

### Expected

- Platform administrator
- Package owner / ISSO
- Control or system owner
- Reviewer / assessor
- Export approver
- Customer IdP
- Malware scanner
- Configured text and vision model endpoints
- Backup operator

### Adversarial or compromised

- Unauthenticated network client
- Authenticated user attempting access to another package
- Malicious or compromised privileged user
- Compromised IdP or trusted identity proxy
- Malicious uploaded document or archive
- Uploaded prompt-injection content
- Compromised model endpoint
- Compromised dependency or authority source
- Attacker with read or write access to storage, database, logs, or backups
- Resource-exhaustion client

## 4. Trust boundaries

1. Browser to nginx HTTPS.
2. nginx to FastAPI and identity proxy headers.
3. API/worker to Postgres.
4. API/worker to package filesystem.
5. Extraction worker to untrusted uploaded content.
6. Worker to malware scanner.
7. Worker to text model endpoint.
8. Worker to vision model endpoint.
9. Runtime to vendored authority files.
10. Primary host to backup destination.
11. Human review state to approved export.

Crossing a boundary requires explicit authentication or service identity, validation, timeout, size limit, least privilege, and audit where material.

## 5. Threats and required controls

### TM-001: cross-package object access

**Threat:** An authenticated user changes a UUID or follows a nested object reference to read or modify a package outside their groups.

**Controls:**

- Default-deny package authorization on every object lookup.
- Resolve child objects through their authorized parent package, not by global ID alone.
- Never rely on portal hiding.
- Platform administrators lack package-content access by default.
- Downloads, search, chat, comments, run steps, artifacts, and audit views use the same policy.

**Verification:** Role-by-object API matrix, guessed UUID tests, list/detail inconsistency tests, and indirect child-object tests.

### TM-002: forged identity headers or tokens

**Threat:** A client supplies proxy identity headers, forged tokens, incorrect issuer/audience, or stale group claims.

**Controls:**

- OIDC Authorization Code with PKCE; server-side token storage.
- Validate issuer, audience, signature, expiry, nonce, and state.
- Strip identity headers at the public nginx boundary.
- Accept proxy identity only from a configured trusted upstream with mutual or shared authentication.
- Short session timeout and immediate administrative revocation.

**Verification:** Invalid issuer/audience/signature/nonce tests, direct-header spoof tests, key-rotation tests, and group-removal tests.

### TM-003: session theft and CSRF

**Threat:** A stolen browser session or cross-site request performs a write.

**Controls:**

- `Secure`, `HttpOnly`, `SameSite=Lax`, `__Host-` session cookie.
- CSRF token and Origin validation on mutating requests.
- Content Security Policy and no third-party scripts by default.
- Thirty-minute idle and eight-hour absolute session limits.
- Login, logout, revocation, and failed-CSRF audit events.

**Verification:** Missing/invalid CSRF, hostile Origin, cookie attribute, session fixation, and expiry tests.

### TM-004: approval bypass or confused deputy

**Threat:** A submitter approves their own export, approval is reused for changed content, or an old approval triggers a new destination.

**Controls:**

- Submitter and approver separation.
- Approval bound to exact payload-manifest SHA-256 and `destination_type=download`.
- Seven-day expiry.
- Any review or payload change supersedes the approval.
- Idempotency key and unique export-delivery constraint.
- Object authorization repeated at download.

**Verification:** Self-approval, changed-payload, expired, replayed, stale-review, and concurrent-decision tests.

### TM-005: path traversal and archive escape

**Threat:** Filenames, archive members, symlinks, duplicate normalized paths, or export names escape intended storage.

**Controls:**

- Server-generated storage keys.
- User filenames are display metadata only.
- Reject absolute paths, traversal, symlinks, hard links, device files, and duplicate normalized member names.
- Generate export paths from allowlisted artifact IDs.
- Verify resolved paths remain under the expected root.

**Verification:** Encoded traversal, Windows/POSIX separator, Unicode normalization, symlink/hard-link, and ZIP-slip fixtures.

### TM-006: decompression, XML, Office, PDF, image, or parser attack

**Threat:** A crafted file causes code execution, network access, memory exhaustion, parser exploitation, or data disclosure.

**Controls:**

- Malware scan before extraction; fail closed when unavailable.
- Extraction under an unprivileged, no-network, resource-limited systemd sandbox.
- File, archive, member, page, nesting, decompression-ratio, text, time, CPU, and memory limits.
- XML external entities and network resolution disabled.
- Office macros rejected; formulas never evaluated.
- Raw SVG never rendered; script and external references rejected.
- Parser dependencies pinned and scanned.

**Verification:** EICAR/customer-approved malware fixture, archive bombs, XXE, malicious Office, malformed PDF/image, timeout, and memory-limit tests.

### TM-007: server-side request forgery

**Threat:** Uploaded content or model output causes the server to fetch an internal URL or follow a redirect.

**Controls:**

- Never fetch source URLs found in package content.
- Model endpoints are deployment-owned allowlisted configuration, not user input.
- HTTPS required except explicit loopback internal endpoint.
- Redirects disabled.
- DNS/IP resolution checked against allowed host policy where applicable.
- Egress firewall limits IdP, model, backup, and approved update destinations.

**Verification:** URL-in-document, redirect, DNS rebinding, loopback, link-local, private-range, and user-supplied endpoint tests.

### TM-008: stored or reflected browser injection

**Threat:** Uploaded names, extracted content, Markdown, comments, model output, SVG, or error detail executes in a reviewer browser.

**Controls:**

- React escapes text by default.
- Sanitize Markdown with raw HTML disabled.
- Never inline raw SVG.
- Restrictive Content Security Policy.
- Safe content type and attachment disposition for source downloads.
- Never reflect internal exception strings or upstream bodies to clients.

**Verification:** HTML/Markdown/SVG/script URI payloads in every rendered field and error-path regression tests.

### TM-009: prompt injection and model control

**Threat:** Evidence tells the model to ignore policy, invent facts, reveal other context, or produce an executable action.

**Controls:**

- Evidence is delimited as untrusted data.
- Model receives only one authorized package fact bundle.
- Closed structured output schema and identifier allowlists.
- No model tools, shell, URL fetch, database query, or write action.
- Deterministic citation, completeness, applicability, and status checks.
- Assistant refusal policy and adversarial qualification suite.

**Verification:** Direct, indirect, encoded, multilingual, role-spoofing, data-exfiltration, and fake-citation prompt-injection cases.

### TM-010: sensitive data egress

**Threat:** Customer production, sensitive, CUI, classified, or unknown data reaches an external text, vision, embedding, or chat endpoint.

**Controls:**

- Required origin and sensitivity labels before upload finalization.
- Most-restrictive effective label.
- Deterministic scanning can escalate but not downgrade.
- Routing gate before every model capability.
- External profile default denies customer production, sensitive, CUI, classified, and unknown.
- Classified always unsupported.
- No runtime bypass.
- Audit endpoint host, profile, model, effective labels, and outcome without prompt content.

**Verification:** Every label combination across normalize, vision, analysis, embedding-disabled, and chat paths with zero-call assertions.

### TM-011: malicious or incorrect model output

**Threat:** The model invents evidence, changes IDs, produces incomplete rows, claims favorable status, or generates assessor-owned conclusions.

**Controls:**

- JSON Schema validation and one repair attempt.
- Expected-ID equality, no extras, no duplicates.
- Citation source/hash/offset verification.
- Deterministic status ceilings.
- Assessor-owned and consequential fields are model-prohibited.
- Model-normalized facts require human confirmation.
- Weaknesses require explicit human confirmation.

**Verification:** Missing/duplicate/extra IDs, fake offsets, wrong hashes, unknown enums, unsupported favorable status, and assessor-field generation tests.

### TM-012: authority-source or dependency compromise

**Threat:** Mutable web content, a compromised package, or an unreviewed authority update changes required behavior.

**Controls:**

- Vendor exact authority bytes.
- Pin URL/version/commit, size, and SHA-256 in an approved manifest.
- No production runtime fetch.
- Review authority changes and regenerate qualified profiles.
- Pin Python, frontend, OS, and extraction dependencies.
- Run dependency, secret, and artifact scans; create an SBOM and signed release.

**Verification:** Digest mismatch, missing source, unapproved manifest, stale profile, dependency scan, and release-signature tests.

### TM-013: job replay and duplicate side effects

**Threat:** Worker crash, lease expiry, retry, or concurrent request produces duplicate model work, review state, approval, or export.

**Controls:**

- Postgres lease and heartbeat.
- Unique `(run_id, step_key)` completion.
- Idempotency keys for consequential API actions.
- No external writeback in v1.
- Requeue only idempotent steps.
- Export delivery has a unique durable record.

**Verification:** Kill worker before/after each commit boundary, expire lease, duplicate request, concurrent approval, and repeated download tests.

### TM-014: partial or misleading persisted state

**Threat:** A crash marks a run successful before files are durable or leaves database/filesystem disagreement.

**Controls:**

- Temporary write, hash, `fsync`, atomic rename.
- Artifact manifest written last.
- Database success only after durable manifest.
- Startup/periodic reconciler.
- Invalid input, policy block, quarantine, dependency failure, and terminal failure are distinct.

**Verification:** Fault injection around every filesystem and database commit boundary and reconciliation tests.

### TM-015: tampering and repudiation

**Threat:** A privileged user alters source, run, review, approval, export, or audit history.

**Controls:**

- Content-addressed source blobs and immutable ready revisions/runs.
- Versioned review state.
- SHA-256 artifact manifests.
- Insert-only audit database role.
- HMAC-SHA-256 audit chain with protected key.
- Daily chain root and manifest index copied to protected backup.

**Verification:** Byte modification, row deletion/update denial, chain break, wrong key, restored-backup, and audit-verifier tests.

### TM-016: secret disclosure

**Threat:** Model credentials, tokens, TLS keys, audit keys, or backup keys appear in repository files, process arguments, logs, reports, or support bundles.

**Controls:**

- systemd credentials or root-owned secret files.
- Redaction by field allowlist, not pattern-only filtering.
- No raw auth headers, prompts, responses, or source text in operational logs.
- Secret scanning in CI and release packaging.
- Support bundle has an explicit allowlist.

**Verification:** Canary secret values exercised through success and failure paths and scanned across logs, audit, reports, metrics, and bundles.

### TM-017: denial of service and disk exhaustion

**Threat:** Large files, expensive model calls, repeated chat, queue flooding, database growth, or backup failure exhausts resources.

**Controls:**

- Request, package, file, page, text, token, call, queue, concurrency, timeout, and rate limits.
- Streaming upload/download.
- Per-user chat quotas.
- Queue age and disk metrics.
- Warn at 80% data volume; reject new uploads and runs at 90%.
- Preserve read and administrative recovery paths.

**Verification:** Boundary and over-limit load tests, slow upload, queue flood, model timeout, database connection exhaustion, and disk watermark tests.

### TM-018: backup theft or unrecoverable backup

**Threat:** Backup exposes customer content or cannot meet recovery objectives.

**Controls:**

- Customer-owned encryption keys.
- Encrypted off-host backup with least-privilege identity.
- WAL plus hourly filesystem snapshots and daily coordinated full backup.
- Quarterly restore and audit/artifact integrity drill.
- Documented retention and key-recovery procedure.

**Verification:** Restore to isolated host, missing key, corrupted snapshot, point-in-time recovery, and four-hour RTO/one-hour RPO evidence.

## 6. Residual risks

- A compromised authorized IdP account can act within that user's package permissions until detected or revoked.
- A compromised internal model endpoint can observe data intentionally routed to it and return malicious text; deterministic checks reduce but do not remove analysis-quality risk.
- LLM outputs remain probabilistic after qualification and require human review.
- A host-level root compromise can access plaintext while services use it; disk encryption protects powered-off media, not a running compromised host.
- Single-node v1 has an availability outage during host failure or maintenance.
- Source authorities can change after a release; the release remains bound to its pinned snapshot until reviewed.
- Backup media may retain encrypted data until scheduled expiry after a logical purge.

These risks MUST be disclosed in production security documentation and addressed through customer host, identity, network, monitoring, and incident-response controls.

## 7. Security hard stops

Real customer file processing is blocked until:

- Customer IdP and group mapping are tested.
- Model routing policy is approved.
- Malware scanner is operational.
- TLS, egress, secrets, SELinux, backup, restore, audit verification, and retention controls pass.
- The authority manifest and release artifacts are approved.

## 8. Review triggers

Review and update this threat model when:

- A new file type, parser, endpoint, model capability, integration, role, export destination, deployment topology, or authorization profile is added.
- The product begins processing a new data class.
- Writeback or tool execution is proposed.
- An authority, identity, storage, audit, backup, or queue contract changes.
- A security incident or material penetration-test finding occurs.

