# ATO Portal Demo Talking Track

**Status:** Approved product language for demos (Phase 6A upload-first reconciliation 2026-07-17)
**Normative implementation contract:** [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md)
**Portal click-path reference:** [`docs/PORTAL_WORKFLOW_GUIDE.md`](docs/PORTAL_WORKFLOW_GUIDE.md)

Use this script only for capabilities implemented in the demonstrated build. Clearly label planned screens or sample outputs.

**Default WSL/synthetic demo:** deterministic analysis run (no matrix LLM). Use targeted or full runs to demo model-assisted matrix rows. Local dev may use an integrity-only malware substitute (**HS-005**), not production ClamAV. Intake MAP may show `policy_blocked` when routing denies pre-attestation model calls — that is expected, not a production qualification claim.

## 30-second leadership pitch

> Government authorization and FedRAMP certification require teams to assemble facts from documents, test results, diagrams, GRC exports, and assessor material. Analysts then spend substantial time checking whether the package is complete, tracing evidence, finding gaps, and drafting package content.
>
> The ATO Evidence Analysis Portal accelerates that analysis and drafting work. It preserves the source of every fact, uses bounded AI to compare and explain evidence, and gives people a place to review every result before an approved draft bundle leaves the system.
>
> It does not certify, authorize, accept risk, replace GRC, perform assessor work, or submit to the government.

## Product in plain words

The product helps answer:

- What did the customer provide?
- Which package requirement does each source support?
- What is missing, stale, contradictory, or not independently supplied?
- What provider-owned draft text can be prepared from confirmed facts?
- What must a human reviewer or assessor decide?
- What exact approved bundle was exported?

## Supported paths

### FedRAMP 20x Program

For cloud service providers pursuing the Program Certification path, the first target is Class C.

The product prepares and checks draft:

- Certification Package Overview
- Security Decision Record
- Ongoing Certification Report
- Secure Configuration Guide readiness and reference
- KSI methods, evidence, metrics, and readiness
- Imported independent assessment material

It shows missing operating obligations, freshness, cadence, and package requirements. It does not perform continuous validation or independent assessment.

### FedRAMP Rev. 5 transition

The `fedramp_rev5_transition` profile provides read-only import and transition analysis for existing Rev. 5 packages, including transition gaps and comparison with FedRAMP 20x Program requirements. It is not the default path for a new certification.

### Agency FISMA security

For agency-owned systems, the product accepts the customer-authoritative security control set and prepares:

- Security SSP section drafts
- SAR input material
- Human-confirmed POA&M candidates
- Security readiness summary
- Evidence sufficiency matrix

Privacy work remains outside the product and must be completed in the agency process.

### Not supported in v1

- DoD RMF, eMASS, CCRI, or IC workflows
- Classified data
- Privacy artifacts
- Official government submission
- Live GRC/scanner/cloud collection

## Upload-first workflow (not profile-first)

**Upload first.** The operator creates a **System**, creates a **minimal revision** (optional parent link only), uploads whatever they have, and finalizes. Only after scan, extract, and intake does the portal reveal **Revision metadata** and the **Package Editor** with pre-filled, editable fields.

**Not the demo story:** pick FedRAMP vs FISMA and impact level correctly before anyone uploads.

### What happens after upload

1. Scan → extract → chunk/index (deterministic)
2. Intake **MAP** — bounded model calls packed to the configured context cap (default **70%** utilization minus reserves); may be `policy_blocked` before human attestation
3. Intake **REDUCE** — deterministic merge into draft + provenance + conflict list; backend readiness is the confirm gate
4. Operator sets **data origin** and **sensitivity** manually (AI never writes these); reviews AI **suggestions** for profile, class, and impact (editable, never auto-locked)
5. Operator resolves conflicts, edits draft, **Confirm Package** → sealed `ready`
6. Preflight → analysis → review → export (same as before)

### Choosing a profile (path) — after upload, before confirm

**Profile = which rulebook this upload follows.** You set it on the **Revision metadata** panel after upload begins — not at minimal revision create, not on the System, and not after the package is locked.

#### The three choices

| Profile | Typical use | You also pick |
| --- | --- | --- |
| **FedRAMP 20x program** | New cloud provider certification (Class C first) | Certification **Class B or C** |
| **FedRAMP Rev. 5 transition** | Compare or migrate an existing Rev. 5 package | FIPS **impact level** (Low / Moderate / High) |
| **Agency FISMA security** | Agency-owned system, security controls only | FIPS **impact level** |

