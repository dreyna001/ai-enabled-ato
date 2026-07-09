# ATO AI Accelerator Plan

## Status

- Planning + **Block 1 technical spec** ([`ATO_BLOCK1_TECHNICAL_SPEC.md`](ATO_BLOCK1_TECHNICAL_SPEC.md)). **Project home:** `C:\Users\dreyn\OneDrive\Desktop\Cursor\ai-enabled-ato\` (sibling to `llm_notable_analysis`, not inside that monorepo).
- Block 1: `dev_local` + OpenAI API first for **synthetic/redacted non-CUI prototyping only**; on-prem VM (systemd, `/etc`, local vLLM) deferred.
- End-state product plan. Assistive, evidence-bound, draft-only.
- Target: government ATO analysis layer. Pattern: mirror notable analysis scope.
- Demo glossary: [`ATO_PORTAL_DEMO_TALKING_TRACK.md`](ATO_PORTAL_DEMO_TALKING_TRACK.md)

## Goal and non-goals

**Goal:** Automate manual ATO analysis; keep GRC/eMASS/scanners authoritative; support `fisma_agency` | `fedramp` | `dod_rmf`; produce evidence review, gap analysis, and full-draft gov artifacts; add portal, OSCAL interop, optional gated writeback.

**Non-goals:** Replace GRC/eMASS/FedRAMP/Qualys/Tenable/STIG/CMDB/doc repos; become SoR for controls/POA&Ms/assets/approvals; final compliance/risk/auth/waiver decisions; AO/3PAO/assessor sign-off; writeback without approval gates; privacy controls (800-53 appendix / PIA); train on customer data; ConMon platform (Option 2); official FedRAMP PMO/Marketplace submission; scan/test execution; evidence-collection workflow engine; **AI image/diagram generation** (no generated architecture graphics — customer-provided diagrams only).

## Government context





### Authorization paths (user-selectable)


| Path           | SoR               | Assessor       | Path-specific outputs                               |
| -------------- | ----------------- | -------------- | --------------------------------------------------- |
| `fisma_agency` | Agency GRC        | ISSO / SCA     | Agency SAR inputs, POA&M drafts                     |
| `fedramp`      | CSP GRC / FedRAMP | 3PAO, PMO      | FedRAMP SAR/POA&M drafts, ConMon prep for GRC       |
| `dod_rmf`      | eMASS             | DoD SCA / CCRI | eMASS SAR inputs, STIG/CCRI brief, DoD POA&M drafts |


User selects path at package or deployment level. Official submission/sign-off stays in customer tools.

### Baseline and roles

- Baseline: **NIST SP 800-53 Rev 5**; normalized control IDs (`AC-2`); optional overlays/tailoring/inheritance from OSCAL/GRC.
- In scope: ISSO/ISSM (operator), System Owner (evidence), SCA/3PAO (SAR), AO/AODR (readiness). Privacy Officer: out of scope.



### Customer-variable inputs

Core schema stable; values, templates, mappings, and policies vary per customer/package.


| Input                     | Customer-specific                                                             | Product handling                                     | Hardware impact (if any)                                         |
| ------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------- |
| Authorization path        | `fisma_agency`, `fedramp`, `dod_rmf`                                          | Templates, fields, validation                        | —                                                                |
| Authoritative tool        | Archer, CSAM, Xacta, eMASS, FedRAMP process                                   | Import/export adapters; GRC remains SoR              | —                                                                |
| OSCAL availability        | Partial/full/malformed models                                                 | Per-model validation; partial import OK              | —                                                                |
| Baseline / tailoring      | Impact level, overlays, tailored controls                                     | Validate format; do not decide scope                 | —                                                                |
| Inheritance               | Common/CSP/customer-responsible controls                                      | Flag unsupported claims                              | —                                                                |
| System metadata           | Boundary, components, flows, owners                                           | `TBD — input missing` for whole-doc drafts           | —                                                                |
| Data handling             | CUI, classification, retention, egress                                        | In-boundary processing; no public-model egress       | Enclave may need dedicated hosts                                 |
| Evidence freshness        | Age thresholds, scan cadence                                                  | Deterministic stale checks                           | —                                                                |
| Scanner inputs            | Qualys, Tenable, SCAP, STIG/SCC, CCRI                                         | Import + control impact translation                  | —                                                                |
| POA&M fields              | FedRAMP / eMASS / agency shapes                                               | Path-aware draft export                              | —                                                                |
| Templates                 | SSP, SAP, SAR, RAR, ConMon, SCR, plans                                        | Customer-approved; explicit TBD gaps                 | —                                                                |
| Prior package             | Prior SSP/POA&M/evidence/ConMon month                                         | Delta, ConMon prep, SCR brief                        | Storage retention                                                |
| Approval policy           | Approvers, writeback destinations                                             | `action_gated` required                              | —                                                                |
| Reference corpus          | NIST, SOPs, overlays, policy                                                  | Approved RAG only                                    | Corpus + embedding size                                          |
| Concurrent analysis SLA   | Packages in parallel                                                          | Queue/worker limits                                  | GPU count, `--max-num-seqs`                                      |
| Full-draft scope          | SSP only vs full catalog                                                      | LLM call volume                                      | GPU time per package                                             |
| Document / diagram intake | PDF/DOCX/XLSX/diagram volume                                                  | Extraction + linking steps                           | GPU + storage per package                                        |
| Model / context           | `gemma-4-31B-it`, 32K+ context; **multimodal image input** for diagram intake | Validated stack; text + image via same on-prem model | VRAM floor (96 GB vs 24-48 GB); vision adds per-diagram GPU time |
| RAG rerank                | On/off                                                                        | Approved corpus                                      | CPU/RAM; Postgres size                                           |
| Retention / air-gap       | Years retained; offline staging                                               | Customer-configured                                  | Shared storage TB                                                |
| HA / RTO                  | Single host vs active-active                                                  | Portal/analyzer HA                                   | Duplicate tiers                                                  |




## Positioning and analogy


| Existing         | Authoritative tool                | Our role                             |
| ---------------- | --------------------------------- | ------------------------------------ |
| Vuln scanning    | Qualys, Tenable, SCAP, STIG tools | Findings -> ATO impact drafts        |
| Controls / POA&M | GRC, eMASS                        | Evidence sufficiency; draft language |
| Evidence         | SharePoint, S3, GRC, repos        | Bounded package read; citations      |
| Authorization    | AO, 3PAO                          | Evidence-bound prep for human review |



| Notable analysis        | ATO accelerator                    |
| ----------------------- | ---------------------------------- |
| SIEM alert              | GRC/scanner/document artifacts     |
| One payload             | One evidence package               |
| Markdown/JSON reports   | Markdown, JSON, OSCAL drafts       |
| Analyst portal + chat   | Evidence portal + package chat     |
| Optional SIEM writeback | Optional GRC/eMASS gated writeback |




## End state and product

```text
evidence package, OSCAL, documents, architecture diagrams
  -> authorization_path + deterministic validation (800-53 Rev 5, classification, dates, links)
  -> document/diagram extraction -> control/evidence/architecture normalization
  -> optional approved-reference RAG -> pre-flight readiness check
  -> structured analysis (path-aware) -> schema/policy checks
  -> markdown / json / OSCAL drafts -> audit record
  -> portal browse + expanded citation-bound chat
  -> optional read-only GRC/scanner/eMASS import -> optional gated writeback
