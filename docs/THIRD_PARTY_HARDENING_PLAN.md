# Third-Party Hardening and Production Adapter Plan

Status: Contract work delivered (Authlib OIDC, ClamAV adapter); optional jsonpointer consolidation remains backlog.

Normative contracts remain in [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md),
[`docs/THREAT_MODEL.md`](THREAT_MODEL.md), and
[`docs/requirements/traceability.yaml`](requirements/traceability.yaml).
Release evidence: [`RELEASE_EVIDENCE_INDEX.md`](RELEASE_EVIDENCE_INDEX.md).
This plan does not override hard stops or runtime-contract rules.

## 1. Purpose

This document plans four bounded workstreams identified in the third-party library
audit:

1. **Implemented:** production OIDC/JWT handling with **Authlib** (`src/ato_service/oidc_auth.py`, `oidc_jwt.py`) — see [`P5_GATE_RECORD.md`](P5_GATE_RECORD.md). Live customer IdP drill remains **HS-003** customer-gated.
2. **Evaluate:** consolidate duplicate RFC 6901 helpers with **`jsonpointer`**, only
   if custom set semantics remain intact.
3. **Implemented (contract):** production malware scanner via **local ClamAV** adapter behind `MalwareScanner` protocol (`src/ato_service/malware_scan.py`). Live scanner drill remains **HS-005** customer-gated.
4. **Keep current:** OpenAI-compatible HTTP client, PostgreSQL job leases, idempotency,
   audit hash chain, blob storage, routing, and bounded LLM normalization workflow.

The product target is **single-customer, on-prem, airgapped operation**. Runtime
must not depend on outbound internet access. Operator procedures may use offline
media or internal mirrors for signature and package updates; the application must
not call public SaaS APIs for auth, scanning, or model hosting in production profiles.

## Open backlog

- jsonpointer RFC 6901 consolidation (optional, no gate blocker)

## 2. On-prem and airgap constraints (all workstreams)

| Boundary | Allowed | Forbidden |
| --- | --- | --- |
| OIDC / JWT | HTTPS to customer-configured **internal** IdP (`OIDC_ISSUER_URL`); JWKS cached in-process with bounded refresh | Trusting unsigned tokens; calling external identity SaaS not configured by the customer |
| Malware scan | Local `clamd` over **Unix socket** or **loopback TCP**; scan bytes already read from local blob storage | Cloud AV APIs, reputation lookups, or upload-to-vendor scanning |
| LLM (unchanged) | Customer-configured on-prem or VPC endpoint via existing httpx client | Hard dependency on vendor SDKs that require runtime internet |
| Config / secrets | Schema-validated JSON + credential files per [`docs/CONFIGURATION.md`](CONFIGURATION.md) | Per-setting env overrides or secrets in JSON |

**ClamAV airgap answer:** Yes. ClamAV is designed for disconnected sites. The
application talks only to a co-located `clamd` daemon on the same host or a
customer-operated sidecar on the internal network. Virus signature updates are an
**operator** responsibility (offline `freshclam` bundle, internal mirror, or
approved change window), not an application runtime dependency on the public internet.

## 3. Build order and dependencies

```text
Component A Diff 5 (draft APIs)     — continues on main product track
  ||
  |+-> Security slice A1 (Authlib OIDC) — delivered; live IdP drill remains HS-003 customer-gated
  |
  +-> Diff 7 (ClamAV adapter)     — delivered (contract); live scanner drill remains HS-005 customer-gated
  |
  +-> Spike B1 (jsonpointer)      — optional backlog; no gate blocker
```

Authlib OIDC and the ClamAV adapter are delivered in code. Production deployment
claims that depend on real IdP login or live scanning remain customer-gated
(**HS-003**, **HS-005**). The jsonpointer consolidation spike is optional backlog
with a recorded no-go verdict; no merge required.

## 4. Workstream 1 — Authlib OIDC (replace now)

