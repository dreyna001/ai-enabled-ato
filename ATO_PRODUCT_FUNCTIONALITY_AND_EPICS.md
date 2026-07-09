# ATO Evidence Analysis Portal — Functional Descriptions, Epics, and User Stories

Literal prose for each planned capability: what the operator provides, what the product does step by step, and what comes out. Written to mirror manual ISSO/assessor work so an ATO SME can map product behavior to existing workflow.

**Posture (all capabilities):** assistive, evidence-bound, draft-only. The product does not grant ATO, accept risk, mark controls officially compliant, or replace GRC/eMASS/scanners as system of record.

**Build reference:** Block numbers match [`ATO_BLOCK1_TECHNICAL_SPEC.md`](ATO_BLOCK1_TECHNICAL_SPEC.md) and [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md).

---

## Part 1 — Functional descriptions (manual workflow mirror)

### 1. Evidence package intake and normalization

**Manual workflow today:** The ISSO exports controls, implementation statements, and evidence references from Archer/CSAM/eMASS or assembles them by hand. Evidence arrives as GRC JSON, OSCAL fragments, policy PDFs, scan exports, and ad hoc spreadsheets — each customer uses a different shape. The ISSO manually reconciles IDs, copies text into working documents, and builds a working set for one assessment cycle.

**Input:** A bounded evidence package for one system and one assessment snapshot. Block 1 accepts `.json` or `.txt` of any internal shape. Full product accepts a package folder: manifest JSON, `evidence/` (PDF, DOCX, XLSX, txt), `architecture/` (diagrams), OSCAL files, scanner exports (Qualys, Tenable, STIG/SCC, CCRI), optional attestation bundles, optional prior-package snapshot.

**What the product does:**
1. Applies file-type and size checks at the ingest boundary; rejects path traversal; quarantines unreadable files.
2. Preserves the raw upload unchanged under `processed/<package_id>/raw/` for audit.
3. If the upload is a known structured format (OSCAL XML, Nessus XML, SARIF, etc.), runs a deterministic parser when available.
4. If the upload is a variable GRC export or messy bundle, runs an LLM normalize step to map fields into the canonical internal model: `package_metadata`, `controls[]` (ID, implementation statement, linked evidence IDs), `evidence_items[]` (ID, title, source type, collected date, extracted text).
5. Validates the normalized model against schema before any analysis proceeds.

**Outcome:** A single canonical package record the rest of the pipeline consumes. The operator does not need to pre-format data to our schema. Raw customer export plus normalized canonical JSON on disk.

**Does not:** Sync live from GRC, poll scanners, or invent controls or evidence not present in the upload.

**Block:** 1 (JSON/txt normalize); 3 (documents, diagrams, scanners, attestations).

---

### 2. Package validation and pre-flight readiness

**Manual workflow today:** Before spending assessor or ISSO time on a full review, someone checks whether the package is structurally complete: control IDs valid, evidence links resolve, dates present, authorization path and impact level declared, broken references flagged.

**Input:** Canonical package after normalize (or direct canonical upload).

**What the product does:**
1. Runs deterministic validation: 800-53 control ID format, duplicate IDs, required metadata (`authorization_path`, `baseline`, `impact_level`, `data_classification`, `system_name`, `assessment_date`), evidence link integrity (every `linked_evidence_id` exists), orphan evidence detection.
2. Computes evidence freshness: compares `collected_at` to customer-configured thresholds; flags stale items.
3. Computes a pre-flight completeness score and path-aware upload checklist (what is still missing for a full analysis run).
4. If score is below `PREFLIGHT_BLOCK_THRESHOLD`, blocks the expensive multi-call analysis and returns actionable warnings. Otherwise proceeds with warnings attached.

**Outcome:** Validation report section in markdown/JSON output; pre-flight score; upload checklist; block or proceed decision. Operator fixes package and re-ingests before wasting LLM cycles.

**Does not:** Decide official control baseline scope or impact level — only validates what was declared.

**Block:** 1 (core validation + preflight); 2 (path-aware upload checklist for FedRAMP/DoD).

---

### 3. Evidence sufficiency matrix (control-by-control review)

**Manual workflow today:** The ISSO or assessor opens each in-scope 800-53 control, reads the implementation statement in the SSP, opens each linked piece of evidence (policy, scan, ticket, log review record), and judges whether the evidence actually substantiates the claim. They note gaps, stale items, contradictions, and draft finding language. This is the core labor of assessment prep.

**Input:** Validated canonical package with controls, implementation statements, linked evidence items (text extracts), and metadata (path, impact, assessment date).

**What the product does:**
1. Runs deterministic pre-checks per control row: stale evidence flags, missing linked evidence, empty implementation statements.
2. Batches controls and sends bounded prompts to the LLM with only the control requirement, implementation statement, and linked evidence text — not raw infra dumps or full PDFs.
3. LLM returns structured output per control: sufficiency status (`supported`, `partial`, `unsupported`, `insufficient_evidence`), finding summary, gap list, assessor questions, evidence citations (must reference provided `evidence_id` values).
4. Validates LLM JSON against schema; one repair call on failure.
5. Rolls up deterministic counts: Supported, Partial, Unsupported, Insufficient evidence, Needs attention (derived).