See **Supported paths** above for what each path produces.

#### What profile controls

Once saved on a revision before confirm, profile drives:

- **Package Editor** — which tabs and sections appear (20x CPO/SDR/OCR vs FISMA controls vs Rev. 5 imports)
- **Analysis checklist** — which rows appear in the evidence matrix
- **Preflight** — what counts as ready to analyze vs ready to export
- **Export ZIP** — which draft artifacts are included
- **Authorization path label** — FedRAMP vs agency (shown read-only after intake)

#### Rules demo presenters should know

| Situation | What happens |
| --- | --- |
| **Creating a new revision** | Minimal create only; upload first |
| **After upload + intake** | Metadata panel + editor appear; AI may suggest profile/class/impact |
| **Data origin / sensitivity** | Operator must select; AI never fills these |
| **After Confirm Package (locked)** | Profile **cannot** be changed on that revision |
| **Need a different path** | Create a **new revision** — a new rulebook, not an edit |
| **Update with parent linked** | Parent must be `ready`; profile inherits from parent and locks in the portal |
| **Same System, different profiles over time** | Allowed — e.g. a Rev. 5 transition revision and a separate 20x revision |
| **Switching paths mid-work** | Old locked revision stays on the old path; new path starts fresh upload and checklist — prior analysis does **not** auto-carry over |
| **Systems no longer active** | **Archive** hides a system from the default list (soft archive only) |

**Say after upload when setting metadata:**

> We upload first, then the product reads the documents. Intake may suggest a profile and class or impact level — we review and save those. Data origin and sensitivity are human attestation only; the model never writes them. Once we confirm and lock the package, the path is fixed. To change paths, we start a new revision.

**Do not say:**

- "Pick FedRAMP before you upload." (Upload-first.)
- "The model set our data classification." (Human-only labels.)
- "We can switch this package to FedRAMP later." (Not on a locked revision.)
- "The system is a FedRAMP system." (Path is per revision.)
- "Parent link copies the old package into the new path." (Parent locks the same path; no copy.)

## Key terms

### ATO

Authority to Operate is an official decision by an Authorizing Official. The product prepares evidence and drafts; it does not make that decision.

### FedRAMP certification

FedRAMP establishes requirements for cloud services used by the federal government. The product supports package preparation for a specified path and class. It does not certify a provider.

### System

The long-lived workspace for one cloud service or agency system — like a project folder that holds every package upload over time.

### Package revision

One upload cycle: the files you uploaded plus the package facts someone confirmed. After **Confirm Package**, that snapshot is **locked** — like saving a PDF you can open later but not edit in place.

Each revision picks its own path (FedRAMP 20x, Rev. 5 transition, or agency FISMA) on the metadata panel after upload. The System does not lock you to one path forever. See **Upload-first workflow** for what that choice controls and when it can change.

### Profile

The authorization path rulebook for one revision: `fedramp_20x_program`, `fedramp_rev5_transition`, or `fisma_agency_security`. Set on **Revision metadata** after upload begins; fixed after lock.

### Parent revision (optional)

A pointer to an **older locked package**, not a copy of it.

- Linking a parent does **not** duplicate files or facts into the new revision.
- You still upload and confirm the new revision from scratch.
- The link exists so the product can later **compare** old vs new and **re-analyze only what changed**.

**Plain analogy:** March package → June update. June points to March as parent. March stays untouched; June is a new locked snapshot.

### Run

One analysis pass against one locked package revision. The run’s results are also kept unchanged — human review adds decisions on top, without rewriting the run.

### Assessment item

One row on the checklist — a FISMA control, a FedRAMP rule, or a FedRAMP KSI.

### Evidence

Proof attached to a claim: policy, scan export, test result, ticket, config record, and similar source material.

### Provenance

Where a fact came from: which uploaded file (by fingerprint) and where inside it — page, section, cell, or field.

### Draft analysis status

| Status | Meaning (plain) |
| --- | --- |
| Supported | The supplied material backs up the claim |
| Partial | Some support exists, but important pieces are missing, weak, or stale |
| Unsupported | Evidence contradicts the claim or shows it is not in place |
| Insufficient evidence | Not enough usable material to decide either way |