### Problem

[`src/ato_service/oidc_auth.py`](../src/ato_service/oidc_auth.py) decodes JWT
payloads via `_decode_jwt_payload()` without verifying **signature**, **issuer**,
**audience**, or **expiration**. This violates TM-002 controls in
[`docs/THREAT_MODEL.md`](THREAT_MODEL.md).

### Approach

- Add **`authlib`** (OAuth/OIDC client + JWT/JWKS utilities) as a pinned dependency.
- Replace manual JWT payload parsing in production paths with Authlib-validated
  `id_token` / access-token handling after Authorization Code exchange.
- Preserve existing behavior:
  - Authorization Code + PKCE + nonce + state (session-backed).
  - Server-side session cookie model in [`session_auth.py`](../src/ato_service/session_auth.py).
  - CSRF and Origin validation on mutating routes (unchanged).
  - Dev embedded issuer (`/dev-oidc`) remains **isolated** to `dev_local` only;
    production must reject `is_embedded_dev_oidc_issuer` and unsigned dev tokens.
- JWKS:
  - Fetch from `{issuer}/.well-known/openid-configuration` then JWKS URI.
  - Cache keys in-process with TTL and refresh on `kid` miss (key rotation).
  - Timeouts bounded; fail closed on unreachable IdP during login (503/401 per contract).
  - In airgap, issuer URL is internal; no external dependency beyond customer IdP.
- Validate: `iss`, `aud` (or azp where applicable), `exp`, `iat` skew window, `nonce`
  (id_token), and required identity claims mapped to `OidcIdentity`.

### Out of scope

- Replacing session storage, CSRF middleware, or role-by-object authorization matrix.
- SAML or header-trust identity (separate EP-06 items).
- New capability bundles or env-based OIDC overrides.

### Expected files

| Area | Files |
| --- | --- |
| Dependency | `pyproject.toml`, deployment-contract approved-deps test |
| Core | `src/ato_service/oidc_auth.py` (production validation path) |
| Tests | `tests/ato_service/test_oidc_auth.py` — invalid sig, wrong iss/aud, expired, wrong nonce, key rotation |
| Contracts | `docs/THREAT_MODEL.md` verification bullets (if wording needs sync) |
| Traceability | `docs/requirements/traceability.yaml` — TM-002 / HS-003 verification |

### Acceptance

- Production profile rejects forged, expired, wrong-issuer, wrong-audience, and
  wrong-nonce tokens with `authentication_required`.
- Valid tokens from configured internal IdP establish sessions with correct
  `actor_id` and `groups`.
- Dev loopback issuer unchanged for `dev_local` WSL bootstrap.
- No code path accepts client-supplied identity headers in production.
- Unit tests cover TM-002 verification list; no live IdP required (JWKS fixtures).

## 5. Workstream 2 — jsonpointer evaluation (spike, then maybe merge)

### Problem

RFC 6901 pointer logic is duplicated in:

- [`src/ato_service/normalize_proposal/json_utils.py`](../src/ato_service/normalize_proposal/json_utils.py)
- [`src/ato_service/draft_builder.py`](../src/ato_service/draft_builder.py) (`_set_json_pointer`, `_value_at_json_pointer`)

Custom behavior that must survive consolidation:

- **`set_json_pointer` auto-create:** missing intermediate dict keys become `{}`
  (see `json_utils.set_json_pointer`).
- **Domain guards:** `is_valid_json_pointer` enforces max length 2000 and project
  regex; callers must still use this guard before read/write.
- **Empty pointer:** read returns document; set on empty pointer is rejected.

### Approach

1. **Spike (B1):** Add `jsonpointer` as optional dev dependency or temporary branch;
   wrap library resolve/set behind thin adapters that preserve auto-create semantics.
2. **Regression suite:** Port existing normalize_proposal and draft_builder pointer
   tests; add cases for auto-create, list indices, `~0`/`~1` escaping, invalid pointer.