**Outcome:** Evidence sufficiency matrix in markdown and JSON — one row per control with status, findings, gaps, questions, and citations. This is the primary analysis artifact feeding SAR drafts, POA&M drafts, and portal control review. Every report includes AI disclosure stating draft inference only.

**Does not:** Mark a control officially compliant in GRC, eMASS, or FedRAMP. Status labels are draft analysis readiness, not Passing/Gaps in a CCM dashboard.

**Block:** 1.

---

### 4. Stale, missing, and contradictory evidence flags (deterministic)

**Manual workflow today:** Experienced ISSOs scan for recurring assessor rejection patterns before formal review: access reviews older than 12 months, SSP claims with no attached proof, evidence IDs in the SSP that do not exist in the package, scanner results that contradict the narrative.

**Input:** Canonical package and sufficiency matrix output.

**What the product does:**
1. Applies date thresholds to `evidence_items[].collected_at` — flags stale without LLM.
2. Detects implementation statements referencing evidence IDs not in the package.
3. Detects evidence items not linked to any control (orphans).
4. Surfaces matrix rows where status is `unsupported` or evidence text contradicts the implementation claim (matrix LLM output + deterministic link checks).

**Outcome:** Dedicated report sections: stale evidence list, missing evidence list, contradictory evidence flags. Feeds assessor readiness checklist and portal filters ("Has stale evidence", "Has open gaps").

**Does not:** Re-collect evidence or trigger new scans.

**Block:** 1 (stale/missing/links); 6 (cross-artifact contradiction brief adds SSP vs scan vs POA&M).

---

### 5. SSP draft generation

**Manual workflow today:** The ISSO writes or updates the System Security Plan — the master document describing the system boundary, components, data flows, and how each 800-53 control is implemented. They copy implementation statements from GRC, pull boundary descriptions from architecture diagrams, and fill gaps with `TBD` where inputs are missing. This takes weeks for a moderate system.

**Input:** Canonical package (metadata, controls, evidence items, optional architecture artifacts, optional OSCAL SSP import), sufficiency matrix, implementation narrative flags, architecture/boundary consistency results, path-specific SSP template (`fisma_agency`, `fedramp`, or `dod_rmf`).

**What the product does:**
1. Assembles SSP sections deterministically from template + canonical model fields (system name, boundary, impact, control list).
2. For each control section, inserts the provided implementation statement; where narrative is thin or narrative flags fire, LLM drafts assessor-facing wording bounded to cited evidence — does not invent policy text or architecture.
3. Inserts boundary/component/flow content from architecture artifact extraction where linked; marks `TBD — input missing` for required sections without inputs.
4. Includes citations to `evidence_id` and `diagram_id` where claims are substantiated.
5. Exports draft SSP as markdown, JSON, and OSCAL `system-security-plan` (when `oscal` profile enabled).

**Outcome:** Full draft SSP document ready for ISSO edit before official GRC/eMASS update. Not authoritative on first output.

**Does not:** Publish to GRC/eMASS without human approval. Does not generate architecture diagrams.

**Block:** 4.

---

### 6. POA&M draft export

**Manual workflow today:** For each weakness (stale access review, STIG finding, unsupported control), the ISSO opens a POA&M item in GRC/eMASS: weakness description, risk level, milestones, responsible party, scheduled completion, linked controls and evidence. FedRAMP and DoD use different field shapes.

**Input:** Sufficiency matrix gaps, scanner-to-ATO brief findings, cross-artifact consistency contradictions, path-specific POA&M field map, existing POA&M items from OSCAL import (if any).

**What the product does:**
1. Creates one draft weakness row per open gap cluster or matrix row flagged `partial`, `unsupported`, or `insufficient_evidence`, plus high-severity scanner findings mapped to controls.
2. Drafts weakness text, suggested milestones, risk framing, and control mapping using LLM bounded to citations.
3. Applies path-aware field names (FedRAMP vs eMASS vs agency GRC).
4. Exports markdown, JSON, and OSCAL `plan-of-action-and-milestones`.

**Outcome:** Full draft POA&M export the ISSO reviews and imports into GRC/eMASS after approval. Official POA&M status remains in GRC.

**Does not:** Track remediation workflow, send notifications, or update official POA&M status.

**Block:** 4; 7 (ConMon POA&M update drafts from package delta).

---

### 7. SAR input pack (assessor findings draft)

**Manual workflow today:** The Security Control Assessor (SCA) or 3PAO documents findings in the Security Assessment Report: what was tested, what failed or partially failed, severity, control mapping, and evidence references. The ISSO often pre-drafts finding language for internal QA before the assessor formalizes it.

**Input:** Sufficiency matrix, scanner-to-ATO brief, gap clusters, assessor questions from matrix, path template.

**What the product does:**
1. Converts matrix rows with gaps into draft finding records: finding ID, control ID, severity hint, finding summary, evidence citations, recommended assessor follow-up questions.
2. Groups related findings via gap clusters where applicable.
3. Exports as markdown/JSON and OSCAL `assessment-results` draft.

**Outcome:** SAR input pack the SCA/3PAO reviews, edits, and signs. We do not produce the official signed SAR.

**Does not:** Execute assessment tests, sign the SAR, or submit to FedRAMP PMO.

**Block:** 4.

