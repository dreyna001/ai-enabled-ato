# Profile-Bound Control Implementation Statement Improvements

**Status:** Implemented. Product-owner decisions are recorded in Section 9.
Runtime ships profile-bound `implementation_statement_policy` in built-in
`agency-fisma-nist-sp800-53-rev5` version **1.2.0**, with **1.1.0** and **1.0.0**
bundle load compatibility preserved. Output remains draft working material
(Section 10); **HS-001** and **HS-002** stay open.

## 1. Objective

Improve control implementation statements so an ISSO and assessor can understand
how the selected control requirement applies to the system without imposing one
agency's writing rules on every profile.

The implementation must remain:

- Bound to the workspace's immutable profile version.
- Grounded in the pinned control requirement and system evidence.
- Minimal: use the existing control statement, responsibility, status, evidence,
  question, review, and approval fields.
- Honest about unknowns instead of inventing implementation details.
- Extensible to future profiles without profile-ID branches in application code.

## 2. Authority and Product-Policy Boundary

### Current profile

Built-in `agency-fisma-nist-sp800-53-rev5` version **1.2.0** pins (workspaces on
**1.1.0** or **1.0.0** keep their immutable bundles):

- NIST SP 800-53 Rev. 5 catalog release 5.2.0 for control requirements and
  organization-defined parameters.
- NIST SP 800-53B release 5.2.0 for Low, Moderate, and High baseline membership.
- NIST SP 800-18 Rev. 2 Table 1 coverage for control implementation details and
  implementation status in the SSP.

### What those sources require

- SP 800-53 defines what each control requires; it does not prescribe one
  universal sentence template for a system-specific implementation statement.
- SP 800-53B determines which controls are selected by a baseline; it does not
  define statement-writing quality.
- SP 800-18 requires the SSP to describe control implementation details and
  status. It does not make FedRAMP's template instructions applicable to this
  profile.

### What this product must define

The statement quality rules below are profile-owned product policy. They are a
repeatable way to satisfy and review the current profile's NIST requirements;
they must not be presented as verbatim NIST requirements.

FedRAMP rules, fields, templates, and reviewer instructions are excluded. A
future FedRAMP profile may define a different policy in its own bundle.

## 3. Statement Quality Standard

For each selected control, the generated or patched response must address the
applicable control requirement with the following content when supported by
evidence:

| Element | Expected content | Application |
| --- | --- | --- |
| Implementation | The mechanism, process, configuration, or procedure used to meet the requirement. | Required when claiming any implemented portion. |
| Responsibility | The system role, team, organization, or evidenced provider responsible for the described portion. | Required; unresolved responsibility becomes a question. |
| Scope | The components, users, data, environments, or boundary to which the implementation applies. | Required when scope is material to the requirement. |
| Timing | The frequency, event, condition, or trigger governing the activity. | Required only when the control requirement or implementation is time- or event-dependent. |
| Requirement coverage | Each applicable requirement clause must be addressed; unsupported clauses remain explicit gaps. | Required semantically; not inferred from statement length. |
| Grounding | Supporting fact IDs and evidence links for agent-authored claims. | Required under the existing profile evidence policy. |
| Status and gaps | The profile-defined implementation status and any unresolved, planned, or unsupported portion. | Required using current fields and questions. |

Statements may use one or several sentences. The policy will not require a
specific sentence order, heading format, minimum word count, or agency template.

### Special cases already in scope

- **Organization-defined parameters:** do not copy unresolved OSCAL parameter
  syntax into the statement. Ask a targeted question when the missing value is
  needed to describe the control.
- **Inherited controls:** name the provider and inherited portion only when
  evidence supports them; otherwise record an unresolved question.
- **Hybrid controls:** distinguish the evidenced inherited portion from the
  evidenced system-specific portion in the same statement.
- **Unknown or incomplete implementation:** preserve the gap and question; do
  not produce plausible filler.

### Explicitly deferred

- Dedicated not-applicable rationale fields and confirmation workflow.
- Common-control provider registry or inheritance UI.
- Full organization-defined parameter registry and editor.
- Clause-level evidence persistence or a new evidence-strength scoring system.
- 800-53A assessment-object or procedure coverage.
- Bulk control review.
- FedRAMP-specific statement rules.

## 4. Profile Contract

Each profile's `ssp-requirements.json` may declare a versioned
`implementation_statement_policy`. Built-in profile version **1.2.0** ships the
policy; workspaces pinned to **1.1.0** keep that bundle immutable. Legacy **1.0.0**
and **1.1.0** bundles without an explicit policy receive defaults matching prior
runtime behavior.

The policy should separate:

1. **Deterministic rules**
   - Reject OSCAL parameter insert syntax when the profile enables the rule.
   - Require a tracked question for unresolved parameterized controls when the
     profile enables the rule.
   - Preserve the current requirement that agent-authored non-unknown control
     claims have supporting facts.
   - Require a statement, documented gap, or tracked question before approval.

2. **Agent drafting and review rules**
   - Profile-owned instructions for implementation, responsibility, conditional
     scope, conditional timing, requirement coverage, ODPs, and inheritance.
   - Instructions are supplied to both initial generation and contextual
     control patches.

3. **Authority references**
   - Link the policy to sources already pinned by the profile manifest and to
     the SP 800-18 Table 1 control implementation coverage IDs.
   - Treat these links as provenance, not as a claim that NIST authored the
     product's statement template.

No new database column, control field, or portal editor is in scope for the
first increment (Section 9); each workspace already pins a stored profile bundle.

## 5. Validation Model

### Deterministic enforcement

Use deterministic checks only for facts the server can prove:

