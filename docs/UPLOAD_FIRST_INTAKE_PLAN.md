# Upload-First Intake and Orchestration Plan

**Status:** **P0–P7 locally complete** (2026-07-18); backend non-integration, portal, build, contract, and focused security gates pass. Customer-gated hard stops remain open.  
**Supersedes workflow assumptions in:** [`PACKAGE_EDITOR_PLAN.md`](PACKAGE_EDITOR_PLAN.md) create-then-upload ordering only — not the sealed-package contract.  
**Normative contracts:** [`ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md), [`docs/contracts/LIFECYCLE_AND_ERRORS.md`](contracts/LIFECYCLE_AND_ERRORS.md)  
**Related:** [`ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md`](../ATO_PRODUCT_FUNCTIONALITY_AND_EPICS.md), [`ATO_PORTAL_DEMO_TALKING_TRACK.md`](../ATO_PORTAL_DEMO_TALKING_TRACK.md)

**Metadata-first reconciliation (2026-07-21):** Portal and API now require profile, profile-conditional class or impact, and human-only `data_origin`/`sensitivity` **at revision create** before upload. Metadata remains editable via authenticated `PATCH` through `awaiting_confirmation`. Intake MAP/REDUCE still extracts package draft facts but **no longer suggests or autofills path metadata**. Migration `20260717_0013` (nullable DB columns) remains the Alembic head; no follow-on migration was added. Decisions **D1**, **D2** (path suggestions), and **D4** (hide until upload) below describe the **2026-07-17 upload-first deferral slice** and are **superseded for path metadata** by metadata-first create; upload-before-confirm, MAP/REDUCE, human-only labels, and single-user mode remain in force.

**Subagent policy:** All exploration and implementation phases below assign **Composer 2.5** subagents (`model: composer-2.5-fast`) per [`.cursor/rules/composer-subagents-for-plans.mdc`](../.cursor/rules/composer-subagents-for-plans.mdc). Parent agent merges, runs contract tests, and gates integration.

**Rules and skills:** Before and during each phase, actively load and apply our Cursor **rules** and **skills** (see [§0](#0-rules-and-skills-leverage-all-phases)). Prefer aligning implementation with them over preserving interim patterns that conflict — including refactoring approach, test style, LLM boundaries, and doc layout when a rule or skill prescribes something better.

---

## 0. Rules and skills leverage (all phases)

Implementation is **not** “follow this plan only.” At phase kickoff, parent and subagents must **search for applicable rules and skills**, read the relevant ones, and **change how we build** when they prescribe a better pattern. Do not treat existing code or this plan’s first draft as immutable if rules/skills point elsewhere.

### Where they live

| Source | Location | Scope |
| --- | --- | --- |
| **Project rules** | [`.cursor/rules/`](../.cursor/rules/) | Always-on for this repo (ATO contract, subagent policy, canvas policy) |
| **Global rules** | `~/.cursor/rules/` (from [`cursor-config`](https://github.com/dreyna001/cursor-config)) | Engineering, Python, security, LLM, testing, scope |
| **Global skills** | `~/.cursor/skills/` (from `cursor-config`) | Workflows invoked via `@skill-name` or read at phase start |

Canonical copy of global config: `/home/dreyna/cursor-config` (WSL) or `%USERPROFILE%\.cursor\` after install.

### How to use them (each phase)

1. **Scan** — List rules/skills that touch this phase’s files and concerns (Python API, portal React, LLM steps, RBAC, docs).
2. **Read** — Open the matching `.mdc` / `SKILL.md` before writing code; parent includes a short “rules/skills applied” note in the phase handoff.
3. **Align** — If a rule conflicts with an existing module pattern or a subagent’s first approach, **follow the rule** and refactor the touchpoint; call out the delta in the PR/handoff.
4. **Skills over improvisation** — Prefer an existing skill workflow (e.g. `@write-python-tests`, `@review-security`, `@llm-step-design`) over ad-hoc steps when the skill covers the task.

### Phase → start here

| Phase | Rules (examples) | Skills (examples) |
| --- | --- | --- |
| **P0–P2** API / portal | `python-coding-standards`, `scope-discipline`, `requirements-confidence-and-small-diffs`, `deployment-runtime-contract`, [ato-runtime-deployment-contract](../.cursor/rules/ato-runtime-deployment-contract.mdc) | `implement-python-feature`, `write-python-tests`, `project-layout-planning` |
| **P1** Context packer | `llm-boundaries`, `llm-deterministic-guardrails`, `reliability-and-performance` | `llm-step-design` |
| **P3** MAP/REDUCE intake | `cybersecurity-llm-workflow-architecture`, `security-evidence-discipline`, `llm-boundaries`, `capability-profile-architecture` | `llm-step-design`, `review-security`, `review-correctness` |
| **P4** Portal UX | `react`, `quality-and-handover`, `no-emojis` | `react`, `review-maintainability-architecture` |
| **P5** RBAC | `security`, `secure-infra-ops` | `review-security`, `python-security-review` |
| **P6** Docs | `docs-layout-and-bootstrap`, `testing-documentation-standards` | `requirements-shaping-and-diff-planning` |
| **P7** Integration | `correctness-and-testing`, `quality-and-handover` | `review-pr`, `mvp-hardening-review` |

This table is a **starting index**, not exhaustive. Grep `~/.cursor/rules/` and `~/.cursor/skills/` when scope is unclear.

### Non-negotiables from rules (override local habit)

- **Runtime contract** — Config in schema-validated JSON; secrets outside JSON; capability flags fail-fast ([ato-runtime-deployment-contract](../.cursor/rules/ato-runtime-deployment-contract.mdc), `deployment-runtime-contract`).
- **LLM** — Deterministic merge in code; bounded context; no agent memory between calls (this plan §4.3 + `llm-deterministic-guardrails`, `llm-boundaries`).
- **Scope** — Small diffs; no drive-by cleanup (`scope-discipline`, `core-engineering-standards`).
- **Human-only fields** — Plan D3; reinforced by security and evidence rules.

When a subagent deliverable diverges from an applicable rule, **fix the deliverable**, not the rule, unless the parent explicitly escalates a contract change.

---

## 1. Product story (locked)

The system owner creates a **System** — no ATO work has happened in the product yet. They create a **revision**, **upload whatever they have** (nothing → partial pile → near-complete package), and **agents read the documents**. Extracted and inferred fields appear for **human edit**. The owner confirms when satisfied; **Confirm Package** seals that snapshot for analysis and export.

**Not the story:** rely on intake to guess FedRAMP vs FISMA and impact level after upload — operators declare path metadata at create.

---

## 2. Locked decisions

Historical P0–P7 decisions as originally locked. **Superseded for path metadata** where noted in the metadata-first reconciliation banner above.

| ID | Decision | Status |
| --- | --- | --- |
| **D1** | **Upload first.** Documents upload before confirm; path metadata was deferred until post-upload PATCH in the 2026-07-17 slice. | **Superseded for path metadata** — create now requires profile/class-or-impact and human labels before upload; upload-before-confirm unchanged. |
| **D2** | **Suggest, never auto-lock.** AI may propose package draft facts. Path metadata suggestions were editable in portal through 2026-07-17. | **Superseded for path metadata** — intake no longer suggests profile/class/impact; draft fact suggestions remain editable. |
| **D3** | **`data_origin` and `sensitivity` are human-only.** AI must **never** write these. Optional mismatch warning only. | **In force** |
| **D4** | **Hide until upload.** Profile, class, impact, and related path fields were hidden at create until upload began in the 2026-07-17 slice. | **Superseded** — path metadata is collected at create and editable via PATCH while pre-ready. |
| **D5** | **~70% context utilization per LLM call** plus existing output and instruction reserves (spec §17.1). Configurable in runtime JSON. | **In force** |
| **D6** | **No LangChain.** Orchestration = Postgres jobs, worker leases, immutable step artifacts, deterministic merge in application code. | **In force** |
| **D7** | **Single user role for now.** One operator can upload, edit, review, and approve export. Relax `self_approval_denied` for `dev_local` / single-user profile; document production re-enable path. | **In force** |
| **D8** | **System soft-archive only.** `archived_at` on System; no hard delete. Default list hides archived. | **In force** |

**Editable vs sealed (unchanged contract):**

- **Draft (`awaiting_confirmation`):** all shown fields and package draft document remain editable.
- **Ready (`ready`):** revision content is immutable; changes require a new revision (optionally with parent link).

---

## 3. Target user flow

```text
Create System
  → Create Revision (profile, class/impact, data origin, sensitivity; optional parent pre-fill)
  → Upload source artifacts + Finalize
  → Intake: scan → extract → chunk/index
  → Intake MAP: bounded LLM passes (pack chunks to ~70% budget per call)
  → Intake REDUCE: merge into draft + provenance + conflict list
  → Portal: Package Editor (pre-filled draft facts, all editable) + metadata PATCH corrections
  → Human edits → Confirm Package → ready
  → Preflight → Analysis → Review → Export (single user may approve)
```

---

## 4. Intake pipeline (technical)

### 4.1 Stages

| Stage | Owner | Reuse vs rewrite |
| --- | --- | --- |
| **Upload / finalize** | Existing `package_revisions` + blob storage | **Reuse** |
| **Scan / extract** | `intake.py`, `extraction/*` | **Reuse**; extend artifact coverage as needed |
| **Chunk / index** | Spec §13.5 (6k + 500 overlap); `package_search_index` | **Reuse** |
| **MAP jobs** | New: per document or chunk-group LLM calls | **New** worker steps; reuse `normalize_proposal` patterns (schemas, routing, limits) |
| **REDUCE / merge** | Merge into `PackageRevisionDraft` + `field_provenance` | **Extend** `draft_builder` / new `intake_merge.py`; delete dead `FactProposal` portal path if unused |
| **Readiness report** | Inventory + gaps + declared path metadata | **New** API + portal panel |
| **Context packer** | Rank chunks → fill to `CONTEXT_UTILIZATION_TARGET` − reserves | **New** shared module; **reuse** in matrix, chat, intake |

### 4.2 MAP call pattern (every LLM step)

```text
Retrieve relevant chunks for this task
  → Pack into context budget (~70% of window minus output + instruction reserve)
  → One API call with structured JSON output
  → Persist artifact (prompt hash, response, citations, context_complete flag)
  → Repeat until task coverage complete or policy_blocked
```

### 4.3 Correlation across calls

| Mechanism | Purpose |
| --- | --- |
| Immutable chunks + `chunk_id` | Citations |
| Normalization / run step artifacts on disk | Per-call audit trail |
| `field_provenance` on draft | Which chunk filled which field |
| Conflict records | Same pointer, different values → human resolves in editor |
| `context_complete=false` | Incomplete read → no `supported` in analysis |

**No agent memory.** Postgres + files are the source of truth between calls.

### 4.4 AI may propose (from docs)

| Field / area | AI |
| --- | --- |
| Package title, boundary, mission, contacts | Yes |
| Control implementation statements | Yes (with citations) |
| Profile / certification class / impact level | **Never from intake** — operator at create; PATCH for corrections |
| `data_origin`, `sensitivity` | **Never** |
| Assessor-owned / official conclusions | **Never** |

### 4.5 Config (runtime JSON)

| Setting | Purpose |
| --- | --- |
| `CONTEXT_UTILIZATION_TARGET` | Default `0.70` |
| Existing text model + routing flags | Unchanged |
| Single-user RBAC mapping | Collapse IdP groups → one effective role set for dev/demo |

---

## 5. Portal UX changes

| Area | Change (2026-07-17) | Metadata-first note (2026-07-21) |
| --- | --- | --- |
| **Create revision** | Minimal form; hide profile, class/impact, origin, sensitivity | **Superseded** — full metadata at create |
| **After upload + intake** | Reveal metadata section + Package Editor tabs | Metadata panel visible from create; intake fills draft facts only |
| **Readiness panel** | Files received, suggested path, gaps, conflicts | Declared path metadata + gaps; empty `suggested_metadata` |
| **Conflicts** | Side-by-side values + sources; user picks or edits |
| **System list** | Archive action; hide archived by default |
| **Review / export** | Same user may submit and approve (D7) |

---

## 6. API / contract changes (move together)

| Change | Notes |
| --- | --- |
| Metadata-first revision create (2026-07-21) | Require profile/class-or-impact and human labels at create; PATCH for corrections through `awaiting_confirmation` |
| Upload-first revision create deferral (2026-07-17, superseded for path metadata) | Minimal create + post-upload PATCH — implemented in P2 before metadata-first reconciliation |
| Post-upload metadata update route | `PATCH /package-revisions/{id}` — still used for corrections |
| `POST /systems/{id}/archive` | Soft-archive (D8) |
| `GET /systems` | Exclude archived by default |
| Intake job types + status on revision | `uploading` → … → `awaiting_confirmation` |
| Readiness / intake-report endpoint | Inventory + gaps + conflicts; `suggested_metadata` always empty for path fields |
| OpenAPI + domain schema + lifecycle doc | Same PR family |
| `traceability.yaml` | New requirements |
| Relax self-approval | Config-gated single-user mode |
| Parent revision | Enforce parent status `ready` on create |

**Delete / retire when replaced:**

- Deprecated OpenAPI `FactProposal` accept/reject as default portal path (already deprecated).
- Portal create-revision profile-first UX (pre-2026-07-17).
- Portal minimal create + post-upload metadata reveal (2026-07-17 slice; superseded 2026-07-21).
- Demo copy implying metadata is chosen only after upload.

---

## 7. Implementation phases

### Phase status (verified 2026-07-17)

| Phase | Decision(s) | Status | Evidence (non-exhaustive) |
| --- | --- | --- | --- |
| **P0** | D8 soft-archive | **Complete** | `systems.py` archive route; portal **Show archived**; `tests/ato_service/test_systems.py`; contract tests |
| **P1** | D5 context cap | **Complete** | `context_budget.py`; `CONTEXT_UTILIZATION_TARGET` in runtime schema/examples; `tests/ato_service/test_context_budget.py`; matrix/chat packer wiring |
| **P2** | D1, D4 defer metadata | **Complete (historical)** | Migration `20260717_0013`; upload-first deferral API/portal — **superseded for path metadata by metadata-first create (2026-07-21)** |
| **P3** | D6 MAP/REDUCE | **Complete** | `intake_map.py`, `intake_merge.py`; intake report OpenAPI/domain schema; intake worker tests; path metadata suggestions removed in metadata-first reconciliation |
| **P4** | D2, D3, D4 UX | **Complete (reconciled)** | `RevisionMetadataPanel`, `IntakeReadinessPanel`, conflict UI; metadata-first create form; portal workflow tests |
| **P5** | D7 single-user RBAC | **Complete** | `SINGLE_USER_MODE_ENABLED` (default `false`); `tests/ato_service/test_ep06_security_matrix.py` |
| **P6** | Docs / talking track | **Complete** | This reconciliation pass; epics §2; talking track; `PORTAL_WORKFLOW_GUIDE`; bounded release index note |
| **P7** | Integration gate | **Complete (local)** | Backend: 1,839 passed, 1 skipped, 20 deselected; portal: 107 passed and production build succeeded. Live/customer validation remains governed by hard stops. |

**Hard stops:** unchanged and open — no customer-ready, production-ready, model-policy, authority-review, scanner, FISMA parity, or live drill claims from this plan.

### Phase 0 — System soft-archive (D8)

**Scope:** API, domain, lifecycle §2.1, portal toggle, tests.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **0A-contract** | generalPurpose | composer-2.5-fast | OpenAPI, domain schema, lifecycle, `systems.py` archive route |
| **0B-portal** | generalPurpose | composer-2.5-fast | System list archive UI, `include_archived` query |

**Gate:** `tests/test_contracts.py`, `tests/ato_service/test_systems.py`.

---

### Phase 1 — Context packer + config (D5)

**Scope:** Shared `context_budget.py` (or extend existing limits module), runtime schema, CONFIGURATION.md, unit tests.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **1A-packer** | generalPurpose | composer-2.5-fast | Packer module + config schema + tests |
| **1B-wire-matrix-chat** | generalPurpose | composer-2.5-fast | Adopt packer in matrix + chat call sites (behavior-preserving) |

**Gate:** existing matrix/chat tests pass; packer unit tests.

---

### Phase 2 — Revision create deferral + hidden fields (D1, D4) — historical

**Scope (2026-07-17):** API allowed minimal revision create; portal upload-first; deferred profile validation until pre-confirm. **Superseded 2026-07-21** by metadata-first create requiring path metadata before upload while retaining nullable DB columns from migration `20260717_0013`.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **2A-api** | generalPurpose | composer-2.5-fast | `package_revisions.py`, validation split, OpenAPI |
| **2B-portal-create** | generalPurpose | composer-2.5-fast | `RevisionCreateForm`, `WorkflowPage` gating |

**Gate:** contract tests + portal unit tests.

---

### Phase 3 — Intake MAP/REDUCE worker (D6)

**Scope:** Job types, map steps, merge into draft, conflict list, readiness artifact. Reuse `normalization_service` / `intake.py` leases; rewrite orchestration where sequential normalize is too narrow.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **3A-explore** | explore | composer-2.5-fast | Map gaps in intake vs normalize vs draft_builder (read-only report to parent) |
| **3B-map-worker** | generalPurpose | composer-2.5-fast | MAP jobs, step artifacts, context packer integration |
| **3C-merge** | generalPurpose | composer-2.5-fast | REDUCE merge, provenance, conflicts, draft persistence |
| **3D-readiness-api** | generalPurpose | composer-2.5-fast | Readiness endpoint + schema |

**Gate:** integration tests with synthetic packages; hostile-input fixtures unchanged or extended.

**Integration subagent (after 3B+3C):** generalPurpose composer-2.5-fast — verify merge + MAP contracts align.

---

### Phase 4 — Portal reveal + edit UX (D2, D3, D4)

**Scope:** Post-upload metadata panel, conflict UI, human-only origin/sensitivity, readiness panel.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **4A-metadata-panel** | generalPurpose | composer-2.5-fast | Reveal logic, editable suggestions, origin/sensitivity manual-only |
| **4B-conflicts-readiness** | generalPurpose | composer-2.5-fast | Conflict list + readiness panel components |

**Gate:** portal vitest; manual WSL walkthrough per [`PORTAL_WORKFLOW_GUIDE.md`](PORTAL_WORKFLOW_GUIDE.md).

---

### Phase 5 — Single-user RBAC (D7)

**Scope:** Config flag, relax self-approval when enabled, default dev_local mapping, docs.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **5A-rbac** | generalPurpose | composer-2.5-fast | `package_rbac.py`, approval route, CONFIGURATION.md |
| **5B-tests** | generalPurpose | composer-2.5-fast | EP-06 security matrix updates for single-user mode |

**Gate:** `test_ep06_security_matrix.py`, export E2E path with one principal.

---

### Phase 6 — Docs, talking track, traceability

**Scope:** Epics §2 rewrite, talking track upload-first, this plan status, RELEASE_EVIDENCE_INDEX note.

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **6A-user-docs** | generalPurpose | composer-2.5-fast | Epics, talking track, PORTAL_WORKFLOW_GUIDE |
| **6B-traceability** | generalPurpose | composer-2.5-fast | traceability.yaml, spec §17.1 70% note |

**Gate:** doc reconciliation tests if counts referenced; parent review only.

---

### Phase 7 — Integration gate (parent + one subagent)

| Subagent | Type | Model | Owns |
| --- | --- | --- | --- |
| **7-integration** | generalPurpose | composer-2.5-fast | Full `-m "not integration"` gate, fix cross-phase breaks |

**Gate:** CI contract workflow equivalent locally.

---

## 8. Reuse vs rewrite guidance

| Keep / extend | Reason |
| --- | --- |
| Chunk model (6k/500), blob storage, intake scan/extract | Correct abstraction |
| `normalize_proposal` schemas, routing, prohibited prefixes | MAP step contract |
| `PackageRevisionDraft`, confirm/seal, ETag | Draft edit model fits D1 |
| Workers, job leases, idempotency | Orchestration backbone (D6) |
| Matrix, chat, preflight after `ready` | Downstream unchanged |

| Rewrite / remove | Reason |
| --- | --- |
| Create-revision-first profile validation | Conflicts with early upload-first deferral; replaced by metadata-first create |
| Default portal FactProposal cards | Deprecated; editor is default |
| Intake path-metadata suggestions | Removed in metadata-first reconciliation |
| Self-approval denial when single-user flag set | D7 |
| Narrow 2-call normalize as **only** intake intelligence | Replace with MAP/REDUCE pipeline |

**Principle:** extend until the abstraction fights the upload-first story, then rewrite that boundary — not the whole codebase.

---

## 9. Out of scope (this plan)

- LangChain or external agent frameworks
- AI-filled `data_origin` / `sensitivity`
- Hard delete systems
- Production multi-role IdP (document re-enable only)
- Impact-level matrix catalog filtering (future)
- Live customer production drills (hard stops unchanged)

---

## 10. Success criteria

1. Owner can create revision with required path metadata and upload evidence.
2. After intake, draft package facts appear **pre-filled and editable**; path metadata is not intake-suggested.
3. Origin/sensitivity are **never** AI-written.
4. Large uploads run **multiple bounded LLM calls** with persisted merge and citations.
5. Conflicts surface in UI; user resolves before confirm.
6. Single user completes upload → confirm → run → review → export.
7. Systems can be soft-archived.
8. Contract tests and non-integration gate pass.

---

## 11. Execution order

```text
P0 archive → P1 context packer → P2 upload-first create → P3 MAP/REDUCE intake
  → P4 portal UX → P5 single-user RBAC → P6 docs → P7 integration gate
```

P1 may parallel with P0. P3 blocks P4. P5 may parallel with P4 after P3 API stable.

---

## 12. Parent agent checklist (each phase)

- [x] **Rules/skills:** Identify and read applicable global + project rules/skills for this phase; note which ones governed the approach (see [§0](#0-rules-and-skills-leverage-all-phases))
- [x] Merge subagent branches; resolve contract conflicts (P0–P6)
- [x] Run `tests/test_contracts.py` + targeted service tests (P0–P6 gates)
- [x] Run `-m "not integration"` before **P7** sign-off
- [x] Update this plan phase status inline when complete (P0–P7)
- [x] Do not close hard stops from mocks
- [x] Phase handoffs note rule/skill alignment where applicable (P0–P6)
