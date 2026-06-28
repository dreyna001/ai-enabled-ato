# ATO Portal Demo Talking Track

Demo script and glossary for the ATO Evidence Analysis Portal UI mockup and leadership conversations.

**Related docs:** [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md)

**UI mockup:** Cursor canvas `ato-portal-product-ui.canvas.tsx` (Evidence Portal product UI).

---

## 30-second leadership pitch

> Getting a system authorized to operate in government — an ATO — requires proving hundreds of security controls with evidence. That review work already happens manually: ISSOs and assessors read policies, scan results, and GRC records, then write findings, POA&Ms, and readiness notes.
>
> We are not replacing GRC, eMASS, Qualys, or FedRAMP tooling. We automate the **analysis layer** — the slow reading, comparing, gap-finding, and draft-writing — and put it in a portal analysts can review, chat with, and optionally push back as drafts after approval.
>
> Same pattern as our notable analysis product: upstream tools generate artifacts, we analyze with AI inside the customer boundary, humans stay accountable.

---

## Core concepts (say this first)

**ATO (Authority to Operate)**  
Formal approval for a government system to run in production based on accepted security risk. An **Authorizing Official (AO)** decides; everyone else prepares the case.

**RMF (Risk Management Framework)**  
NIST's seven-step lifecycle: **Prepare -> Categorize -> Select -> Implement -> Assess -> Authorize -> Monitor**. Our product mainly accelerates **Implement drafting**, **Assess**, **Authorize prep**, and **Monitor prep** — not the AO decision itself.

**System / authorization package**  
One IT system (or bounded system) going through ATO. In the portal, that's an **evidence package** — all controls, evidence, and scan data for that system in one bundle.

**Authorization path**  
Which gov program rules apply:

- **FISMA agency** — agency ISSO/SCA, agency GRC
- **FedRAMP** — cloud service provider, 3PAO assessor, FedRAMP POA&M format
- **DoD RMF / eMASS** — DoD assessor, STIG/CCRI emphasis, eMASS as system of record

Same NIST controls; different forms, assessors, and tooling.

---

## What is a control?

A **control** is a specific security requirement from **NIST SP 800-53 Rev 5**. There are hundreds per system (often 100–300+ depending on impact level).

**Control ID format:** family + number, e.g. `AC-2`, `AU-6`, `CM-6`.

| Part | Meaning | Example |
| --- | --- | --- |
| Family (letters) | Security domain | `AC` = Access Control |
| Number | Specific requirement in that family | `-2` = second/main access control |

**What a control asks:** "Prove you do X securely" — e.g. manage accounts, review logs, enforce configs.

**What the customer must provide:**

1. **Implementation statement** — "We do X this way" (usually in SSP/GRC)
2. **Evidence** — documents/scans/logs that prove it

**What we do:** Compare evidence to the requirement and flag gaps. We do **not** mark a control "compliant" as an official decision.

---

## Controls in the demo (one layer deep)

### AC-2 — Account Management

**Requirement (plain English):** The system must manage user accounts properly — create, modify, disable, remove, and review access in a controlled way.

**Typical evidence:** IAM policy, access review records, onboarding/offboarding procedures, screenshots or exports from identity tools.

**Why it shows "Partial" in the demo:** Some evidence exists, but an access review is **stale** (too old) or a claim in the SSP isn't backed by attached proof.

**Demo line:** "AC-2 is about proving you manage who has access. The AI found policy evidence but flagged that the last access review is outdated — a very common real ATO finding."

### AU-6 — Audit Review, Analysis, and Reporting

**Requirement:** Someone regularly reviews audit logs to detect suspicious activity.

**Typical evidence:** SIEM reports, log review SOPs, tickets showing reviews happened, sample review records.

**Why "Supported" in the demo:** Evidence appears current and aligned with the implementation statement.

**Demo line:** "AU-6 is log review discipline. Here the evidence matches what they claim — no major gap flagged."