---

### 8. SAP draft generation

**Manual workflow today:** Before testing begins, the assessor writes the Security Assessment Plan: scope, methods, controls to test, sampling approach, schedule, and areas of focus based on system risk and known weak areas.

**Input:** Control list in scope, sufficiency matrix (pre-assessment gaps inform focus), SAP test-focus optimization output, path template.

**What the product does:**
1. Drafts SAP sections: assessment scope, methods, control sample list, schedule placeholders.
2. Ranks controls for test focus based on gap severity, stale evidence, and scanner contradictions (SAP test-focus optimization).
3. Exports markdown/JSON and OSCAL `assessment-plan`.

**Outcome:** Draft SAP for assessor review and official approval. Assessor owns the official SAP.

**Does not:** Execute the assessment or approve the SAP.

**Block:** 4.

---

### 9. RAR draft (Risk Assessment Report)

**Manual workflow today:** AO staff and risk analysts draft the Risk Assessment Report summarizing residual risk, likelihood/impact framing, and risk-related findings to support the authorization decision. The AO makes the decision; the document supports it.

**Input:** Sufficiency matrix rollups, open gaps, POA&M draft summaries, path template.

**What the product does:**
1. Drafts risk statements tied to cited gaps and evidence — no new facts.
2. Frames likelihood/impact language as draft prose for AO staff review.
3. Exports markdown/JSON RAR draft.

**Outcome:** Draft RAR for AO staff edit. No risk acceptance decision.

**Does not:** Accept risk on behalf of the AO or authorize operation.

**Block:** 4.

---

### 10. Authorization readiness package

**Manual workflow today:** Before the AO briefing, staff assemble a readiness packet: executive summary, open blockers, control status rollups, outstanding POA&M items, and checklist of what remains before authorization.

**Input:** Package run summary, matrix rollups, POA&M draft index, assessor readiness checklist, validation warnings.

**What the product does:**
1. Assembles a single readiness document with deterministic rollups and LLM narrative glue.
2. Lists blockers (controls needing attention, stale evidence count, validation failures).
3. Produces audience-specific summary views (ISSO working, AO readiness, assessor prep) from the same cited facts.

**Outcome:** Authorization readiness package draft for staff review before AO meeting.

**Does not:** Recommend authorize/deny or substitute for AO judgment.

**Block:** 4; 6 (audience summaries).

---

### 11. Document ingestion (policies, SOPs, records)

**Manual workflow today:** The ISSO collects policy PDFs, access review spreadsheets, log review SOPs, and IR plans. They manually read each document, decide which controls it supports, and attach it in GRC with a collected date.

**Input:** PDF, DOCX, XLSX, txt, md files dropped in `evidence/` folder.

**What the product does:**
1. Extracts bounded text per document (page/section limits configurable).
2. Creates `evidence_items[]` records with `evidence_id`, title, `source_type`, `collected_at`, extracted text, `source_document_ref`.
3. Proposes control link suggestions based on content; operator confirms links before matrix re-run.
4. Preserves source file alongside extraction audit metadata.

**Outcome:** Searchable evidence items in the package index, ready to link to controls. Link suggestions speed manual GRC attachment work — human confirms.

**Does not:** Invent policy language or auto-publish links to GRC without confirmation.

**Block:** 3.

---

### 12. Architecture and diagram intake

**Manual workflow today:** The ISSO maintains boundary diagrams (Visio, draw.io, Lucid) and manually writes the authorization boundary section of the SSP to match. Assessors compare diagram to SSP narrative and flag mismatches.

**Input:** PNG, JPEG, WebP, SVG, PDF architecture pages, or structured exports (draw.io JSON, Visio XML, CMDB boundary export).

**What the product does:**
1. For structured exports: parses to `architecture_artifacts[]` with components and flows.
2. For raster/PDF: runs in-boundary vision/text extraction to `extracted_components[]`, `extracted_flows[]`, `boundary_scope_claim`.
3. Compares SSP boundary narrative to diagram content; flags components in diagram missing from SSP and reverse.
4. Links artifacts to relevant controls (e.g., SC, CM families) when suggested or confirmed.

**Outcome:** Architecture artifacts in package, boundary consistency flags in report, SSP boundary sections populated from cited extraction. Failed extraction marked `TBD — diagram missing or unreadable`.

**Does not:** Generate or render new architecture diagrams.

**Block:** 3.

---

### 13. Scanner-to-ATO brief (STIG, CCRI, vulnerability exports)

**Manual workflow today:** The ISSO imports Qualys/Tenable/STIG/SCC/CCRI results into a spreadsheet, manually maps findings to 800-53 controls, and writes narrative for the SAR and POA&M explaining ATO impact ("CM-6 partial because 14 hosts failed STIG ID...").

**Input:** Scanner export files (Nessus XML, SCAP, STIG checklist, CCRI, SARIF, vendor JSON) in evidence folder.

**What the product does:**
1. Parses structured scanner formats deterministically where possible.
2. Maps findings to control IDs using deterministic rules + bounded LLM for narrative impact language.
3. Produces scanner impact section: finding ID, severity, affected asset, mapped control, draft SAR/POA&M language, citations to scan artifact.

**Outcome:** STIG/CCRI/vuln impact brief feeding SAR input pack and POA&M drafts. Does not re-run scans.