These labels are not official compliance or authorization results.

## Demo flow

### 1. Systems and packages

Say:

> A **System** is the workspace for one service — for example, “Agency CRM” or “Cloud Platform X.”
>
> We **create a minimal revision first**, then upload. We do **not** pick FedRAMP or FISMA before files are on the system.
>
> Each time the team uploads and confirms a package, that snapshot becomes a locked **package revision**. You can have many revisions over time: first submission, quarterly update, new evidence after a finding.
>
> After upload and intake, we set **profile** and **Class B/C** or **impact level** on the **Revision metadata** panel. **Data origin** and **sensitivity** are human attestation only — the model never writes those. That path sets the editor, checklist, and export shape for this upload only, and it is **fixed after Confirm Package**.
>
> Optionally, a new revision can **link a parent** — the prior locked package it follows on the **same path**. Parent link locks the profile; it is for history and comparison, not for switching rulebooks and not for copying files.
>
> Inactive systems can be **archived**; they disappear from the default list but are not hard-deleted.
>
> People only see systems their identity groups are allowed to access.

Show:

- System name
- **Create revision** (minimal; optional parent only)
- **Show archived** toggle when demoing archive
- After upload: **Revision metadata** panel with profile, class or impact, data origin, sensitivity
- Selected revision and path label
- Revision status (uploading → ready) and whether analysis can run
- **Parent revision** field when creating an update (optional; locks profile)
- **Change Analysis** when a parent is linked and both sides have content (optional)

Do not say:

- "Pick the profile before upload."
- "This system is compliant."
- "This system will receive an ATO."
- "The product selected the baseline."
- "Linking a parent copies the old package." (It does not.)
- "We can change the profile after confirm." (You cannot.)

### 2. Upload, intake, and draft edit

Say:

> Files are checked, scanned, and extracted safely. Every file is fingerprinted so we always know which upload a fact came from.
>
> Intake runs bounded **MAP** passes — packed to about **70%** of the model context window minus reserves — then a deterministic **REDUCE** merge into the draft. MAP may be **policy-blocked** before we attest data labels; that is routing discipline, not a failure of the upload-first story.
>
> The portal shows an **intake readiness** report: files received, suggested path, gaps, and conflicts. AI may suggest profile and class or impact, but we edit everything in the **Package Editor** and set origin/sensitivity ourselves. We click **Confirm Package** once to lock the revision — not hundreds of separate approve/reject cards.
>
> After lock, that revision cannot be edited. New files or fixes mean a **new revision**. You can point the new one at the old one as **parent** so the product knows what changed.

Show:

- Per-file scan and extraction status
- Rejected or quarantined reason
- Intake readiness panel (files, gaps, MAP step status)
- Conflict list with pick-candidate or edit-in-editor actions
- Revision metadata with human-only origin/sensitivity badges
- Package Editor tabs with pre-filled fields
- Provenance badges (from upload vs model-assisted)
- Save Draft and Confirm Package
- Optional: create next revision **from parent** when demoing an update cycle

### 3. Preflight readiness

Say:

> Preflight answers two separate questions: **Can we run analysis on this snapshot?** and **Is it complete enough to export?**
>
> Missing pieces can still be useful — they tell the team what to ask the customer for next.

Show:

- Analysis blockers
- Export blockers
- Warnings
- Informational readiness percentage

Do not describe the percentage as the decision.

### 4. Evidence matrix

Say:

> The matrix is a checklist with **one row per control or requirement**. Each row shows what the evidence supports, what is missing, and where it came from.
>
> In the usual demo run (**Deterministic Run**), rules fill the rows — no AI call. With **Targeted** or **full** runs, AI can propose statuses; code still validates citations and coverage either way.
>
> If this revision has a **parent**, a **Targeted Run** can focus on rows that changed instead of redoing the entire package.

Show:

- Run type (deterministic vs targeted/full)
- Row status and context-complete marker
- Source citations
- Gaps and questions
- Model-proposed status separately from human disposition (when the run used a model)

### 5. FedRAMP 20x content

Say:

> For the FedRAMP 20x Program path, the sealed package and export bundle carry CPO, SDR, OCR, SCG reference, KSI material, and imported independent assessment inputs. Official JSON is validated against the pinned schema at export; preflight and semantic rules surface readiness blockers before that.

Show:

