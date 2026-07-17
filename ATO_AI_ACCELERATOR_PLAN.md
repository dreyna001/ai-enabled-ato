# ATO Evidence Analysis Portal Product Plan

**Status:** Product vision and delivery summary
**Normative implementation contract:** [`ATO_TECHNICAL_SPEC.md`](ATO_TECHNICAL_SPEC.md)

This document explains why the product exists and how it is delivered. It does not define schemas, APIs, security policy, or acceptance thresholds. Where details differ, the technical specification wins.

## Product goal

Reduce the manual work required to prepare and review authorization evidence without replacing the people or systems that make official decisions.

The product:

- Accepts a bounded evidence snapshot for one system.
- Preserves the source and provenance of every usable fact.
- Checks package structure and evidence readiness.
- Uses AI only for controlled comparison, explanation, and draft writing.
- Gives analysts a portal to review, edit, reject, request evidence, and confirm weaknesses.
- Exports an approved draft bundle for an authoritative customer process.

The product never grants an ATO, certifies a cloud service, accepts risk, signs assessor material, or becomes the official GRC record.

## Target customers and paths

### Primary: FedRAMP 20x Program Certification

The first qualified path is FedRAMP 20x Program Certification, Class C.

The product helps a cloud service provider prepare and inspect:

- Certification Package Overview
- Security Decision Record
- Ongoing Certification Report
- Secure Configuration Guide readiness and reference
- KSI methods, evidence, metrics, and readiness
- Imported independent assessment material
- Package freshness, cadence, and completeness

Class B is a later qualification of the same official package family with its own applicability rules and tests. Existing Rev. 5 material may be imported for transition analysis.

The product does not perform continuous validation, host quarterly reviews, collect live telemetry, or submit to FedRAMP.

### Secondary: agency FISMA security

For agency-owned systems, the product analyzes the customer-supplied security control set and prepares:

- Security SSP section drafts
- SAR input material
- Human-confirmed POA&M candidates
- Security readiness summaries
- Evidence sufficiency matrices

Agency templates, tailoring, organization-defined parameters, and inheritance decisions come from the customer. Privacy work is explicitly outside this product scope.

### Deferred

- FedRAMP Agency Certification path
- FedRAMP Class D
- DoD RMF, eMASS, CCRI, and IC workflows
- Classified processing
- Privacy controls and privacy artifacts
- Live GRC/scanner/cloud integrations
- Official government submission

## Customer problem

Authorization packages contain many facts spread across policies, implementation statements, test results, scanner exports, diagrams, tickets, spreadsheets, OSCAL, and GRC exports. ISSOs, system owners, and assessors spend substantial time:

1. Reconciling inconsistent source formats.
2. Finding which evidence supports which requirement.
3. Checking stale, missing, contradictory, or weak evidence.
4. Rewriting the same facts into package materials.
5. Tracking reviewer comments and revisions.
6. Repeating the work after package changes.

The product focuses on this analysis and draft-preparation layer.

## Product boundary

| Authoritative system or person | Product role |
| --- | --- |
| Customer GRC | Import/export draft data; never replace the official record |
| FedRAMP process and schemas | Validate draft package material; never certify or submit |
| Agency authorization process | Prepare security inputs; never authorize or accept risk |
| Scanners and test tools | Read exported results; never execute scans |
| IdP | Authenticate users; product enforces package authorization |
| ISSO/system owner | Supply and review facts |
| SCA/3PAO/assessor | Supply independent conclusions; product does not generate them |
| AO or FedRAMP authority | Make official decisions outside the product |

## User experience

The normal flow is:

```text
Create a System
  -> start a PackageRevision for that System
  -> select the profile for that PackageRevision:
       - fedramp_20x_program for new FedRAMP 20x Program Certification work
       - fisma_agency_security for agency FISMA security work
       - fedramp_rev5_transition for supported read-only import and transition analysis
         of an existing Rev. 5 package, not as the default for a new certification
  -> declare data origin and sensitivity
  -> upload evidence into that PackageRevision
  -> review extraction and normalization proposals
  -> confirm the proposals, sealing that same PackageRevision as ready
  -> run deterministic and AI-assisted analysis
  -> review matrix and draft package materials
  -> request missing evidence or confirm weaknesses
  -> submit an exact draft bundle for approval
  -> separate approver accepts or rejects
  -> download the approved ZIP
```

The confirmed PackageRevision remains the same revision and is immutable once ready. A later source, fact, profile, label, or link change creates a child PackageRevision. Re-analysis creates a new immutable run.

## Why AI is used

AI is useful for tasks that require reading and explaining variable text:

- Proposing mappings from unfamiliar customer exports
- Comparing a claim to the evidence supplied for it
- Explaining contradictions
- Identifying missing narrative elements
- Drafting provider-owned prose from confirmed facts
- Answering package-scoped questions with citations

Code, not AI, decides:

- Data-routing eligibility
- Schema validity
- Applicable rules
- Dates, freshness, and cadence
- Link integrity and exact matrix coverage
- Citation validity
- Status ceilings
- POA&M eligibility
- Export eligibility and authorization

AI is never used to invent source facts, perform assessor work, choose a baseline, accept risk, certify, or authorize.

## Deployment

The target is one RHEL 9-compatible installation per customer enterprise:

- nginx and TLS
- React portal
- FastAPI service
- PostgreSQL metadata, state, jobs, and audit index
- Protected local package storage
- Python analyzer workers
- Customer IdP
- Configured OpenAI-compatible text and optional vision endpoints