```

- **Product:** ATO Evidence Analysis Portal.
- **Unit of work:** evidence package.
- **Primary output:** assessor-ready control evidence review.
- **Secondary:** full-draft gov docs, AI expansion outputs (pre-flight, consistency, advisor, audience briefs), analysis outputs, readiness deltas.
- **Integration:** read-only first, draft-only second, approval-gated writeback last.

### Extraction vs reasoning (core architecture)

**Minimize what the LLM reasons about.** Use deterministic parsers when the input format is reliable; use the LLM to normalize messy customer-specific shapes into the canonical model, then validate. The LLM is not the final source of truth.

```text
Terraform / SBOM / K8s / IAM / Config / Nessus / SARIF / OSCAL / PDFs / diagrams / GRC JSON (any shape)
  -> Ingest boundary (file type, size, safety)
  -> Extraction layer (deterministic when reliable; LLM normalize when customer shape varies)
  -> Canonical security model (strict internal schema — validated before analysis)
  -> LLM (reasoning only: sufficiency, consistency, narrative drafts)
  -> Reports / OSCAL drafts / portal
```

| Category | Inputs | Handling | LLM? |
| --- | --- | --- | --- |
| **1 — Deterministic** | SBOM, Terraform, CloudFormation, K8s YAML, AWS Config, IAM, SGs, route tables, NACLs, asset inventory, port scans, Nessus XML, SARIF, CVE feeds, OSCAL fields | Parse to structured JSON facts | No |
| **2 — Semi-structured** | Policies, runbooks, architecture docs, Visio/draw.io, PDFs, SOPs, **variable GRC exports** | Deterministic extract when possible; **LLM normalize to fact records** when shape varies | Normalize/extract yes; not full doc in reasoning prompt |
| **3 — Reasoning** | Canonical model + control list + evidence index | Sufficiency, consistency, SSP narrative, POA&M wording, gap/contradiction analysis | Yes |

**Canonical security model** (per package): normalized facts the LLM consumes — not raw YAML, full PDFs, or 250k-token dumps. Target: security/architecture/policy **facts** + evidence index + controls in scope. Million-token context is headroom, not the design center. Missing facts remain `TBD — input missing`; the LLM may not infer official scope or evidence.

**Full-draft docs:** assemble from matrix + canonical model (deterministic templates/OSCAL); LLM for narrative glue and assessor-facing wording — not regenerating every control or re-reading raw infra files per call.

**Baseline / control list:** deterministic lookup from impact + path + overlays (Category 1); optional LLM for tailoring **rationale** only (Category 3). Official selection stays GRC/eMASS.

**Pragmatic rule (accuracy over purity):** Same posture as **notable analysis** — customers do not ship one fixed schema. At the boundary: **file type, size, and safety checks only**. Use **deterministic parsers when the format is reliable** (SARIF, Nessus XML, OSCAL XML, etc.). Use **LLM normalize/extract** when shape varies (GRC JSON export, PDF policy, messy bundle) — then validate structured output before analysis. Strict schema applies to the **internal canonical model**, not customer uploads.

**Notable parallel:**

| Notable | ATO |
| --- | --- |
| `.json` / `.txt`, arbitrary alert shape | Allowed file types + size; arbitrary GRC/evidence bundle shape |
| Raw payload preserved; LLM analyzes | Raw inputs preserved; LLM **normalizes** to canonical model |
| Structured **output** validated | Canonical model + matrix **output** validated |
| Optional field hints for portal (`correlation_id`, etc.) | Optional hints; never required for ingest |

**Block mapping:** Block 1 = flexible ingest + LLM normalize to canonical model + matrix reasoning. Block 3+ = more deterministic adapters where formats are stable. Later blocks = canonical model feeds full drafts and chat.

## Evidence portal UI

Mirror analyst portal pattern: calm read-only SPA, sidebar nav, list+detail+chat.


| Layer   | Choice                                                                   |
| ------- | ------------------------------------------------------------------------ |
| UI      | React (TS), Vite, Tailwind v4, shadcn/ui, react-router-dom, lucide-react |
| Reports | react-markdown + sanitize; OpenAPI client                                |
| Tests   | Vitest + Playwright (nginx-served)                                       |


**Routes:** `/` home; `/packages` list; `/packages/:id` detail (matrix, drafts, citations); package chat in detail pane.

**UX:** Read-only default; writeback only with `action_gated`; show path/baseline/impact/classification; label all AI/draft content; fail-visible empty/error states; tabs: **summary**, **readiness** (assessor checklist), control matrix, draft artifacts, evidence index (with search), architecture/boundary; package chat supports **expanded bounded intents** (explain, compare, draft language, gap advisor — not authorization decisions).

### Package run summary (analysis header)

Every package detail view opens with a **run summary** — deterministic rollups from the latest analysis run for that package. This is **not** a live GRC or continuous-compliance dashboard; it reflects one bounded evidence snapshot plus the most recent analyzer output.

| Portal metric | Source | Meaning |
| --- | --- | --- |
| **Supported** | Matrix row count | Draft: linked evidence appears to substantiate the claim |
| **Partial** | Matrix row count | Draft: evidence exists but gaps, stale items, or weak linkage remain |
| **Unsupported** | Matrix row count | Draft: evidence contradicts or does not show implementation |
| **Insufficient evidence** | Matrix row count | Draft: no linked evidence or too thin to assess |
| **Needs attention** | Derived | `partial` + `unsupported` + `insufficient_evidence` |
| **Evidence in package** | `package_metadata.evidence_count` | Artifacts in this snapshot — not pipeline attestation inventory |
| **Stale evidence flags** | Deterministic pre-matrix | Items past freshness threshold |
| **Validation warnings** | Pre-flight + schema | Broken links, orphans, metadata gaps |

Mandatory banner on summary and matrix tabs: **"Draft analysis readiness — not official control status in GRC, eMASS, or FedRAMP."** Never label these metrics as Passing, Gaps, or Attestations.

Structured JSON field `package_run_summary` (Block 5 portal; additive to Block 1 text `summary`) holds the counts above for API and UI. Block 1 may emit a minimal deterministic rollup from matrix rows without portal work.

### Control matrix (review table)

Use a **review table** pattern familiar from pipeline-native CCM tools, with our semantics:

- Columns: control ID, title (when available), family, sufficiency status, stale flag, gap count
- Filters: All | Supported | Partial | Unsupported | Insufficient evidence | Has stale evidence | Has open gaps
- Default sort: control ID; optional sort by family or needs-attention first
- Row drill-down: finding summary, gaps, assessor questions, citations — same data as report matrix

**Gap clusters (Block 6):** deterministic grouping by control family plus shared gap themes across rows; feeds gap-closure advisor and POA&M draft prep. Advisory only — not official weakness tracking.

### Targeted re-analysis

When the customer adds or replaces evidence and re-ingests the package, the portal offers **Re-analyze controls needing attention** — re-run the sufficiency matrix for rows flagged `partial`, `unsupported`, or `insufficient_evidence` only (plus any controls whose linked evidence changed). Analysis-only: does **not** trigger scanner runs, pipeline jobs, or external tool scans. Full package re-analysis remains the default after material intake changes.

Deferred: scheduled re-analysis on unchanged package without re-upload (`CCM polling` row in deferred scope) — same selective scope, cron-driven.

### Evidence index and search (portal)

**Evidence index tab:** browse all `evidence_items` in the package — ID, title, source type, collected date, stale flag, linked controls, source ref.

**Package-scoped search (Block 5/6):** keyword search over evidence titles and extracted text; optional embedding retrieval for chat and index (one package only — not org-wide GRC search). Helps ISSOs find “where is the access review?” without opening every PDF. Inspired by semantic retrieval in ezRMF/Boundera; scoped to archived package boundary.

### Assessor readiness checklist (Block 6)

Deterministic + bounded LLM pass over the **imported package** before gated export — inspired by Boundera/Paramify “reviewer simulation,” but draft-only:

- Stale or missing evidence on high-weight controls
- Implementation statements with `TBD — input missing` on in-scope controls
- Broken evidence links, orphan artifacts
- SSP claims without linked evidence (from consistency brief)
- Scanner findings contradicting implementation narrative
- OSCAL draft fails schema validation (when `oscal` profile enabled)
- FedRAMP: KSI catalog present but indicators without supporting evidence (when 20x inputs included)

Output: checklist with citations and severity hints — not a pass/fail authorization decision. Portal tab: **Readiness** alongside Summary.

**Backend:** FastAPI on loopback; nginx TLS + SPA + proxy auth (`X-Forwarded-User`, portal secret). Layout: `frontend/evidence-portal/`, `onprem_service/portal_app.py`, nginx/systemd units. AWS GovCloud / Azure Government: same SPA/API; I/O triggers only.

## Deployment


| Phase   | Target           | Notes                                  |
| ------- | ---------------- | -------------------------------------- |
| Initial | On-prem          | Local LLM, file-drop/API, nginx portal |
| Later   | AWS GovCloud     | S3 trigger parity                      |
| Later   | Azure Government | Blob trigger parity                    |


Customer-owned boundary; no multi-tenant SaaS; LLM inside boundary; no default public-model egress. **Exception:** Block 1 development uses OpenAI only for synthetic/redacted non-CUI feasibility testing, before the production on-prem runtime exists.

### On-prem hardware sizing

**Rule:** Size for **concurrent package analysis**, not portal users. Target architecture for one full package (100-300+ controls, full-draft catalog) is ~= **20-35 LLM calls** with batched matrix analysis and template assembly. Keep **40-120 calls** as a conservative benchmark envelope until real package tests prove lower wall time. Heavier per package than notable analysis; lower daily volume.

**Stack:** vLLM (`127.0.0.1:8000`) -> LiteLLM (`4000`) -> analyzer -> portal/nginx/Postgres. Model: `gemma-4-31B-it`. Same on-prem model serves **text and image input** (architecture/diagram vision extraction) — no separate public or SaaS vision API. Baseline profile: `[a6000-96gb-ultra9-285k](llm_notable_analysis_onprem_systemd/docs/operations/deployment/deployment_profiles/a6000-96gb-ultra9-285k.md)`. ATO wall-time benchmarks: not yet recorded (include diagram vision fixtures).


| Dimension            | Small (5-10 users)        | Large enterprise                 |
| -------------------- | ------------------------- | -------------------------------- |
| Portal users         | 5-10 reviewers            | 20-50+                           |
| Active packages      | 1-5                       | 10-50+                           |
| Analysis concurrency | 1-2                       | 4-10 (assessment/ConMon windows) |
| Availability         | Single integrated host OK | Tiered HA                        |


**Small — single integrated host (recommended production):**


| Resource       | Min              | Recommended                                                       |
| -------------- | ---------------- | ----------------------------------------------------------------- |
| GPU            | 1x 80-96 GB VRAM | 1x RTX PRO 6000 96 GB                                             |
| CPU            | 16 cores         | 24 cores (Ultra 9 285K class)                                     |
| RAM            | 128 GB           | 128-256 GB                                                        |
| Storage        | 1 TB NVMe        | 2 TB NVMe (~60 GB model + 100-500 GB packages/reports + headroom) |
| Cost (HW only) | —                | ~USD 18k-35k                                                      |


Start `MAX_WORKERS=1`; vLLM `--max-num-seqs 4` only after LiteLLM benchmarks. **Pilot/lab (not prod):** 8-16 vCPU, 32-64 GB RAM, 500 GB, no GPU or 24 GB + validated smaller model.

**Enterprise — tiered:**


| Tier         | Sizing                                                                                                | Role                             |
| ------------ | ----------------------------------------------------------------------------------------------------- | -------------------------------- |
| A Inference  | 2x 96 GB GPU hosts or 1x dual-H100 (`tensor-parallel-size 2`); 32-64 vCPU, 256 GB RAM, 2 TB NVMe each | 2-10 concurrent packages         |
| B App/portal | 2+ nodes: 8-16 vCPU, 32-64 GB, 200 GB NVMe                                                            | Analyzer, portal API, nginx; HA  |
| C Postgres   | 16-32 vCPU, 64-128 GB, 500 GB-2 TB SSD (+ replica)                                                    | Archive, chat, RAG vectors       |
| D Storage    | 10-50+ TB NAS/object                                                                                  | Packages, reports, audit, corpus |
| Edge         | LB/WAF                                                                                                | In front of nginx                |


Cost (HW only): ~USD 80k-250k+. Dual-GPU draft: `[h100x2-intel-tbd](llm_notable_analysis_onprem_systemd/docs/operations/deployment/deployment_profiles/h100x2-intel-tbd.md)`.

**Validate before purchase:** (1) pick profile, (2) vLLM/LiteLLM benchmark on target GPU, (3) one representative full-draft package E2E, (4) measure p50/p95 wall time and disk, (5) then raise workers/GPU count. Notable refs: `[AIRGAPPED_DEPLOYMENT.md](llm_notable_analysis_onprem_systemd/docs/operations/deployment/AIRGAPPED_DEPLOYMENT.md)`, `[LLM_INFERENCE_BENCHMARKING.md](llm_notable_analysis_onprem_systemd/docs/operations/llm/LLM_INFERENCE_BENCHMARKING.md)`.

### Documentation deliverables (on-prem)


| Document                                    | Status       | Notes                                           |
| ------------------------------------------- | ------------ | ----------------------------------------------- |
| Hardware sizing                             | In this plan | Small vs enterprise; validate-before-purchase   |
| `ATO_ONPREM_READINESS_ASSESSMENT.md`        | Planned      | Fork notable readiness assessment               |
| `ATO_ONPREM_INSTALL.md`                     | Planned      | systemd, config.env                             |
| `ATO_ONPREM_AIRGAPPED_DEPLOYMENT.md`        | Planned      | Offline model/corpus staging                    |
| ATO deployment profiles + benchmark         | Planned      | Per-GPU concurrency for full-draft workloads    |
| `ATO_EVIDENCE_PORTAL_NETWORK_DEPLOYMENT.md` | Planned      | nginx/TLS; mirror analyst portal doc            |
| `ATO_EVIDENCE_PORTAL_CHAT_SECURITY.md`      | Planned      | Citation-bound chat guardrails                  |
| Operations index (future ATO package)       | Planned      | Deployment, LLM, portal, OSCAL, ConMon runbooks |


Order: readiness -> install/air-gap -> profiles/benchmark -> portal network/security.

## ATO workflow map (NIST RMF)


| RMF step   | Deliverable               | Owner              | We automate                                      | We do not                 |
| ---------- | ------------------------- | ------------------ | ------------------------------------------------ | ------------------------- |
| Prepare    | Org risk, common controls | ISSO, PMO          | Advisory context                                 | Policy decisions          |
| Categorize | FIPS 199                  | System owner, ISSO | Flag missing metadata                            | Impact determination      |
| Select     | Baseline, tailoring       | GRC, ISSO          | Validate IDs; OSCAL import                       | Official selection        |
| Implement  | SSP                       | ISSO, GRC          | Full draft SSP + boundary from diagrams/evidence | Official SSP in GRC/eMASS |
| Implement  | Supporting plans          | SMEs               | Full draft when requested                        | Approval/storage          |
| Assess     | Evidence linkage          | Control owners     | Sufficiency matrix (analysis)                    | Official GRC linkage      |
| Assess     | SAP                       | Assessor           | Full draft SAP                                   | Official SAP approval     |
| Assess     | SAR                       | SCA/3PAO           | Full draft SAR input pack                        | Official SAR sign-off     |
| Assess     | Scans/STIG                | Scanner            | STIG/CCRI brief -> SAR/POA&M                     | Scan execution            |
| Authorize  | RAR                       | AO staff           | Full draft RAR                                   | Risk acceptance           |
| Authorize  | Auth package              | AO staff           | Readiness package draft                          | AO decision               |
| Monitor    | POA&M                     | GRC, eMASS         | Full draft POA&M export                          | Official POA&M status     |
| Monitor    | ConMon                    | ISSO               | Delta + prep drafts + gated GRC export           | PMO submit; POA&M SoR     |
| Cross      | Q&A                       | Assessor, ISSO     | Citation-bound chat                              | Facts outside package     |




## Full draft document model (locked)

- Path-aware template per `authorization_path`; **provided inputs only**; cite or `TBD — input missing`.
- Mandatory human review before GRC/eMASS publish; AI disclosure; markdown/JSON/OSCAL export; never authoritative on first output.



### Catalog (priority)


| P   | Document                        | RMF       | OSCAL                           | Notes                               |
| --- | ------------------------------- | --------- | ------------------------------- | ----------------------------------- |
| 1   | SSP                             | Implement | `system-security-plan`          | All path sections                   |
| 2   | POA&M export                    | Monitor   | `plan-of-action-and-milestones` | Path-aware GRC import               |
| 3   | SAR input pack                  | Assess    | `assessment-results`            | SCA/3PAO signs official SAR         |
| 4   | Authorization readiness package | Authorize | —                               | AO summary, blockers, checklist     |
| 5   | SAP                             | Assess    | `assessment-plan`               | Scope, methods, sampling, schedule  |
| 6   | ConMon prep pack                | Monitor   | —                               | `fedramp`; GRC feed only (Option 1) |
| 7   | SCR / significant-change brief  | Monitor   | —                               | Primarily `fedramp`                 |
| 8   | RAR                             | Authorize | —                               | No risk acceptance decision         |


**Supporting plans (customer-requested):** IRP, CP/ISCP, CMP, Rules of Behavior, ConMon strategy — full draft + review; do not invent RTO/RPO without inputs.

### Analysis outputs (not standalone deliverables)

Feed full drafts; not official ATO artifacts alone: evidence sufficiency matrix; **package run summary rollups**; **gap clusters by control family**; assessor questions (in SAR pack); STIG/CCRI impact brief; gap-to-owner task export; inheritance/shared-responsibility review; readiness delta; pre-flight readiness; cross-artifact consistency brief; gap-closure advisor notes; assessor response pack; architecture/boundary consistency flags; audience summary views.

## Differentiation, ConMon, deferred scope


| Differentiator       | We do                                           | GRC/scanner gap                           |
| -------------------- | ----------------------------------------------- | ----------------------------------------- |
| Evidence sufficiency | Control review + citations, stale/missing flags | Stores links; rarely judges sufficiency   |
| Full-draft language  | SAR pack, POA&M, SSP, SAP, RAR                  | Templates; weak evidence-driven drafting  |
| Scanner translation  | STIG/SCAP/vuln -> 800-53 impact                 | Findings exist; ATO interpretation manual |
| Package chat         | Citation-bound, one package                     | Generic search/chat                       |
| Path-aware shaping   | FISMA/FedRAMP/DoD from one core                 | Path-specific forms only                  |
| OSCAL round-trip     | Full-draft import/export                        | Varying OSCAL; rarely AI-assisted         |
| Package delta        | Gaps resolved/stale; ConMon prep                | Status tracking; weak narrative delta     |
| Package run summary    | Draft sufficiency rollups per analysis run      | Live pass/gap dashboards; attestation inventory |
| Assessor readiness     | Pre-export checklist on imported package        | Official assessor sign-off or 3PAO portal SoR   |
| Posture              | Draft + human review + gated writeback          | Often positioned as SoR                   |




### Competitive landscape — full RMF / cATO platforms

Reference competitors (not exhaustive): RegScale, ezRMF, Anitian FedFlex, ASSYST ComplySyncATO. These are **platform plays** — continuous compliance, artifact generation, often SoR or near-SoR — not analysis-only layers on top of existing GRC.


| Platform             | What they optimize                                                          | Typical buyer                                             |
| -------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------- |
| RegScale             | Continuous controls monitoring, OSCAL, eMASS exports, cATO                  | Agency/program replacing manual RMF with CCM platform     |
| ezRMF                | DoD CSRMC, AI agents, eMASS-ready packages, IL-5 deploy                     | DoD program wanting end-to-end RMF automation in-boundary |
| Anitian FedFlex      | FedRAMP lifecycle for CSPs; agentic SSP/evidence/ConMon; often infra + 3PAO | SaaS vendor pursuing FedRAMP listing                      |
| ASSYST ComplySyncATO | DevSecOps pipeline compliance, GRC RPA, continuous ATO                      | ISSO tying CI/CD scans to GRC controls                    |


### Competitive landscape — pipeline-native CCM (TestifySec pattern)

Reference: TestifySec and similar — CI/CD observer, cryptographically signed attestations, continuous pass/gap control dashboard, OSCAL SSP generation, auditor assistant from an evidence base.


| Tool / pattern | What they optimize | Typical buyer |
| --- | --- | --- |
| TestifySec | SDLC attestations mapped to 800-53 pass/gap; OSCAL SSP; re-scan gap controls | FedRAMP CSP / DevSecOps-mature product team |
| Vanta / Drata / Secureframe (as sinks) | Pipeline tools push signed evidence into GRC | SaaS vendor continuous compliance |


**What we adopt (analysis-only, not their platform model):** see **Patterns borrowed across the market** table below (TestifySec rows included there).

**What we do not adopt:** CI/CD observer, attestation signing, continuous compliance SoR, live control pass/fail from telemetry, framework-as-product workspace, triggering external scans.

**Positioning vs TestifySec:** They **generate** pipeline evidence continuously. We **analyze** whatever the customer already exported — policies, scans, OSCAL, attestation bundles, diagrams — in one bounded package. Complementary when the customer exports TestifySec or OSCAL output into our intake path; not a replacement for their pipeline collector.

### Competitive landscape — OSCAL-native and FedRAMP 20x tools

Reference: Boundera, Paramify, NISTCompliance.AI (Quzara), RegScale 20x prototypes — OSCAL/SSDR generation, KSI validation, reviewer simulation, dual human+machine export. RegScale also appears in full RMF platforms above; listed here for **documentation and 20x** patterns.


| Tool | What they optimize | Typical buyer |
| --- | --- | --- |
| Boundera | KSI evidence mapping, cloud-signal collection, OSCAL packages, gap analysis | FedRAMP 20x CSP with strong cloud integrations |
| Paramify | SSDR/OSCAL generation, KSI validation, MCP evidence automation, trust center | FedRAMP CSP pursuing 20x path |
| NISTCompliance.AI | AI SSP/POA&M across 800-53, cross-framework mapping, auditor co-pilot | Multi-framework agency or CSP |
| RegScale (20x) | KSI catalog import, OSCAL SSP export, OSCAL CLI validation loop | cATO / OSCAL-first programs |


**What we do not adopt from this category:** live cloud/IdP polling, KSI validation schedulers, MCP evidence collectors, SSDR as system of record, trust-center hosting, auto-submit to FedRAMP PMO.

### Patterns borrowed across the market (ours — analysis-only)

Good ideas from TestifySec, RegScale, Boundera, Paramify, ezRMF, NISTCompliance.AI, and similar tools — reframed for **imported package analysis**, not platform replacement.


| Market pattern | Who does it | Our adaptation | Block |
| --- | --- | --- | --- |
| Run summary KPIs | TestifySec, RegScale dashboards | **Package run summary** — draft sufficiency rollups per analysis run | 5 |
| Control table + gap filter | TestifySec, RegScale | **Control matrix** with family/status/stale/gap filters | 5 |
| Re-check failed controls | TestifySec | **Targeted re-analysis** after customer re-ingests evidence | 5 |
| Attestations as evidence | TestifySec | **Read-only intake** of exported attestation bundles | 3 |
| Reviewer / 3PAO simulation | Boundera, Paramify | **Assessor readiness checklist** — common rejection patterns on imported package before export | 6 |
| OSCAL validate before submit | RegScale, Boundera toolkit, Paramify | **OSCAL validate-before-export** — schema + semantic checks on draft exports | 2 / 7 |
| FedRAMP 20x KSI rollup | Boundera, Paramify, RegScale | **KSI readiness rollup** — map matrix + evidence to imported KSI catalog (`fedramp` only); no live KSI polling | 2+ |
| Suggested evidence-to-control links | Boundera, ezRMF | **Link suggestions** on document intake; human confirms before matrix | 3 |
| Semantic evidence retrieval | ezRMF | **Package-scoped evidence search** — index + chat retrieval over one archived package | 5 / 6 |
| What changed since last package | Boundera, RegScale ConMon | **Package delta report** — control, evidence, and matrix diffs vs `prior_package_id` | 7 |
| Dual machine + human export | Paramify | **Paired export** — OSCAL JSON/XML plus ISSO-readable markdown (optional HTML) from same run | 4 / 7 |
| Auditor co-pilot / walkthrough | NISTCompliance.AI | **Assessor walkthrough pack** — cited Q&A trail for 3PAO/SCA review sessions | 6 |
| Narrative completeness gaps | Boundera | **Implementation narrative flags** — missing required elements in statements vs control requirement (RAG-grounded) | 6 |
| Path-specific intake checklist | Paramify intake, Vanta | **Pre-flight upload checklist** — path-aware “still missing before full run” list | 2 |
| STIG/CCI → control narrative | ezRMF | **Scanner-to-ATO brief** (existing) — finding impact language for SAR/POA&M | 4 |
| “What's blocking ATO?” chat | TestifySec, Boundera | **Package chat** gap-advisor intent with citations | 6 |


**What we never adopt from any vendor category:** CI/CD/cloud observers, attestation signing, continuous compliance SoR, live pass/fail posture, eMASS/GRC bidirectional sync as SoR, agent swarms with write access, environment discovery, scan execution, FedRAMP PMO submit, trust-center hosting.

**Where full RMF / cATO platforms miss (our wedge):**


| Gap                                          | Why platforms miss it                                                                                       | What we do instead                                                                                         |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **No replace-GRC option**                    | They want to be (or become) the compliance system; migration cost is high                                   | Read-only import from Archer/CSAM/Xacta/eMASS/Qualys; customer SoR unchanged                               |
| **Evidence sufficiency, not checkbox state** | Strong at control status, tests, continuous monitoring; weak at “does this PDF/scan actually prove AC-2?”   | Control-by-control sufficiency matrix, stale/missing/contradictory flags, citations                        |
| **Messy real-world evidence**                | Optimized for their model, integrations, or telemetry — not arbitrary ISSO-uploaded packages                | PDF/DOCX/XLSX + diagrams + OSCAL + scanner exports in one bounded package                                  |
| **Assessor-facing analysis**                 | Generate SSP/SAR/POA&M from **their** data model; less focus on assessor Q&A prep                           | SAR input pack, assessor questions, assessor **response** pack, SAP test-focus from gaps                   |
| **Cross-artifact contradiction**             | Documents stay internally consistent; less narrative reconciliation across SSP vs scan vs policy vs diagram | Explicit consistency brief: implementation claim vs evidence vs scanner vs POA&M                           |
| **Imported authorization package**           | Assume greenfield or ongoing sync into **their** platform                                                   | Analyze what the customer **already has** in GRC/repos/scanners for one assessment cycle                   |
| **On-prem / air-gap / CUI-first**            | Often SaaS, bundled GovCloud, or platform-in-your-account with their ops model                              | On-prem first; local LLM; customer boundary; no default public-model egress                                |
| **Path-agnostic analysis core**              | Often FedRAMP-CSP or DoD-program shaped; multi-path from same **imported** package is secondary             | One engine; `fisma_agency` / `fedramp` / `dod_rmf` shapes drafts only                                      |
| **ConMon without owning ConMon**             | cATO platforms want continuous monitoring **inside** the product (Option 2 shape)                           | Option 1: delta + drafts + gated export to **existing** GRC/FedRAMP process                                |
| **ISSO review portal, not GRC dashboard**    | Workflow, POA&M boards, program dashboards                                                                  | Calm read-only portal: matrix, drafts, citations, expanded chat — notable-analysis pattern                 |
| **Scanner-to-ATO narrative**                 | Findings ingested for control pass/fail; weak “what does this STIG finding mean for SSP/SAR?”               | STIG/CCRI impact brief mapped to 800-53 for SAR/POA&M drafts                                               |
| **Architecture from customer diagrams**      | Boundary from CMDB/IaC/model inside platform                                                                | Diagram/image/structured intake **now**; SSP boundary check against cited extraction                       |
| **Pre-flight before expensive runs**         | Assume platform data is ready                                                                               | Completeness score + block/warn before multi-call package analysis                                         |
| **Human review as first-class output**       | AI drafts feed **their** workflow state                                                                     | Every output draft-only; explicit review; `action_gated` export to GRC — never authoritative on first pass |


**Positioning line vs category 1:** They sell “run your ATO on our platform.” We sell “analyze the package you already have, faster and more consistently, then push drafts back to the tools you already own.”

**Not a direct fight when:** customer has no GRC, wants cATO platform + CCM, pursues greenfield FedRAMP CSP with pipeline-native evidence (TestifySec / Anitian / Paramify territory), or buys infra + 3PAO + authorization path bundled.

**Direct fight when:** ISSO/SCA already lives in Archer/eMASS/CSAM; assessor questions evidence quality; team has scanner + policy PDFs + partial OSCAL and needs **review-ready drafts** without replatforming.

**Also in scope:** inheritance analysis; supporting-plan drafts; SCR full draft; gap-to-owner export; ConMon prep + GRC feed (`fedramp`); AI expansion capabilities below (document/diagram intake, pre-flight, consistency, chat modes, etc.).

## AI expansion (in scope)

Assistive, evidence-bound, draft-only. Deterministic validation before and after every LLM step. No authoritative decisions.


| P   | Capability                    | Output                                                                              | Guardrails                                              |
| --- | ----------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 1   | Document ingestion            | PDF/DOCX/XLSX -> bounded `evidence_items`; **proposed control link suggestions** for human confirm | Extract/cite source doc; no invented policy text        |
| 2   | Pre-flight readiness          | Completeness score, block/warn before full run, **path-aware upload checklist**     | Deterministic checks first; LLM explains gaps only      |
| 3   | Cross-artifact consistency    | SSP vs evidence vs scans vs POA&M contradiction brief                               | Citations only; feeds SAR/POA&M drafts                  |
| 4   | Gap-closure advisor           | Per-gap: evidence types needed, assessor Q hints, draft remediation steps           | Advisory; not official POA&M until approved             |
| 5   | Assessor response pack        | Map inbound assessor comments -> controls/evidence; draft response language         | Human review; no authoritative rebuttal                 |
| 6   | SAP test-focus optimization   | Risk-ranked assessment focus, sampling hints from gap matrix                        | Draft SAP input; assessor owns official SAP             |
| 7   | ConMon / SCR intelligence     | **Package delta report** (control/evidence/matrix diffs), ConMon narrative, POA&M update suggestions, SCR trigger hints | Option 1 only; no PMO submit or POA&M SoR               |
| 8   | Audience-specific summaries   | ISSO working, AO readiness, assessor prep brief (same cited facts)                  | No new facts; labeled draft views                       |
| 9   | Expanded package chat         | Multiple bounded chat intents (see below)                                           | One package; citations required; refuse auth decisions  |
| 10  | Architecture / diagram intake | Boundary/component/flow extraction for SSP + consistency checks                     | Intake now; cite diagram artifact; no invented topology |
| 11  | Assessor readiness checklist  | Pre-export rejection-pattern flags with citations                                   | Draft checklist only; not assessor sign-off             |
| 12  | Implementation narrative flags | Missing required elements in implementation statements vs control requirement       | RAG-grounded; cite requirement text; feeds SSP draft    |
| 13  | Assessor walkthrough pack     | Cited Q&A trail for 3PAO/SCA review sessions                                        | Human-led session; AI prepares cited talking points       |
| 14  | KSI readiness rollup          | Map sufficiency matrix + evidence to imported KSI catalog (`fedramp` / 20x inputs)   | No live KSI polling; imported catalog + evidence only   |
| 15  | Package-scoped evidence search | Keyword + optional embedding retrieval over one archived package                 | No cross-package or GRC-wide search                     |
| —   | Enhanced reference RAG        | Overlay-aware control interpretation; cite 800-53/agency/path template clauses      | Approved corpus only; no open web                       |




### Package chat (item 9 — expanded, not one rigid mode)

**Current posture:** citation-bound Q&A over **one archived package** — not global search, not GRC SoR chat.

**In scope expansion:** same guardrails, **more operator intents** via explicit modes or intent routing (all package-scoped):


| Intent                                              | Allowed                        | Refuse       |
| --------------------------------------------------- | ------------------------------ | ------------ |
| Explain finding / control                           | Yes, with evidence citations   | —            |
| What evidence supports control X?                   | Yes                            | —            |
| Draft POA&M / SAR / remediation language for gap    | Yes (draft)                    | Auto-publish |
| Compare current vs prior package (control/evidence) | Yes, when prior package linked | —            |
| What would close this gap?                          | Yes (advisor; item 4)          | —            |
| Summarize for AO / assessor audience                | Yes (item 8)                   | —            |
| Authorize / accept risk / mark compliant            | —                              | Always       |


Chat remains **assistive**: proposes language and summaries; humans approve before GRC/export. Not an unconstrained agent.

### Architecture / diagram intake (item 10 — now)

Required for SSP boundary sections and consistency analysis. Intake at package ingest — not deferred. **Read customer-provided diagrams only — never generate or render new architecture images.**

**Accepted inputs:**


| Format                   | Handling                                                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| PNG, JPEG, WebP, SVG     | Bounded vision/text extraction -> `architecture_artifacts[]` with `diagram_id`, caption, extracted text, components[], flows[] |
| PDF (architecture pages) | Page extract -> same artifact model                                                                                            |
| Structured export        | JSON/XML/text from draw.io, Lucid, Visio export, CMDB boundary export -> normalize to artifact model                           |


**On-prem vision path:** raster/SVG/PDF-page diagrams sent to `gemma-4-31B-it` via vLLM multimodal API (in-boundary); structured exports skip vision when parse succeeds. No external vision model or egress.

**Minimum artifact fields:** `diagram_id`, `title`, `source_path` or upload ref, `collected_at`, `boundary_scope_claim` (from caption/metadata if provided), `extracted_components[]`, `extracted_flows[]`, `extraction_method` (`structured` | `vision` | `text`), `linked_control_ids[]` (optional, human or AI-suggested).

**Analysis uses diagrams to:** validate SSP boundary narrative against cited diagram content; flag components in diagram but missing from SSP (and reverse); support authorization readiness and SCR delta. Mark `TBD — diagram missing or unreadable` when extraction fails — do not invent architecture.

**Limits:** max file size and page count per package (customer-configurable); vision step in-boundary only; store source diagram + extraction audit in package.

### ConMon (locked): Option 1 — feed GRC

Ingest monthly OSCAL/GRC + scans + prior package -> **package delta report** (added/removed/changed controls, evidence items, matrix status transitions) -> ConMon narrative + POA&M **update** drafts -> approval-gated OSCAL/GRC export (`action_gated`). GRC/FedRAMP own POA&M status, cadence, PMO/Marketplace submit, task routing.

Implement: `prior_package_id`; **control-level and evidence-level diffs**; matrix status transitions (e.g. partial -> supported); FedRAMP POA&M updates; ConMon narrative; manual/scheduled re-ingest; no ConMon scheduler as SoR.

### Option 2 — replace ConMon (not chosen)

Would require: POA&M SoR, workflow engine, FedRAMP submission pipeline, SCR workflow, multi-system portfolio, live CCM polling, expanded RBAC, program-year audit, deep GRC vendor matrix, ConMon dashboard UI, legal/SLA ownership. Deferred — separate product line if ever reconsidered.

### Deferred (not now)


| Capability                           | Why not now                                | Consider later when                     |
| ------------------------------------ | ------------------------------------------ | --------------------------------------- |
| Control-owner workflow               | Competes with GRC task engines             | Gap-to-owner -> GRC handoff proven      |
| Enterprise portfolio dashboard       | Package-scoped product                     | Single-system value proven              |
| Live scan execution                  | Scanner/assessor owns execution; liability | Read-only import insufficient           |
| Official risk register / AO sign-off | AO/GRC owns decisions                      | Richer draft RAR packaging              |
| Privacy / PIA                        | Out of scope; different role/gates         | Privacy-heavy agency systems            |
| SCRM (800-161)                       | Specialized domain                         | Vendor evidence analysis demand         |
| Assessor collaboration threads       | Overlaps GRC/assessor portals              | High 3PAO review volume                 |
| CMDB inventory cross-check           | No CMDB SoR integration                    | SSP vs scan inventory mismatches        |
| CCM polling                          | Snapshot import simpler/safer              | Scheduled **targeted re-analysis** (needs-attention controls only) without full re-upload |
| eMASS-native beyond OSCAL            | OSCAL-first DoD path                       | eMASS field parity required             |
| Cross-framework mapping (CMMC, SOC 2 from one base)                  | Path-specific shaping sufficient for now   | Multi-framework agency demand           |
| Org-wide common controls             | Exceeds single-package scope               | Agency PMO use case                     |
| FedRAMP auto-submit                  | Blurs into Option 2                        | Never under current posture             |
| Full ConMon platform (Option 2)      | See ConMon section                         | Product pivot only                      |




## OSCAL interop

**Fixed model names (not customer-specific):** `system-security-plan` (SSP), `assessment-plan` (SAP), `assessment-results` (SAR inputs), `plan-of-action-and-milestones` (POA&M). Path varies **field content**, tool behavior, extensions, which models exist — not model type names. Partial import OK; fail per model.

**Import:** OSCAL JSON/XML for all four models -> internal package; preserve citations. Optional FedRAMP 20x **KSI catalog** JSON as reference input for KSI readiness rollup (`fedramp` path).

**Export:** Full-draft OSCAL for all four; mark draft/machine-generated; round-trip GRC -> analyze -> export -> human edit. **Paired export:** same run produces OSCAL plus ISSO-readable markdown (optional HTML profile) — human and machine formats stay in sync from one analysis.

**Validate-before-export (Block 2/7):** Before `action_gated` writeback, run OSCAL schema validation (NIST OSCAL schemas / CLI-compatible checks) and package-specific semantic rules (required fields for path, broken back-mrefs). Fail export with actionable errors; do not ship invalid OSCAL. Inspired by RegScale/Boundera validation loops; validation is deterministic — not LLM.

**Alternate intake:** custom JSON manifest; evidence documents; architecture diagrams; attestation exports (see Evidence package contract).

## Data, governance, audit


| Requirement           | Rule                                              |
| --------------------- | ------------------------------------------------- |
| Classification        | Package declares max CUI/classification           |
| Boundary              | Processing in customer enclave; no default egress |
| Evidence vs inference | Source text = evidence; LLM output = inference    |
| Retention             | Customer-configured                               |


**Required metadata:** `authorization_path`, `baseline` (800-53 R5), `impact_level`, `data_classification`, `system_name`, `authorization_boundary`, `assessment_date`, `controls[]`, `evidence_items[]`, optional `architecture_artifacts[]`, `overlays[]`, `customer_input_profile` (templates, field maps, freshness thresholds, corpus).

**AI governance:** human-in-the-loop; no training on customer data; approved RAG only; structured output + validation/repair; failed validation kept for review; AI disclosure in every report.

**LLM allowed:** summarize/compare evidence; gaps; assessor Qs; full-draft docs; scanner impact; document/diagram **extraction** (read customer uploads); consistency/contradiction analysis; gap-closure advisor; assessor response drafts; audience summaries; expanded chat intents (draft language only). **Not allowed:** invent evidence or architecture; **generate images or architecture diagrams**; authoritative compliance; override validation; official records; approve risk/auth; out-of-package facts.

**Audit (lightweight):** per run — `package_id`, path, timestamp, operator, model/profile, input hash, output paths, validation warning count; append-only log.

**Deterministic logic:** schema/path validation; 800-53 ID format; dedupe IDs; dates/stale evidence; link normalization; OSCAL parse/export; path-aware field mapping; structured LLM validation; audit write.

## Capabilities and profiles


| Capability                                                          | RMF               | Deliverable                                             |
| ------------------------------------------------------------------- | ----------------- | ------------------------------------------------------- |
| Evidence sufficiency review                                         | Assess            | Matrix -> SAR feed                                      |
| Scanner-to-ATO brief                                                | Assess            | STIG/SCAP/CCRI -> SAR/POA&M                             |
| Full draft SSP / POA&M / SAR / SAP / RAR / readiness / ConMon / SCR | Implement-Monitor | See catalog                                             |
| Supporting-plan drafts                                              | Implement         | IRP, CP/ISCP, CMP, RoB, ConMon strategy                 |
| Inheritance analysis                                                | Select/Implement  | Gap flags                                               |
| Readiness delta                                                     | Monitor           | ConMon input                                            |
| Gap-to-owner export                                                 | Assess/Monitor    | Remediation handoff                                     |
| Document ingestion                                                  | Assess/Implement  | PDF/DOCX/XLSX -> evidence items + link suggestions      |
| Architecture / diagram intake                                       | Implement         | Boundary/component/flow artifacts for SSP + consistency |
| Pre-flight readiness                                                | Assess            | Completeness score; block/warn before full run          |
| Cross-artifact consistency                                          | Assess/Monitor    | Contradiction brief (SSP, evidence, scans, POA&M)       |
| Gap-closure advisor                                                 | Assess            | Evidence needed, remediation draft steps                |
| Assessor response pack                                              | Assess            | Draft responses to inbound assessor comments            |
| Assessor readiness checklist                                        | Assess/Authorize  | Pre-export rejection-pattern flags with citations       |
| Assessor walkthrough pack                                           | Assess            | Cited Q&A trail for 3PAO/SCA sessions                   |
| Implementation narrative flags                                      | Implement/Assess  | Missing elements in statements vs requirement           |
| KSI readiness rollup                                                | Assess (`fedramp`)| Matrix mapped to imported KSI catalog                   |
| Package delta report                                                | Monitor           | Control/evidence/matrix diffs vs prior package        |
| Package-scoped evidence search                                      | Assess            | Index + retrieval over one archived package             |
| OSCAL validate-before-export                                        | Implement/Monitor | Schema + semantic gate before gated writeback           |
| SAP test-focus optimization                                         | Assess            | Risk-ranked focus areas for SAP draft                   |
| ConMon / SCR intelligence                                           | Monitor           | Delta narrative, POA&M suggestions, SCR hints           |
| Audience summaries                                                  | Authorize         | ISSO / AO / assessor brief views                        |
| Package chat (expanded intents)                                     | Assess/Authorize  | Citation-bound Q&A + draft/compare/advisor modes        |
| Read-only import                                                    | Assess/Monitor    | GRC, eMASS, scanners                                    |
| Draft / gated writeback                                             | Implement/Monitor | Approved payloads only                                  |



| Profile                                                | Enables                                                             |
| ------------------------------------------------------ | ------------------------------------------------------------------- |
| `core`                                                 | Analysis, markdown, JSON, audit                                     |
| `oscal`                                                | OSCAL import/export                                                 |
| `html_reports`                                         | Optional HTML                                                       |
| `reference_rag`                                        | Approved NIST/agency/overlay grounding (enhanced overlay citations) |
| `document_intake`                                      | PDF/DOCX/XLSX extraction to evidence items                          |
| `architecture_intake`                                  | Diagram/image/structured boundary ingest + extraction               |
| `package_archive`                                      | History, index                                                      |
| `evidence_portal`                                      | Read-only SPA                                                       |
| `package_chat`                                         | Expanded bounded chat intents over one package                      |
| `scanner_readonly` / `grc_readonly` / `emass_readonly` | Imports                                                             |
| `ticket_draft`                                         | POA&M/ticket payloads                                               |
| `conmon_grc_feed`                                      | ConMon delta, POA&M updates, narrative, gated export (`fedramp`)    |
| `oscal_validation`                                     | Schema + semantic gate before gated OSCAL export                    |
| `action_gated`                                         | Approval-gated writeback                                            |




## Evidence package contract

**Preferred / canonical intake paths:** External customer uploads may be arbitrary `.json` / `.txt` or customer-specific exports; these paths describe the canonical package layout after normalization, or the recommended structured manifest when customers can provide one.

```text
incoming/<package_id>.{json,json.gz,oscal.json,oscal.xml}
incoming/<package_id>/evidence/*.{pdf,docx,xlsx,txt,md}
incoming/<package_id>/architecture/*.{png,jpg,jpeg,webp,svg,pdf,json,xml}
```

**Control fields:** `control_id`, `control_title`, `control_requirement`, `implementation_statement`, `linked_evidence_ids[]`.

**Evidence fields:** `evidence_id`, `title`, `source_type`, `source_owner`, `collected_at`, `text` or OSCAL ref; optional `source_document_ref`, `page_or_section`, `extraction_method`.

**Attestation export intake (read-only, Block 3+):** Customers may include exported pipeline attestations as evidence — not collected by us. Accept JSON, OSCAL fragments, or signed-bundle exports the customer drops into `evidence/`. Set `source_type` to one of: `pipeline_attestation`, `signed_attestation_bundle`, `in_toto_statement`. Normalize to bounded text + metadata for matrix reasoning; preserve `source_document_ref` for auditor traceability. No CI/CD observer, no signing, no webhook collector.

**KSI catalog intake (optional, `fedramp` / 20x):** Customer may include FedRAMP 20x KSI catalog JSON (or OSCAL conversion) as reference metadata for rollup analysis — not live validation. Store as `ksi_catalog_ref` on package; KSI readiness rollup maps matrix rows and evidence to indicator IDs. No cloud polling or scheduled KSI re-validation.

**Architecture artifact fields:** `diagram_id`, `title`, `source_type`, `collected_at`, `boundary_scope_claim`, `extracted_components[]`, `extracted_flows[]`, `extraction_method`, `linked_control_ids[]`, optional `source_document_ref`.

**Outputs:** `reports/<id>.{md,json,oscal.json}`, `audit/<id>-<run_id>.json`.

**Report sections:** summary, **`package_run_summary`** (structured rollups for portal), AI disclosure, pre-flight readiness, **upload checklist**, readiness, **assessor readiness checklist**, evidence matrix, **implementation narrative flags**, **gap clusters** (Block 6), **KSI readiness rollup** (when catalog present), architecture/boundary consistency, stale/missing/contradictory evidence, cross-artifact consistency brief, scanner impact, full-draft index, assessor Qs, assessor response pack (when provided), **assessor walkthrough pack** (optional), inheritance, supporting-plan status, **package delta report** (if prior package), SCR/ConMon summary (if prior package), gap-to-owner export, audience summaries (optional), citations, validation warnings, **oscal_validation** (when export attempted).

## Product boundaries

Qualys/Tenable/STIG: technical findings. GRC/eMASS: official records. Repos: official evidence. Portal: what evidence supports, what's missing, what to review next. ATO process remains; we accelerate analysis within it.

## User decisions required

- End-state product: ATO Evidence Analysis Portal.
- Paths: `fisma_agency`, `fedramp`, `dod_rmf`. Baseline: 800-53 Rev 5.
- Deploy: on-prem first; AWS GovCloud and Azure Government later.
- OSCAL primary interop; portal stack React/Vite/Tailwind/shadcn.
- ConMon Option 1 (feed GRC); not Option 2.
- Full draft model + mandatory human review before GRC/eMASS publish.
- AI expansion in scope: document ingestion, diagram intake (now), pre-flight, consistency, gap advisor, assessor response pack, **assessor readiness checklist**, **assessor walkthrough pack**, **implementation narrative flags**, **KSI readiness rollup**, **package-scoped evidence search**, **OSCAL validate-before-export**, **paired export**, SAP focus, ConMon/SCR intelligence, audience summaries, expanded package chat intents, enhanced RAG.
- On-prem hardware sizing in this plan (small single-host vs enterprise tiered).
- Out of scope: privacy, AO/3PAO sign-off, ungated writeback, FedRAMP PMO submit, ConMon Option 2.



## Open items

- [ ] **Block 1 implementation:** scaffold code in this project folder per [`ATO_BLOCK1_TECHNICAL_SPEC.md`](ATO_BLOCK1_TECHNICAL_SPEC.md) (includes synthetic golden fixture spec).
- [ ] **Block 5 portal:** package run summary header, control matrix filters, targeted re-analysis, evidence index search (spec in Evidence portal UI section).
- [ ] **Block 3 intake:** attestation export adapter; document link suggestions on ingest.
- [ ] **Block 6 analysis:** assessor readiness checklist, implementation narrative flags, assessor walkthrough pack, gap clusters, package-scoped evidence search.
- [ ] **Block 2/7 OSCAL:** validate-before-export gate; paired OSCAL + markdown export; optional KSI catalog intake for `fedramp`.
- [ ] **Block 7 ConMon:** package delta report (control/evidence/matrix diffs vs `prior_package_id`).
- [ ] ATO package wall-time benchmark on `a6000-96gb-ultra9-285k` (include document + diagram intake fixtures).
- [ ] ATO deployment profiles (full-draft + expanded AI concurrency defaults).
- [ ] Architecture intake limits (max diagram size/pages) and vision extraction validation fixtures.
- [ ] Enterprise tier diagram.
- [ ] Remaining on-prem operator docs (see Documentation deliverables).