- Package Editor **Profile** tab (FedRAMP 20x JSON sections)
- **Assessor Inputs** tab (import-only fields)
- Preflight export blockers (assessor inputs, KSI, schema messages)
- Approved export ZIP manifest and profile artifacts — not a separate FedRAMP dashboard

Do not describe an SSP/SAR/POA&M bundle as the primary 20x package.

### 6. Agency FISMA content

Say:

> For an agency system, the customer supplies the tailored control list and agency templates. We analyze the security evidence and prepare security drafts in the export bundle. The product does not choose tailoring or cover the privacy package.

Show:

- Package Editor **Controls** tab (control inventory and implementation statements)
- **Profile** and **Privacy** tabs (FISMA sections and privacy-scope notice)
- Matrix and preflight readiness
- Approved export ZIP (SSP sections, SAR input, POA&M candidates when weaknesses were confirmed in review)

### 7. Human review

Say:

> AI output never becomes the final answer by itself. A reviewer marks each row: accept, edit, reject, ask for more evidence, or confirm a weakness. The original analysis run stays as-is; human decisions are recorded separately on top.

Show:

- Matrix row status (model-proposed when the run used a model)
- Human disposition
- Comment history
- Evidence request

### 8. Approval and export

Say:

> One person submits the exact export bundle. By default a **different** person must approve that same bundle — same content, same fingerprint. When **single-user mode** is explicitly enabled for dev/demo (`SINGLE_USER_MODE_ENABLED`, default **false** in production examples), the same operator may approve their own submission after normal auth and hash checks. If anything changes, approval starts over.
>
> V1 delivers a downloadable ZIP. It does not push into GRC or FedRAMP systems directly.

Show:

- Submitted hash
- Submitter and approver
- Expiration
- Export manifest and validation results

### 9. Package assistant

Say:

> The assistant can explain only this authorized package and must cite its sources. It cannot browse the web, run tools, change records, certify the package, accept risk, or recommend an authorization decision.

Good demo questions:

- "What evidence supports this control?"
- "Why is this row partial?"
- "Which package fields are still missing?"
- "What changed since the last locked package?" (parent revision + Change Analysis)

Refusal demo:

- "Should the AO approve this system?"
- "Mark this control compliant."
- "Accept this risk."

## Security and deployment wording

Use:

> The application is installed on customer infrastructure. Model routing is separately controlled. The initial external model profile is limited to synthetic or explicitly approved redacted non-production data. Real customer production, sensitive, CUI, unknown, and classified data are blocked from that external route. A future approved internal endpoint can change the routing policy without changing the application workflow.

Do not use:

- "Everything stays inside the customer boundary" when the model endpoint is external.
- "CUI-ready" without a customer-approved endpoint, network, deployment, and security assessment.
- "Air-gapped" unless the demonstrated deployment has no external dependency.
- "Automated compliance decision."

## Common questions

### Does it replace GRC?

No. It prepares and reviews evidence-bound drafts. GRC and government processes remain authoritative.

### Does it replace an ISSO or assessor?

No. It reduces reading, comparison, and drafting effort. Humans provide and review facts, confirm weaknesses, supply independent conclusions, and make official decisions.

### Can it use customer production data with OpenAI?

Not under the default policy. The external profile blocks customer production, sensitive, CUI, classified, and unknown data. Any different use requires an explicitly approved deployment policy and boundary; classified remains unsupported.

### Does a valid JSON file mean the FedRAMP package is complete?

No. The product checks both the official schema and the applicable package rules, dates, assessor inputs, KSI material, and other obligations.

### Can we change profile after the package is locked?

No. Profile is set on **Revision metadata** after upload and fixed at **Confirm Package**. To use a different path (for example FISMA → FedRAMP 20x), create a **new revision** with the new profile. Prior locked revisions and their analysis stay on the old path.

### Does it perform continuous monitoring?

No. It analyzes snapshots you upload. It can compare a new locked package to an older one when they are linked by parent revision. It does not collect live telemetry or run the customer’s ConMon program.

### Does approval make the output official?

No. Product approval only authorizes export of that exact draft bundle. Official use and decisions happen in the customer's or government's authoritative process.

## Mandatory close

End every demo with:

> This is an evidence analysis and draft-preparation product. Every material result remains traceable to supplied sources and subject to human review. The system does not make certification, compliance, risk-acceptance, or authorization decisions.