### CM-6 — Configuration Settings

**Requirement:** Systems must use secure configurations (baselines), and unauthorized changes must be controlled.

**Typical evidence:** STIG/CIS baseline docs, config management policy, scan results showing compliance or drift.

**Why "Partial" + POA&M in demo:** STIG/scan findings suggest hosts don't match the stated baseline — technical finding contradicts or weakens the narrative.

**Demo line:** "CM-6 is where scanner results often matter. Qualys/Tenable/STIG output gets mapped to this control to show ATO impact."

### IR-4 — Incident Handling

**Requirement:** The org can detect, respond to, and handle security incidents per a defined plan.

**Typical evidence:** Incident response plan, tabletop exercise records, runbooks, ticket examples.

**Why "Supported" in demo:** Plan + exercise evidence look current.

**Demo line:** "IR-4 is procedural — do you have a plan and proof you practice it?"

### RA-5 — Vulnerability Monitoring

**Requirement:** Identify and remediate vulnerabilities on an ongoing basis.

**Typical evidence:** Vulnerability scan reports, remediation tickets, scan cadence policy, asset inventory linkage.

**Why "Needs review" in demo:** Scanner export may be present but missing **asset ownership** or clear tie to the authorization boundary.

**Demo line:** "RA-5 connects vuln scanning to ATO. Findings exist, but the package doesn't fully connect scans to the right assets — assessor would ask questions."

---

## Evidence (what the portal is really reviewing)

**Evidence** = proof attached to a control (policy PDF text, config export, scan result, log sample, ticket record).

| Term in UI | Meaning |
| --- | --- |
| **Linked evidence** | Artifact IDs tied to a control in the package |
| **Current** | Collected recently enough for policy threshold |
| **Stale** | Too old; assessors often reject or question it |
| **Unsupported claim** | SSP says "we do X" but evidence doesn't show X |
| **Missing evidence** | Control references an evidence ID that isn't in the package |
| **Citation** | Pointer like `ev-iam-policy-2026-02` — AI must cite these, not invent facts |

**Behind the scenes:** Dates and links checked in code; sufficiency and gap language from bounded LLM over provided text only.

---

## Control status labels in the portal

| Status | Meaning | Official? |
| --- | --- | --- |
| **Supported** | Evidence appears to support the implementation claim | No — draft analysis only |
| **Partial** | Some support, but gaps/stale/contradictory items | No |
| **Needs review** | Human must decide; AI sees ambiguity or missing context | No |

**Demo line:** "Green/yellow/orange here is our analysis readiness — not the official control status in GRC or eMASS."

---

## Gov artifacts (Draft Artifacts screen)

### SSP — System Security Plan

**What it is:** The master document describing the system, boundary, and how each control is implemented.  
**Who owns it:** System owner / ISSO in GRC or eMASS.  
**What we draft:** A full review-ready SSP using the selected path template and provided package inputs. Missing boundary, inventory, tailoring, or evidence becomes `TBD — input missing`; humans edit before official SSP update.

### SAR — Security Assessment Report

**What it is:** Assessor's official report of findings from testing/review.  
**Who owns it:** SCA / 3PAO sign-off.  
**What we draft:** A full SAR input pack — finding language, severity, control mapping, citations, and assessor questions. The SCA / 3PAO still owns the official SAR.

### POA&M — Plan of Action and Milestones

**What it is:** Tracked list of weaknesses, remediation plans, due dates, risk — lives in GRC/eMASS.  
**What we draft:** A full draft POA&M export — all open or updated weakness items, path-specific fields, milestones, owners, risk, and citations. ISSO approves before import to GRC/eMASS.

**Demo POA&M fields:**

- **Weakness** — what's wrong (e.g. STIG baseline not enforced)
- **Milestone** — what must happen to fix it (re-scan, attach evidence, update SSP)
- **Risk / citations** — severity framing + proof pointers (`ev-stig-2026-05`, `scan-tenable-2026-06`)