3. **Verdict gate:** Merge only if:
   - All regression tests pass unchanged behavior.
   - Auto-create semantics are explicit in one module (not accidental library default).
   - No new surface area in public APIs.
4. If library cannot express auto-create cleanly, **keep custom helpers** and close
   the spike as "no change."

**Verdict (2026-07-13): no-go.** The `jsonpointer` library raises when intermediate
dict keys are missing; product set semantics require auto-create to `{}`. Custom
helpers in `json_utils.py` and `draft_builder.py` are retained. See
`tests/ato_service/test_jsonpointer_evaluation.py`.

### Out of scope

- Changing canonical JSON digest algorithms or merge policy.
- Replacing JSON Patch (RFC 6902) — not used today.

### Acceptance

- Documented go/no-go decision in this file (section 8 changelog).
- If go: single shared module; duplicate private helpers removed from `draft_builder.py`.
- If no-go: spike removed; custom helpers retained with comment cross-reference.

## 6. Workstream 3 — Diff 7: local ClamAV adapter (HS-005)

### Problem

[`src/ato_service/malware_scan.py`](../src/ato_service/malware_scan.py) implements
`DevLocalIntegrityScanSubstitute` for `dev_local` and fail-closed
`ProductionMalwareScannerUnavailable` for `onprem_production`. Customer extraction
remains blocked until a verified production adapter exists (**HS-005**).

### Approach

- Implement **`ClamAvMalwareScanner`** (name TBD) satisfying `MalwareScanner`:
  - `scan_verified_bytes` / `scan_stored_artifact` unchanged contract.
  - Connect to `clamd` via **Unix domain socket** (preferred) or **127.0.0.1 / site-local
    TCP** only — no remote cloud endpoints.
  - Use a minimal client library (**`clamd`** or **`pyclamd`**) or a thin stdlib
    socket protocol implementation if library surface is acceptable.
  - Map `OK` -> `CLEAN`, `FOUND` -> `INFECTED`, daemon errors -> `ERROR` with
    `reason_code` aligned to [`docs/contracts/LIFECYCLE_AND_ERRORS.md`](contracts/LIFECYCLE_AND_ERRORS.md).
- Extend runtime config schema (non-secret only) with scanner transport settings,
  for example:
  - `MALWARE_SCANNER_TRANSPORT`: `unix_socket` | `tcp_loopback`
  - `MALWARE_SCANNER_SOCKET_PATH` or `MALWARE_SCANNER_HOST` + `MALWARE_SCANNER_PORT`
  - `MALWARE_SCANNER_TIMEOUT_SECONDS`
  - Keep existing `MALWARE_SCANNER_ENABLED`, `MALWARE_SCANNER_ID`, `MALWARE_SCANNER_FAILURE_POLICY=fail_closed`.
- Update `resolve_malware_scanner(config)`:
  - `dev_local` -> unchanged integrity substitute.
  - `onprem_production` -> ClamAV adapter when enabled and config valid; else fail closed.
- **No networking in the adapter module except** the local socket to `clamd`.
- Operator docs:
  - Install `clamd` on RHEL 9 (or sidecar VM on internal network).
  - Offline signature update procedure (internal mirror / media import).
  - systemd ordering: `clamd` before intake worker.
  - HS-005 verification checklist (EICAR test file, daemon down -> 503 fail-closed).

### Airgap deployment model

```text
[intake worker] --read blob--> [local disk]
       |
       +-- UNIX socket / loopback --> [clamd + local signature DB]
```

Optional: `clamd` on a dedicated scanner VM reachable only over customer internal
network (not internet). Application config must allow socket path or host restricted
to approved addresses; reject `0.0.0.0` or public-routable defaults in production
validation.

### Alternative: approved vendor SDK

