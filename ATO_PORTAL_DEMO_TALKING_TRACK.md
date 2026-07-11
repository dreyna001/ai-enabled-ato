# ATO Portal Demo Talking Track

**Status:** Approved product language for demos
**Normative implementation contract:** [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md)

Use this script only for capabilities implemented in the demonstrated build. Clearly label planned screens or sample outputs.

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

## Key terms

### ATO

Authority to Operate is an official decision by an Authorizing Official. The product prepares evidence and drafts; it does not make that decision.

### FedRAMP certification

FedRAMP establishes requirements for cloud services used by the federal government. The product supports package preparation for a specified path and class. It does not certify a provider.

### System

The bounded government system or cloud service being prepared for review.

### Package revision

One immutable snapshot of source files and confirmed facts under its own profile. The profile belongs to the package revision, not the System.

### Run

One immutable analysis of one package revision under one authority, configuration, prompt, and model profile.

### Assessment item

The thing being checked. It may be a FISMA control, FedRAMP rule, or FedRAMP KSI.

### Evidence

Source material that substantiates a claim: policy, procedure, export, test result, scan result, ticket, review record, configuration record, or similar proof.

### Provenance

The exact source of a fact: file hash plus page, section, cell, JSON/XML pointer, text offset, or image region.

### Draft analysis status

| Status | Meaning |
| --- | --- |
| Supported | Supplied context directly supports all material claim elements |
| Partial | Some support exists, but material elements are missing, stale, weak, or not fully reviewed |
| Unsupported | Supplied evidence contradicts the claim or shows the implementation is absent |
| Insufficient evidence | The package lacks enough usable evidence to decide |

These labels are not official compliance or authorization results.

## Demo flow

### 1. Systems and packages

Say:

> Each System can have many immutable package revisions. The supported profile and authorization path belong to each PackageRevision, so a System is not locked to one path. Users see only systems their customer identity groups permit.

Show:

- System name
- Latest PackageRevision and its profile and path
- Data origin and sensitivity
- Readiness and run state

Do not say:

- "This system is compliant."
- "This system will receive an ATO."
- "The product selected the baseline."

### 2. Upload and extraction

Say:

> Before analysis, files are streamed, size-checked, malware-scanned, type-checked, and safely extracted. Every source is hashed. If the format is unfamiliar, AI may propose field mappings, but a person must confirm them before they become trusted package facts. After review, confirmation seals the current revision as ready. Any later source, confirmed fact, profile, label, or link change creates a child revision rather than changing the ready revision.

Show:

- Per-file status
- Rejected or quarantined reason
- Extracted source locator
- Pending proposals

### 3. Preflight readiness

Say:

> Preflight separates two questions. Can we safely analyze this snapshot? And is the package complete enough to export? Missing evidence may still be useful to analyze because it tells the team what to request.

Show:

- Analysis blockers
- Export blockers
- Warnings
- Informational readiness percentage

Do not describe the percentage as the decision.

### 4. Evidence matrix

Say:

> The matrix gives exactly one row for every expected assessment item. The model proposes an evidence-based result, and deterministic code checks citations, row coverage, stale evidence, missing context, and status limits.

Show:

- Status and context-complete marker
- Source citations
- Gaps and questions
- Model output separately from human disposition

### 5. FedRAMP package view

Say:

> For the FedRAMP 20x Program path, the portal organizes the package around the official CPO, SDR, OCR, SCG reference, KSI material, and imported independent assessment inputs. Official JSON is validated against the pinned schema, then separate semantic rules check whether the package is actually ready.

Show:

- Official schema result
- Semantic readiness blockers
- Assessor-owned fields marked import-only
- KSI methods and metric history
- Freshness and next-report dates

Do not describe an SSP/SAR/POA&M bundle as the primary 20x package.

### 6. Agency FISMA view

Say:

> For an agency system, the customer supplies the tailored control list and agency templates. We analyze the security evidence and prepare security drafts. The product does not choose tailoring or cover the privacy package.

Show:

- Control inventory
- SSP security draft
- SAR input pack
- Security readiness and privacy-scope notice

### 7. Human review

Say:

> AI results never silently become accepted findings. A reviewer can accept, edit, reject, request evidence, or confirm a weakness. The original run remains unchanged, and every human decision is versioned and audited.

Show:

- Model proposal
- Human disposition
- Comment history
- Evidence request

### 8. Approval and export

Say:

> The package owner submits one exact draft payload. A different approver reviews that hash. Any content change invalidates the approval. V1 produces a downloadable ZIP and does not write directly into GRC or FedRAMP.

Show:

- Submitted hash
- Submitter and approver
- Expiration
- Export manifest and validation results

### 9. Package assistant

Say:

> The assistant can explain only this authorized package and must cite its sources. It cannot browse the web, run tools, change records, certify the package, accept risk, or recommend an authorization decision.

Good demo questions:

- "What evidence supports this assessment item?"
- "Why is this row partial?"
- "Which package fields are still missing?"
- "What changed from the previous run?"

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

### Does it perform continuous monitoring?

No. It can analyze supplied snapshots and prepare OCR or delta material. It does not collect telemetry, run validations, or host the review process.

### Does approval make the output official?

No. Product approval only authorizes export of that exact draft bundle. Official use and decisions happen in the customer's or government's authoritative process.

## Mandatory close

End every demo with:

> This is an evidence analysis and draft-preparation product. Every material result remains traceable to supplied sources and subject to human review. The system does not make certification, compliance, risk-acceptance, or authorization decisions.