### SAP — Security Assessment Plan

**What it is:** Assessor's plan for *what* to test before the assessment.  
**What we draft:** A full draft SAP: assessment scope, methods, controls to test, sampling approach, schedule, and focus areas derived from gaps.

### RAR — Risk Assessment Report

**What it is:** Risk analysis supporting the authorization decision.  
**What we draft:** A full draft RAR from evidence gaps — risk statements, likelihood/impact framing, and residual risk language. ISSO/AO staff review it; we do not accept risk.

---

## Other terms in the demo

**NIST SP 800-53 Rev 5**  
The control catalog. "Rev 5" is the current revision — our baseline.

**FIPS 199 impact level (Low / Moderate / High)**  
How bad failure would be for confidentiality, integrity, availability. Drives **how many controls** apply — not determined by our product.

**CUI (Controlled Unclassified Information)**  
Sensitive unclassified data. Package declares max classification; processing stays in customer boundary.

**OSCAL**  
Standard JSON/XML format for SSP, SAP, assessment results, POA&M. We import/export standard model names; GRC remains authoritative.

**FedRAMP Moderate**  
FedRAMP baseline for moderate-impact cloud systems — common for CSPs. Header pill shows path + impact context.

**Readiness bar (31 / 52 controls ready)**  
Share of controls our analysis marked supported vs total in package — **readiness for human review**, not ATO granted.

**ConMon (Continuous Monitoring)**  
FedRAMP ongoing monthly monitoring after initial authorization. **Locked strategy:** ConMon prep and gated export to GRC (Option 1) — delta analysis, POA&M update drafts, narrative draft; GRC and FedRAMP process remain authoritative. We do **not** replace the ConMon workflow or submit to FedRAMP Marketplace (Option 2). See ConMon strategy in [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md).

**Significant change / SCR (FedRAMP)**  
Material system change that may require re-assessment. Our **significant-change brief** (in plan) compares packages; we don't run the SCR workflow.

---

## Screen-by-screen with definitions

### Overview

- **Active packages** — systems with evidence bundles in flight (one bundle = one system + one assessment cycle snapshot)

  **What an evidence bundle contains** (file-drop or import — not live GRC sync):

  | Layer | Examples |
  | --- | --- |
  | **Metadata** | System name, authorization path (FISMA / FedRAMP / DoD), impact level, assessment date, boundary summary, data classification |
  | **Controls** | 800-53 Rev 5 IDs in scope, implementation statements, linked evidence IDs |
  | **Evidence artifacts** | Policies, SOPs, access reviews, log review records, IR plans, configs, tickets, screenshots — PDF, DOCX, XLSX, text/markdown |
  | **Scanner / STIG exports** | Qualys, Tenable, SCAP/STIG, SCC, CCRI findings imported as read-only inputs |
  | **OSCAL (optional)** | Partial or full SSP, POA&M, SAP, assessment-results from GRC/eMASS |
  | **Architecture (optional)** | Boundary/network diagrams — PNG, PDF pages, or structured exports from draw.io / Visio |
  | **Prior package (optional)** | Last SSP/POA&M/evidence snapshot for delta, ConMon prep, or significant-change comparison |

  **Demo line:** "Same idea as our notable payload — everything the ISSO would assemble for one assessor review cycle, bounded and citeable. GRC and scanners stay authoritative; we analyze the bundle they export or drop."

- **Controls ready / need attention** — aggregate from our matrix, not GRC
- **Drafts awaiting review** — AI-generated full draft documents not yet human-approved

**Behind the scenes:** Portal reads archived analysis runs. Stats aggregate control matrix from latest report JSON — not live GRC sync.

### Control Review

- **Control list** — 800-53 controls in scope for this system
- **AI evidence finding** — LLM summary bounded to linked evidence
- **Draft SAR finding** — creates assessor-facing finding draft for that control

**Behind the scenes:** Controls from OSCAL SSP or package manifest. LLM output schema-validated; stale dates and broken links caught deterministically first.