If a customer mandates a non-ClamAV on-prem scanner, add a second adapter behind
the same `MalwareScanner` protocol and `MALWARE_SCANNER_ID` dispatch. Same rules:
local or internal-network only, fail closed, no cloud API. ClamAV remains the
reference implementation for qualification.

### Expected files

| Area | Files |
| --- | --- |
| Core | `src/ato_service/malware_scan.py`, optional `src/ato_service/clamav_scanner.py` |
| Config | `docs/contracts/runtime-config.schema.json`, examples under `deployment/config/` |
| Ops | `docs/CONFIGURATION.md`, `docs/OPERATIONS_AND_RECOVERY.md`, `deployment/systemd/` if separate unit needed |
| Tests | `tests/ato_service/test_malware_scan.py`, fake clamd socket/server tests |
| Traceability | `docs/requirements/traceability.yaml`, gate record HS-005 closure |

### Acceptance

- Production profile scans uploaded artifacts through local `clamd` when configured.
- EICAR (or fixture signature) yields `quarantined` / `infected` outcome per lifecycle contract.
- Daemon unavailable yields retryable 503; no extraction or false quarantine.
- `dev_local` still uses integrity-only substitute; WSL doc unchanged in intent.
- Deployment-contract tests validate config schema and fail-closed defaults.
- No runtime outbound internet calls introduced.

## 7. Workstream 4 — Explicit keep list (no change)

These areas stay custom; do not replace with third-party frameworks in this plan:

| Area | Rationale |
| --- | --- |
| OpenAI-compatible httpx client (`text_llm.py`) | Bounded retries, explicit timeouts, no SDK lock-in; works with on-prem model servers |
| PostgreSQL job / intake leases (`jobs.py`, `intake_work.py`) | Explicit lease fencing and auditability |
| Idempotency store | Domain-specific replay semantics |
| Audit hash chain | Contracted canonical serialization |
| Blob storage layout | Customer path and retention rules |
| Pagination cursors, ETags | API contract stability |
| Runtime config loader | Schema-validated JSON; no env sprawl |
| Bounded LLM normalization workflow | Deterministic guards + validated structured output |
| Domain format detection and problem taxonomy | Product-specific |
| Canonical JSON digests | Must not change algorithms |

Internal consolidation only (no new deps): route remaining secret reads in
`text_llm.py` through `credentials.py` when touched.

## 8. Verification matrix

| Workstream | Unit / contract tests | Manual / drill |
| --- | --- | --- |
| Authlib OIDC | JWT validation matrix, PKCE/nonce regressions | Login against internal IdP in staging |
| jsonpointer | Pointer regression suite | N/A if no-go |
| ClamAV | Fake daemon, EICAR, fail-closed | Operator HS-005 checklist on RHEL |
| Keep list | Existing suites remain green | N/A |

## 9. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Authlib JWKS fetch blocked by airgap firewall | Document internal IdP reachability from API host; bounded cache; fail closed at login |
| ClamAV stale signatures | Operator runbook for offline updates; monitor `clamd` health in readiness optional later |
| jsonpointer breaks auto-create | Spike with verdict gate; revert if semantics diverge |
| Scanner timeout on large files | Configurable timeout; align with `MAX_*` extraction limits |

## 10. Changelog

| Date | Change |
| --- | --- |
| 2026-07-13 | Initial plan approved (Authlib, jsonpointer spike, ClamAV Diff 7, keep list). |
| 2026-07-13 | Authlib production id_token validation implemented (`oidc_jwt.py`); TM-002 unit matrix landed; HS-003 remains customer IdP drill. |
| 2026-07-13 | jsonpointer spike verdict: **no-go** — library lacks intermediate dict auto-create; custom helpers retained (`test_jsonpointer_evaluation.py`). |
| 2026-07-13 | ClamAV local adapter implemented (`clamav_scanner.py`); runtime schema/examples/docs updated; **HS-005** remains open pending customer live drill. |