**Does not:** Execute scans or remediate findings.

**Block:** 3 (intake); 4 (brief in reports).

---

### 14. Cross-artifact consistency brief

**Manual workflow today:** Senior ISSOs manually reconcile SSP claims against scan results, POA&M open items, and attached evidence — looking for "SSP says STIG enforced but scan shows 40 failures" class contradictions.

**Input:** Canonical package, sufficiency matrix, scanner brief, optional OSCAL POA&M import.

**What the product does:**
1. Compares implementation statements to linked evidence text, scanner findings, and open POA&M items.
2. LLM produces contradiction brief with citations only — each contradiction points to specific artifact IDs.
3. Feeds SAR and POA&M draft generation.

**Outcome:** Cross-artifact consistency section in report; portal readiness flags.

**Does not:** Resolve contradictions or update GRC records.

**Block:** 6.

---

### 15. Assessor readiness checklist

**Manual workflow today:** Before sending the package to a 3PAO or internal SCA, the ISSO mentally runs through common rejection reasons: stale evidence, TBD implementation statements on in-scope controls, broken links, OSCAL that will not validate, FedRAMP KSI indicators without proof.

**Input:** Full analysis output: validation warnings, matrix, stale flags, consistency brief, OSCAL validation result (if applicable), KSI rollup (FedRAMP).

**What the product does:**
1. Runs deterministic rules for known rejection patterns.
2. Bounded LLM adds narrative completeness flags where rules cannot cover.
3. Outputs checklist items with severity hints and citations — not pass/fail authorization.

**Outcome:** Assessor readiness checklist in report and portal Readiness tab. Operator fixes items before export.

**Does not:** Simulate official 3PAO sign-off or replace assessor judgment.

**Block:** 6.

---

### 16. Assessor walkthrough pack and response pack

**Manual workflow today:** During assessor review meetings, the ISSO prepares talking points per control with evidence pointers. When the assessor sends written questions or comments, the ISSO maps each comment to controls and drafts responses with evidence citations.

**Input (walkthrough):** Matrix, evidence index, gap clusters.

**Input (response):** Inbound assessor comments (text upload or paste) mapped to package.

**What the product does:**
1. Walkthrough pack: cited Q&A trail per control — "if assessor asks about AC-2, cite ev-iam-policy and flag stale access review."
2. Response pack: maps each assessor comment to control IDs and evidence; drafts response language for human review.

**Outcome:** Draft meeting prep and response documents. Human leads session and approves every response.

**Does not:** Authoritatively rebut assessor findings or close POA&M items.

**Block:** 6.

---

### 17. Gap clusters, gap-closure advisor, and gap-to-owner export

**Manual workflow today:** The ISSO groups related gaps (all access control family issues, all stale review problems) for POA&M planning and assigns remediation tasks to control owners via email or GRC tasks.

**Input:** Sufficiency matrix gaps, control families, scanner brief.

**What the product does:**
1. Gap clusters: deterministic grouping by control family and shared gap themes.
2. Gap-closure advisor: per gap, suggests evidence types needed, assessor question hints, draft remediation steps — advisory only.
3. Gap-to-owner export: structured task list (control ID, gap summary, suggested owner field from metadata, citations) for handoff to GRC task engine or email.

**Outcome:** Clustered gap view in portal; advisor notes in report; exportable remediation task list.

**Does not:** Own control-owner workflow or send tasks without operator action.

**Block:** 6.

---

### 18. Implementation narrative flags and inheritance analysis

**Manual workflow today:** The ISSO checks whether each implementation statement actually addresses what the control requires (not just "we have a policy") and whether inherited/common-control claims are supported by the provider's evidence.

**Input:** Controls with implementation statements, approved reference RAG (800-53 requirement text, overlays), inheritance metadata from OSCAL.

**What the product does:**
1. Compares implementation statement content to control requirement elements (RAG-grounded); flags missing required elements.
2. For inherited controls, flags unsupported claims where customer-responsible evidence is absent.

**Outcome:** Implementation narrative flags section; inheritance review section. Feeds SSP draft and assessor checklist.

**Does not:** Decide official inheritance scope — GRC/eMASS owns tailoring.

**Block:** 6.

---

### 19. Supporting plan drafts (IRP, CP/ISCP, CMP, RoB, ConMon strategy)

**Manual workflow today:** SMEs draft Incident Response Plan, Contingency Plan, Configuration Management Plan, Rules of Behavior, and ConMon strategy as separate documents referenced by the SSP.

**Input:** Customer-requested plan type, relevant evidence items, SSP metadata, templates.

**What the product does:**
1. Assembles plan sections from template + cited evidence.
2. Marks `TBD — input missing` for RTO/RPO, roles, or procedures not provided — does not invent.

**Outcome:** Full draft supporting plan for SME review.

**Does not:** Approve or store official plans — GRC/repo remains SoR.

**Block:** 4 (on request).

---

### 20. ConMon prep and package delta (ongoing authorization)

**Manual workflow today:** FedRAMP and agency programs require monthly ConMon: compare this month's evidence to last month, update POA&M, note what improved or regressed, draft ConMon narrative for GRC. Significant changes may trigger SCR.