### Draft Artifacts

- **Export OSCAL** — machine-readable draft for GRC import
- **Send to approval** — human gate before writeback

**Behind the scenes:** Path-aware field mapping (FedRAMP vs eMASS vs agency). Export writes draft OSCAL models; official records unchanged until GRC import after approval.

### Assistant

- Refuses **authorization** questions — AO decision, not AI
- Returns **readiness summary** — counts and gaps from the package

**Behind the scenes:** Retrieval over one archived package only. Citations required; no open-web grounding.

### Approvals

- **GRC import** — push approved draft into Archer/CSAM/etc.
- **Blocked by path** — eMASS writeback disabled when package is FedRAMP

**Behind the scenes:** `action_gated` capability profile. No writeback without explicit human approval.

### Audit Trail

- **Run** — one analysis execution (validate -> LLM -> reports -> audit log)
- **Validation warnings** — schema/date/link issues caught before trusting output

**Behind the scenes:** Append-only audit record per run: package_id, path, timestamp, model profile, input hash, output paths, warning count.

---

## 10-second definitions cheat sheet (while clicking)

| Term | Say this |
| --- | --- |
| Control | A NIST security requirement we must prove with evidence |
| AC-2 | Prove you manage user accounts properly |
| Evidence | The proof documents/scans tied to a control |
| SSP | Official "how we implement controls" document |
| SAR | Assessor's official findings report |
| POA&M | Official remediation tracker for weaknesses |
| Partial | Some proof, but gaps — not ready for assessor without fixes |
| Stale | Evidence too old to trust |
| Draft | AI wrote it; human must review before it's official |
| GRC/eMASS | Where official records live — we don't replace them |

---

## Leadership one-liner per screen

1. **Overview** — "Which systems are close to ready and what needs human attention?"
2. **Control Review** — "Does the evidence actually prove each NIST requirement?"
3. **Draft Artifacts** — "Turn evidence and gaps into full draft ATO documents humans can review."
4. **Assistant** — "Ask questions about this package — with citations, not guesses."
5. **Approvals** — "Nothing hits GRC without a person."
6. **Audit Trail** — "Every AI run is logged."

---

## Suggested demo flow (5 minutes)

1. **Overview** — three packages, one ready for review
2. **Control Review** — AC-2 stale evidence and unsupported claim
3. **Draft Artifacts** — full draft SSP / POA&M export for human review
4. **Assistant** — citation discipline; refuse authorization decision
5. **Approvals** — human gate before writeback
6. **Audit Trail** — traceable runs

**Close:** "The ATO process does not go away. We make the manual analysis inside it faster, more consistent, and reviewable."

---

## Objection handling (leadership)

| Objection | Response |
| --- | --- |
| "We already have Archer / eMASS / FedRAMP tools" | "Those are the system of record. We accelerate the analysis and draft-writing those tools do not do well." |
| "Can AI grant our ATO?" | "No. AO and assessors decide. We produce evidence-bound drafts and readiness summaries." |
| "Is our data leaving the boundary?" | "Production answer: no. On-prem first, local model, no default public LLM egress. Early OpenAI prototyping is synthetic/redacted only." |
| "Why not build this inside GRC?" | "GRC vendors optimize tracking and workflow. We optimize evidence analysis and generative draft quality." |
| "What's the ROI?" | "ISSOs and assessors spend weeks per package on manual evidence review and document prep. We target that labor, not tool replacement." |

---

## Tie-back to notable analysis (internal audience)

| Notable IR | ATO portal |
| --- | --- |
| SIEM alert | GRC/scanner/evidence package |
| Automated investigation report | Control evidence review + draft gov artifacts |
| Analyst portal + case chat | Evidence portal + package chat |
| Optional Splunk writeback | Optional GRC/eMASS draft writeback after approval |
| Does not close incidents autonomously | Does not grant ATO autonomously |