- Profile-allowed control IDs, statuses, and responsibilities.
- Valid supporting fact references.
- Forbidden unresolved OSCAL placeholder syntax.
- Presence of a statement, documented gap, or tracked question.
- Existing evidence-grounding and approval rules.

Do not use word counts or keyword checks as a proxy for statement quality.

### Agent semantic review

The control agent should evaluate whether an evidence-grounded statement covers
the profile-required elements and applicable control clauses. Missing semantic
content should produce a targeted existing control question, not invented text.

Semantic quality findings create targeted ISSO questions and advisories. They do
not create new model-controlled approval blockers. Existing deterministic
approval blockers remain authoritative, and the ISSO remains responsible for
final review.

### Human review

The ISSO may edit the statement, answer questions, add evidence, request a
targeted patch, and approve the completed revision. Model output remains draft
working material.

## 6. Minimal User Workflow

1. Generation receives the exact pinned control requirement, the profile's
   statement policy, and relevant evidence facts.
2. The agent drafts only supported content and returns existing structured
   control fields and supporting fact IDs.
3. Missing required information becomes a targeted control question.
4. The ISSO resolves questions, edits the statement, or requests a focused
   patch.
5. Existing deterministic reviewability checks govern approval.

This increment adds no new page, workflow state, bulk action, or generic
field-mapping UI.

## 7. Acceptance Criteria

### Profile behavior

- A profile bundle can declare a versioned implementation-statement policy.
- The current FISMA/NIST profile contains only rules attributable to its own
  pinned sources or clearly labeled product policy.
- No FedRAMP source or rule is imported into the current profile.
- A synthetic future profile can provide different agent instructions without a
  profile-ID conditional in Python or TypeScript.
- Legacy profile version `1.0.0` loads with defaults matching current behavior.

### Generation and editing

- Initial generation and contextual patches receive the same pinned policy.
- An agent cannot claim a non-unknown implementation, status, or responsibility
  without supporting facts under the current profile.
- Unsupported responsibility, scope, timing, or requirement clauses result in a
  targeted question or explicit gap, not fabricated content.
- Generated, patched, and directly edited statements continue to reject
  unresolved OSCAL parameter insert syntax under the current profile.
- Existing ODP and inherited/hybrid behavior remains intact.

### Approval and compatibility

- Existing deterministic reviewability and approval behavior does not weaken.
- Semantic agent findings create ISSO questions or advisories only; they cannot
  alone approve or reject a revision or add model-controlled approval blockers.
- Existing approved snapshots remain immutable and are not silently revalidated.
- No database migration or new control-workbench field is required.
- Technical specification, operator guidance, traceability, profile bundle,
  schema, builder, and tests change together with the runtime contract.

## 8. Implementation Phases

Implementation under Section 9 is delivered. Phases below record ownership and
verification scope for the shipped increment.

### Phase 1 — Profile schema and compatibility

**Ownership:** Composer 2.5 profile-contract subagent.

- Add the policy schema, typed loader, legacy defaults, source-reference
  validation, profile diff visibility, and bundle tests.
- Emit the current profile policy deterministically from the offline builder.
- This phase must preserve runtime behavior.

### Phase 2 — Generation and validation

**Ownership:** Composer 2.5 generation-contract subagent.

- Resolve the policy into `SelectedProfilePolicy`.
- Move current ODP and inherited/hybrid prompt instructions from global literals
  into the current profile.
- Apply the same policy to generation, patches, direct edits, metrics, and
  approval where deterministic flags apply.
- Add generation and contract tests.

Phase 1 and Phase 2 may be developed as independent bounded workstreams after
their shared data contract is frozen.

### Phase 3 — Documentation and integration gate

**Ownership:** separate Composer 2.5 documentation subagent plus Composer 2.5
integration-review subagent.

- Update Section 31, operator guidance, active workflow status, traceability,
  hard-stop wording if needed, and deployment contract tests.
- Verify bundle determinism, legacy compatibility, focused backend tests, portal
  regression tests, and contract consistency.
- Review the combined changes before the parent agent accepts them.

Cloud execution is preferred for these parallel workstreams. The parent agent
owns contract decisions, merge coherence, focused verification, and final
acceptance.

## 9. Approved Product-Owner Decisions

**Decision status:** Implemented (runtime contract and built-in **1.2.0** bundle).

1. **Semantic quality findings:** Agent semantic review produces targeted ISSO
   questions and advisories. These findings do not become model-controlled
   approval blockers; only existing deterministic approval rules may block
   approval.
2. **Profile versioning:** Ship the explicit implementation-statement policy in
   profile version `1.2.0`. Profile version `1.1.0` remains immutable for
   workspaces that pin it.
3. **Timing, frequency, and trigger:** Required only when the control
   requirement or evidenced implementation is time- or event-dependent—not for
   every control.
4. **First implementation scope:** Profile policy, agent prompts, validation,
   questions, tests, and documentation only. Out of scope for this increment:
   new portal UI, database changes, dedicated not-applicable or provider fields,
   SP 800-53A assessment coverage, bulk control review, and FedRAMP-specific
   rules.
5. **Approved snapshots:** Apply the standard to newly generated or patched
   statements; preserve existing approved snapshots without silent
   revalidation.

## 10. Non-Claims and Hard Stops

This improvement does not establish:

- Authority qualification under HS-001.
- Customer agency template parity or acceptance under HS-002.
- Qualified OSCAL SSP conformance.
- FedRAMP conformance.
- Assessor acceptance, control effectiveness, or an authorization decision.

The output remains a draft, evidence-grounded control implementation narrative
subject to ISSO and assessment-team review.