**Input:** Current package, `prior_package_id` linked prior snapshot (OSCAL/GRC export + scans from prior month).

**What the product does:**
1. Diffs controls, evidence items, and matrix statuses between packages.
2. Produces package delta report: added/removed/changed controls, new/removed evidence, status transitions (e.g., partial to supported).
3. Drafts ConMon narrative and POA&M update suggestions for FedRAMP path.
4. SCR/significant-change brief when material boundary or control changes detected (FedRAMP).

**Outcome:** Delta report, ConMon prep pack, POA&M update drafts for gated GRC export. GRC and FedRAMP process remain authoritative.

**Does not:** Submit to FedRAMP PMO, own POA&M status, or run ConMon scheduler as system of record (Option 2 deferred).

**Block:** 7.

---

### 21. FedRAMP KSI readiness rollup (20x)

**Manual workflow today:** FedRAMP 20x CSPs map evidence to Key Security Indicators and check whether each indicator has supporting proof before assessor review.

**Input:** Imported KSI catalog JSON (customer-provided), sufficiency matrix, evidence index.

**What the product does:**
1. Maps matrix rows and evidence items to KSI indicator IDs from imported catalog.
2. Rolls up draft readiness per indicator with citations.
3. No live cloud polling or scheduled KSI re-validation.

**Outcome:** KSI readiness rollup section in report (FedRAMP path only).

**Does not:** Collect cloud signals or validate KSIs against live infrastructure.

**Block:** 2+.

---

### 22. Package-scoped evidence search

**Manual workflow today:** The ISSO searches through dozens of PDFs and folders asking "where is the access review?" or "which document mentions log retention?"

**Input:** Archived package evidence index with extracted text.

**What the product does:**
1. Keyword search over evidence titles and extracted text.
2. Optional embedding retrieval scoped to one package (not org-wide GRC search).

**Outcome:** Search results with evidence ID, snippet, linked controls. Portal Evidence index tab.

**Does not:** Search across packages or live GRC.

**Block:** 5/6.

---

### 23. Targeted re-analysis

**Manual workflow today:** After the ISSO uploads a new access review or fixes one control's evidence, they re-review only the affected controls rather than re-reading the entire package.

**Input:** Updated package re-ingested; prior matrix run; list of controls with changed evidence or `needs attention` status.

**What the product does:**
1. Operator selects "Re-analyze controls needing attention" in portal (or CLI flag in later blocks).
2. Re-runs sufficiency matrix LLM calls only for flagged controls plus controls whose linked evidence changed.
3. Merges results into updated report and audit record.

**Outcome:** Updated matrix rows for targeted controls; new audit run ID. Full re-analysis remains default after material intake changes.

**Does not:** Trigger external scans or GRC sync.

**Block:** 5.

---

### 24. OSCAL import, export, and validate-before-export

**Manual workflow today:** Programs exchange SSP, SAP, SAR results, and POA&M via OSCAL JSON/XML between GRC, eMASS, and assessor tools. Invalid OSCAL blocks import.

**Input:** OSCAL JSON/XML for any of four models; or draft exports from analysis run.

**What the product does:**
1. Import: parses OSCAL into canonical package; preserves citations; partial import OK per model.
2. Export: generates full-draft OSCAL for SSP, SAP, assessment-results, POA&M with draft/machine-generated markers.
3. Paired export: same run produces OSCAL plus ISSO-readable markdown.
4. Before gated writeback: runs NIST OSCAL schema validation and path-specific semantic rules; fails export with actionable errors if invalid.

**Outcome:** Round-trip OSCAL drafts ready for human edit and GRC import. Validation gate prevents shipping broken OSCAL.

**Does not:** Become OSCAL system of record or auto-import to GRC without approval.

**Block:** 2 (import + paths); 4 (export); 7 (validate-before-export).

---

### 25. GRC read-only import and approval-gated writeback

**Manual workflow today:** ISSO exports from Archer/CSAM/eMASS, works offline, then manually re-enters drafts back into GRC after review. Writeback is deliberate and approved.

**Input:** Read-only export from GRC/eMASS/scanner adapter (when enabled). For writeback: human-approved draft payload + approval record.

**What the product does:**
1. Read-only import pulls controls, evidence metadata, OSCAL fragments into package intake — GRC stays SoR.
2. After operator reviews draft in portal and clicks "Send to approval," routes to approver queue.
3. With `action_gated` profile, approved OSCAL/POA&M payload exports to configured GRC destination. Blocked if path mismatch (e.g., eMASS writeback disabled for FedRAMP package).

**Outcome:** Faster round-trip: export from GRC -> analyze -> review -> approved import back. Official records change only after human approval.

**Does not:** Bidirectional sync, auto-write, or replace GRC workflow engine.

**Block:** 7.

---

### 26. Evidence portal, package chat, and audit trail

**Manual workflow today:** ISSOs share reports via email and spreadsheets; assessor questions go back and forth in threads; audit is manual version control.

**Input:** Archived analysis runs per package.

**What the product does — portal:**
1. SPA with package list, detail view, run summary header (Supported/Partial/Unsupported/Insufficient/Needs attention counts).
2. Tabs: Summary, Readiness, Control matrix (filterable), Draft artifacts, Evidence index, Architecture, Audit.
3. Read-only default; writeback only with `action_gated`.