The application may run on premises while the model endpoint remains external. It is accurate to call processing fully in-boundary only when the model endpoint, network route, and data policy are also inside an approved boundary.

The initial external endpoint is restricted to synthetic or explicitly approved redacted non-production data. Real customer production data is blocked from external routing by default.

Non-secret operator settings use one schema-validated runtime JSON selected by `ATO_RUNTIME_CONFIG_PATH`; protected credential references keep secret bytes outside that file. Optional functionality uses explicit flags with startup dependency checks. Code, examples, service/proxy assets, install/smoke actions, docs, and deployment-contract tests move together as one runtime contract.

## Delivery roadmap

### EP-00: contract freeze

Before feature coding:

- Pin authoritative FedRAMP/NIST sources.
- Publish internal schemas and OpenAPI.
- Define states, error taxonomy, threat model, evaluation labels, and operations contract.
- Publish the runtime configuration, capability-flag, secret-reference, and deployment-contract rules.
- Synchronize all active documents.

### EP-01: core safety

Harden the `ato_service` safety foundation:

- Route policy before every model call.
- Enforce size, text, token, call, and concurrency limits.
- Require exact matrix coverage and stable citations.
- Write immutable run artifacts.
- Separate invalid input, policy denial, transient failure, terminal failure, and quarantine.
- Fail startup on invalid runtime JSON, unsafe endpoints/paths, or missing enabled-capability dependencies.
- Establish the API-only least-privilege deployment and smoke-test baseline without claiming a production release.

### EP-02: package foundation

- Add systems and immutable PackageRevisions.
- Add field-level provenance and confirmation.
- Add Postgres lifecycle state and durable jobs.
- Add content-addressed storage and recovery.
- Add worker service/config/credential assets only when the worker runtime and replay tests exist.

### EP-03: FedRAMP 20x Program

- Implement official CPO, SDR, and OCR schema handling.
- Add SCG and KSI readiness.
- Import assessor-owned material without generating it.
- Add semantic package and cadence validation.

### EP-04: secure intake

- Add OSCAL and supported documents, scanner exports, diagrams, and attestations one format at a time.
- Sandbox extraction and test malicious inputs.

### EP-05: draft artifacts

- Generate paired machine and human FedRAMP package material.
- Generate agency FISMA security drafts from qualified customer template packs.
- Preserve provenance and explicit missing facts.

### EP-06: review portal

- Add OIDC login and package access controls.
- Add review, comments, evidence requests, weakness confirmation, approval, audit, and ZIP export.
- Activate portal/static proxy routes and portal-specific credentials only with the implemented authenticated portal.

### EP-07: advanced bounded analysis

- Add consistency analysis, package delta, KSI/OCR summaries, targeted re-analysis, and package chat.
- Pass the SME-labeled AI qualification suite.

### EP-08: on-prem release

- Complete the existing API-only systemd/nginx/install scaffold for every implemented process.
- Add installation, upgrade, rollback, backup, restore, purge, monitoring, and incident runbooks.
- Keep runtime config, examples, process credentials, deployment assets, docs, and contract tests synchronized.
- Pass live RHEL 9 installation, migration, smoke, deployment, and recovery drills.

## Current state

The code-complete product stack landed with Phase 6 (2026-07-14). See [`README.md`](README.md) for local verification commands and contract-test entry points.

**Delivered (code-complete, contract-tested):**

- `ato_service` API with OIDC-backed server sessions
- React/Vite portal
- `ato-intake-worker` and `ato-analyzer-worker` long-running workers
- Full `/api/v1` surface: systems, package revisions, draft editor, intake, deterministic and model-assisted analysis runs, review dispositions, export approval/download, package search and bounded chat
- `ato-operator` CLI (preflight, migration verify, qualification check, validation drills, audit verify, search-index rebuild)
- Deployment scaffold (API, portal nginx, intake/analyzer workers, install/smoke scripts)
- Sealed qualification corpus under `data/qualification/`

**Not claimed (customer/environment evidence):**

- Live RHEL install/upgrade/rollback drills
- Customer IdP verification (**HS-003**)
- Production malware scan drill (**HS-005**)
- Real model qualification (**HS-004** / **HS-006**)
- Qualified authority review (**HS-001**)
- Backup-target verification (**HS-008**)

The historical Block 1 developer CLI is retired. Future work extends `ato_service` in the normative phase order and preserves the runtime/deployment contract in every phase.

## Success measures

Pilot success requires:

- Complete official-schema and semantic validation for the supported FedRAMP profile.
- No critical false-supported findings in the adjudicated holdout.
- At least 95% precision for `supported` status.
- 100% valid citation locators.
- At least 80% exact assessor-status agreement.
- 100% prompt-injection policy test pass.
- A reviewer can trace every material draft fact to a source.
- No export occurs without object authorization, review state, separate approval, and an exact payload hash.
- Install, backup, restore, upgrade, rollback, and failure recovery are demonstrated on the target platform.

The full gates and test requirements are in the technical specification.

## Customer decisions required for production

- Agency FISMA template and field mappings
- IdP issuer, client, and group mappings
- Model endpoint and approved data-routing policy
- Malware scanning service
- Retention, legal hold, and approval overrides
- Backup destination and key ownership
- Named GRC integration details, if writeback is later requested
- Qualified SME labels for AI evaluation
- Assessor-owned FedRAMP package inputs

No implementation may infer these customer decisions.