**What the product does — chat:**
1. Citation-bound Q&A over one archived package.
2. Intents: explain finding, list evidence for control X, draft POA&M/SAR language, compare to prior package, gap advisor, audience summary.
3. Refuses: authorize, accept risk, mark compliant.

**What the product does — audit:**
1. Append-only record per run: package_id, path, timestamp, operator, model, input hash, output paths, validation warning count.

**Outcome:** Single place to review analysis, chat with citations, approve exports, and prove what the AI ran when.

**Does not:** Multi-tenant SaaS, org-wide search, or unconstrained agent behavior.

**Block:** 5 (portal core); 6 (chat expansion); 1 (audit log); 7 (approvals).

---

## Part 2 — Epics

Epics map to build blocks and RMF themes. Each epic is independently demoable when its block ships.

| Epic ID | Epic name | Build block(s) | Primary persona |
| --- | --- | --- | --- |
| E1 | Evidence package ingest and canonical normalization | 1, 3 | ISSO |
| E2 | Package validation and pre-flight gating | 1, 2 | ISSO |
| E3 | Evidence sufficiency matrix and control review | 1 | ISSO, SCA |
| E4 | Deterministic evidence quality flags | 1, 6 | ISSO, SCA |
| E5 | OSCAL and multi-path authorization support | 2, 7 | ISSO |
| E6 | Document, diagram, and scanner intake | 3 | ISSO, control owners |
| E7 | Full-draft government artifacts (SSP, SAR, POA&M, SAP, RAR, readiness) | 4 | ISSO, SCA, AO staff |
| E8 | Evidence portal and package run summary | 5 | ISSO, SCA, AO staff |
| E9 | Advanced analysis (consistency, gaps, assessor prep, narrative, KSI) | 6 | ISSO, SCA |
| E10 | ConMon delta, gated writeback, and production operations | 7 | ISSO, FedRAMP PMO liaison |
| E11 | On-prem runtime, security boundary, and audit governance | 7+ | Platform admin, ISSO |

---

## Part 3 — User stories

Format: **As a** [persona], **I want** [action], **so that** [manual workflow outcome].

Acceptance criteria are intentionally concrete — verifiable by an ATO SME.

---

### Epic E1 — Evidence package ingest and canonical normalization

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E1-US1 | As an ISSO, I want to drop a GRC JSON export of any internal shape into intake, so that I do not have to re-key controls and evidence into a fixed schema before analysis. | Messy export normalizes to canonical model; raw preserved; golden + messy fixtures pass. | 1 |
| E1-US2 | As an ISSO, I want unreadable or unsafe uploads quarantined with a clear reason, so that bad files do not silently corrupt analysis. | Quarantine path, error message, no partial report without operator acknowledgment. | 1 |
| E1-US3 | As an ISSO, I want to ingest a package folder with PDFs, DOCX, and scanner exports, so that one drop contains everything I would otherwise attach manually in GRC. | Each file becomes evidence item or scanner artifact; source refs preserved. | 3 |
| E1-US4 | As an ISSO, I want the system to suggest which controls a new document supports, so that I spend less time on manual GRC linking. | Link suggestions with confidence; human confirms before matrix uses new links. | 3 |
| E1-US5 | As a platform admin, I want attestation bundles accepted as read-only evidence, so that pipeline exports from TestifySec-like tools can be analyzed without us running CI/CD. | `source_type` set correctly; normalized text; no signing or collection. | 3 |

---

### Epic E2 — Package validation and pre-flight gating

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E2-US1 | As an ISSO, I want broken evidence links caught before analysis runs, so that I fix the package instead of getting nonsense matrix rows. | Orphan links fail validation; reported with control ID and missing evidence ID. | 1 |
| E2-US2 | As an ISSO, I want a completeness score and warning list before a full LLM run, so that I know what is missing the way I would from a pre-review checklist. | Score, warnings, block/warn threshold; analysis blocked when below threshold. | 1 |
| E2-US3 | As an ISSO on FedRAMP path, I want a path-aware upload checklist, so that I see FedRAMP-specific missing items before spending assessor time. | Checklist differs by `authorization_path`; cites missing metadata/artifacts. | 2 |
| E2-US4 | As an ISSO, I want stale evidence flagged by collected date against our policy threshold, so that I catch outdated access reviews before the 3PAO does. | Stale list with evidence ID, collected date, threshold; deterministic. | 1 |

---

### Epic E3 — Evidence sufficiency matrix and control review

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E3-US1 | As an ISSO, I want each in-scope control reviewed against its linked evidence with a draft sufficiency status, so that I get the same judgment I would make manually but faster. | One row per control; four statuses; finding summary; citations to evidence IDs only. | 1 |
| E3-US2 | As an SCA, I want assessor questions drafted per gap control, so that I have a starting point for follow-up without writing from scratch. | Questions present on partial/unsupported rows; cite evidence. | 1 |
| E3-US3 | As an ISSO, I want rollup counts (Supported, Partial, Unsupported, Insufficient, Needs attention), so that I can report package readiness like I would in a status briefing. | `package_run_summary` counts match matrix rows; deterministic rollup. | 1, 5 |
| E3-US4 | As an ISSO, I want every report to state AI disclosure and draft-only posture, so that leadership cannot mistake output for official compliance status. | Fixed disclosure text in every markdown/JSON report. | 1 |

---

### Epic E4 — Deterministic evidence quality flags

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E4-US1 | As an ISSO, I want a list of SSP claims that reference missing evidence IDs, so that I fix linkage before assessor review. | Missing evidence section; control ID + broken link ID. | 1 |
| E4-US2 | As an SCA, I want contradictions between implementation statements and scanner findings surfaced, so that I do not have to manually crosswalk STIG output to SSP narrative. | Contradiction brief with control ID, SSP claim cite, scan cite. | 6 |
| E4-US3 | As an ISSO, I want orphan evidence items listed, so that I either link or remove documents that add no value. | Orphan list with evidence ID and title. | 1 |

---

### Epic E5 — OSCAL and multi-path authorization support

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E5-US1 | As an ISSO, I want to import an OSCAL SSP partial export, so that I analyze what is already in GRC without retyping. | Partial OSCAL import; controls and evidence populate canonical model. | 2 |
| E5-US2 | As a FedRAMP ISSO, I want FedRAMP-specific POA&M field shapes in exports, so that my draft imports into CSP GRC without field remapping. | POA&M export uses FedRAMP field map when path is `fedramp`. | 2, 4 |
| E5-US3 | As a DoD ISSO, I want eMASS-oriented field shapes and STIG emphasis in drafts, so that outputs match my eMASS workflow. | Path `dod_rmf` templates; STIG brief prominent in assess outputs. | 2, 4 |
| E5-US4 | As an ISSO, I want OSCAL export validated before writeback, so that I do not push broken XML/JSON to GRC. | Schema + semantic validation; fail with actionable errors. | 7 |

---

### Epic E6 — Document, diagram, and scanner intake

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E6-US1 | As an ISSO, I want policy PDFs extracted to searchable evidence text, so that the matrix can cite policy content without me pasting it manually. | Evidence item with extracted text, page ref, source path. | 3 |
| E6-US2 | As an ISSO, I want architecture diagrams read and components listed, so that SSP boundary sections reflect what is actually in the diagram. | `architecture_artifacts[]` populated; extraction method recorded. | 3 |
| E6-US3 | As an ISSO, I want Nessus/STIG exports mapped to control impact language, so that I do not manually write SAR findings for every scan result. | Scanner brief with finding-to-control mapping and draft narrative. | 3, 4 |
| E6-US4 | As an ISSO, I want boundary narrative compared to diagram extraction, so that I catch "diagram shows DMZ component not in SSP" before assessor does. | Consistency flags with diagram ID and SSP section cite. | 3, 6 |

---

### Epic E7 — Full-draft government artifacts

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E7-US1 | As an ISSO, I want a full draft SSP generated from my package, so that I edit a complete document instead of writing from a blank template. | All path SSP sections present; TBD where input missing; citations where supported. | 4 |
| E7-US2 | As an ISSO, I want a draft POA&M with one row per open gap, so that I import weaknesses into GRC instead of typing each item. | Weakness, milestone, risk, control map, citations; path-aware fields. | 4 |
| E7-US3 | As an SCA, I want a SAR input pack with draft findings and severity hints, so that I formalize findings faster while retaining sign-off authority. | Findings linked to controls and evidence; OSCAL assessment-results export. | 4 |
| E7-US4 | As an SCA, I want a draft SAP with test focus on high-risk gaps, so that assessment scope reflects known weak areas. | SAP sections complete; focus list ranked from matrix gaps. | 4 |
| E7-US5 | As AO staff, I want a draft RAR and authorization readiness package, so that I prepare the AO briefing from cited gaps not new invention. | RAR + readiness doc; no risk acceptance language as decision. | 4 |
| E7-US6 | As an ISSO, I want paired OSCAL and markdown export from one run, so that machine and human formats stay in sync. | Same run_id; equivalent content in both formats. | 4, 7 |
| E7-US7 | As an SME, I want optional supporting plan drafts (IRP, CMP, etc.), so that referenced plans exist when the SSP cites them. | Plan type selectable; TBD for missing inputs. | 4 |

---

### Epic E8 — Evidence portal and package run summary

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E8-US1 | As an ISSO, I want to see all packages and their latest run summary, so that I know which systems need attention without opening reports on disk. | Package list; header counts match latest `package_run_summary`. | 5 |
| E8-US2 | As an ISSO, I want to filter the control matrix by status and stale evidence, so that I work gap controls first like I would in a spreadsheet filter. | Filters work; row drill-down shows matrix detail and citations. | 5 |
| E8-US3 | As an ISSO, I want to search evidence within one package, so that I find "access review" without opening every PDF. | Search returns evidence ID, snippet, linked controls. | 5, 6 |
| E8-US4 | As an ISSO, I want to re-run analysis only on controls needing attention after I upload fixed evidence, so that I do not wait for a full package re-review. | Targeted re-analysis updates only flagged/changed controls; new audit entry. | 5 |
| E8-US5 | As an ISSO, I want a banner stating draft analysis readiness on every summary view, so that stakeholders do not confuse this with GRC official status. | Mandatory banner text on summary and matrix tabs. | 5 |

---

### Epic E9 — Advanced analysis (consistency, gaps, assessor prep)

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E9-US1 | As an ISSO, I want gap clusters by control family, so that I plan POA&M remediation in logical groups. | Clusters in report; each cites member control IDs. | 6 |
| E9-US2 | As an ISSO, I want an assessor readiness checklist before export, so that I fix common 3PAO rejection reasons proactively. | Checklist with severity and citations; not pass/fail auth. | 6 |
| E9-US3 | As an SCA, I want a walkthrough pack with cited talking points per control, so that review meetings stay evidence-grounded. | Q&A per control; citations required. | 6 |
| E9-US4 | As an ISSO, I want to paste assessor comments and get draft responses mapped to controls, so that I respond faster while reviewing every word. | Comment-to-control mapping; draft response text; human approval implied. | 6 |
| E9-US5 | As an ISSO, I want implementation narrative flags when my SSP statement does not address the control requirement, so that I fix thin boilerplate before assessor sees it. | Flags cite requirement text from approved RAG. | 6 |
| E9-US6 | As an ISSO, I want package chat that cites evidence and refuses authorization questions, so that I get analyst help without AI overstepping. | Chat returns citations; auth/risk questions refused with explanation. | 6 |
| E9-US7 | As a FedRAMP ISSO, I want KSI readiness rollup from my imported catalog, so that I see indicator coverage before 20x assessor review. | Rollup maps evidence to KSI IDs; no live polling. | 2, 6 |
| E9-US8 | As an ISSO, I want a gap-to-owner export for remediation handoff, so that I assign tasks in GRC without retyping gap descriptions. | Export with control ID, gap summary, owner hint, citations. | 6 |

---

### Epic E10 — ConMon delta, gated writeback, and production operations

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E10-US1 | As a FedRAMP ISSO, I want to compare this month's package to last month's, so that ConMon documents what changed without manual diff spreadsheets. | Delta report: control, evidence, matrix status changes. | 7 |
| E10-US2 | As a FedRAMP ISSO, I want ConMon narrative and POA&M update drafts from the delta, so that I feed GRC instead of replacing ConMon tooling. | ConMon prep pack; Option 1 only; gated export. | 7 |
| E10-US3 | As a FedRAMP ISSO, I want a significant-change brief when boundary or controls materially change, so that I know when SCR discussion is needed. | SCR brief when delta rules fire; cites changed artifacts. | 7 |
| E10-US4 | As an ISSO, I want approved drafts pushed to GRC only after explicit approval, so that AI output never becomes official record by accident. | `action_gated` required; approval queue; blocked wrong path. | 7 |
| E10-US5 | As an ISSO, I want read-only import from GRC to seed a package, so that I start analysis from current SoR export not manual copy. | Import adapter populates canonical model; GRC unchanged. | 7 |

---

### Epic E11 — On-prem runtime, security boundary, and audit governance

| ID | Story | Acceptance criteria (summary) | Block |
| --- | --- | --- | --- |
| E11-US1 | As a platform admin, I want analysis to run with local LLM inside the enclave, so that CUI packages are not sent to public APIs. | `onprem_production` profile; `ALLOW_SENSITIVE_OPENAI` irrelevant; local vLLM path. | 7+ |
| E11-US2 | As an ISSO, I want every analysis run logged with input hash and model profile, so that I can answer auditor questions about what the AI touched. | Append-only audit JSON per run; immutable raw input preserved. | 1, 7 |
| E11-US3 | As a platform admin, I want packages declaring CUI rejected when OpenAI dev profile is used, so that prototyping does not leak sensitive data. | Classification gate when `ALLOW_SENSITIVE_OPENAI=false`. | 1 |
| E11-US4 | As an AO staff reader, I want audience-specific summaries from the same facts, so that I see executive framing without different invented findings. | ISSO/AO/assessor views; same citations; labeled draft. | 6 |

---

## Part 4 — Epic to RMF step map (quick reference)

| RMF step | Epics |
| --- | --- |
| Prepare | E11 (RAG, governance) |
| Categorize | E2 (metadata validation) |
| Select | E5 (OSCAL import, inheritance E9-US5) |
| Implement | E1, E6, E7 (SSP, supporting plans, diagram intake) |
| Assess | E3, E4, E6, E7 (matrix, scanner brief, SAR/SAP), E9 |
| Authorize | E7 (RAR, readiness), E9 (audience summaries) |
| Monitor | E7 (POA&M), E10 (ConMon, delta, SCR) |
| Cross-cutting | E8 (portal), E11 (audit, boundary) |

---

## Part 5 — What we explicitly do not build (SME guardrails)

Use this list when scoping demos or responding to "can it also...?" from assessors and AOs.

- Grant or deny ATO; accept risk; sign SAR; submit to FedRAMP PMO
- Execute vulnerability scans, penetration tests, or CCRI
- Replace GRC, eMASS, or FedRAMP Marketplace as system of record
- Live-sync controls, POA&M status, or evidence from GRC
- Run CI/CD observers or sign attestations
- Generate architecture diagrams or invent evidence/architecture facts
- Org-wide portfolio dashboard or cross-package search
- Privacy controls (800-53 appendix / PIA) or SCRM (800-161) in current scope
- ConMon platform replacement (POA&M SoR, workflow engine, PMO pipeline)
