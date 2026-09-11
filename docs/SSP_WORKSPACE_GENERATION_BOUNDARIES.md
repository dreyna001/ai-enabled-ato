# SSP Workspace: Generation Boundaries and Agent Split Analysis

**Last updated:** 2026-09-06

This document records what the internal SSP workspace must **not** treat as
authoritative generated output, and evaluates whether splitting SSP drafting
from control drafting into separate model steps would improve quality.

Related operator workflow: [`SSP_WORKSPACE_OPERATOR_GUIDE.md`](SSP_WORKSPACE_OPERATOR_GUIDE.md)

---

## What is required that we do NOT generate

Everything below stays **human-entered**, **deterministic**, or **explicitly
non-qualifying**. LLM output in these areas is at most a **proposal** until the
ISSO acts.

### ISSO attestation gates (no LLM on the confirm action)

| Item | What the ISSO locks | Why it is not generated |
| --- | --- | --- |
| **Confirm categorization** | Final FIPS 199 C/I/A, per-axis rationale, evidence links | Sets the official impact level and control baseline |
| **Confirm system definition** | Authorization boundary, component inventory, interconnection register | Table 1 boundary and architecture register |
| **Confirm information types** | SP 800-60 mappings, descriptions, impact adjustments | FIPS 199 information-type register |
| **Approve** | Working revision snapshot | Single ISSO approval action; no model call |
| **Export** | DOCX, JSON, draft OSCAL JSON | Deterministic render from approved snapshot |

Agent steps may **propose** categorization, diagram-derived boundary/components,
or information-type drafts. None of those become authoritative until the
corresponding **Confirm** action.

### Human-entered values the model must not invent

| Item | Operator behavior | Model boundary |
| --- | --- | --- |
| **Organization-defined parameters (ODPs)** | ISSO answers on **Questions** → **Save answer** | Model may ask; must not pick agency policy values |
| **Inherited / hybrid responsibility scope** | ISSO describes only evidence-supported provider vs system portions | Do not invent inheritance boundaries or provider scope |
| **Direct section / control edits** | ISSO edits and saves | No silent overwrite; **Ask agent** returns a patch for review |
| **Simple question answers** | Typed answers saved without LLM | Deterministic merge into workspace state |

### Out of scope or non-qualifying products

| Item | Status |
| --- | --- |
| **Authorization decision** | Not produced by this workflow |
| **Authority-qualified SSP / OSCAL** | Blocked by **HS-001** |
| **Agency template parity** | Blocked by **HS-002**; customer template owner must review |
| **Privacy plan completeness** | Not claimed |
| **C-SCRM plan completeness** | Not claimed |
| **POA&M weakness creation** | Requires explicit human confirmation (package workflow) |

### Evidence and completeness rules (generate may run; output must not fabricate)

| Rule | Behavior |
| --- | --- |
| **Missing facts** | Leave narrative empty, use `unknown` status/responsibility, or open a targeted question |
| **Ungrounded agent claims** | Approval blocked when agent control metadata lacks evidence references (profile policy) |
| **Unresolved required SSP items** | Must be satisfied or tracked as open questions before approve |
| **Unresolved control statements** | Must be filled or tracked as open questions before approve (profile policy) |
| **OSCAL `{{ insert: param, ... }}` syntax** | Must not appear in final implementation statements |
| **Stuck evidence** | Approve blocked while artifacts remain `uploaded` or `processing` |

### Draft-only until ISSO approval

All model-produced SSP text, control statements, categorization proposals, diagram
analysis drafts, and **Ask agent** patches are **working material** until the ISSO
approves the revision. Exports after approval are deterministic snapshots, not new
model calls.

### Hard gate summary (Approve)

Approve requires all of the following to be true. None are satisfied by generation
alone:

1. Categorization **confirmed** (not stale)
2. System definition **confirmed** (not stale)
3. Information types **confirmed** (not stale)
4. All evidence **processed** (or failed and removed)
5. Required SSP sections and controls **resolved or explicitly questioned**
6. No **ungrounded agent control metadata** (per profile policy)

Generate is intentionally allowed **before** items 1–3 are confirmed (provisional
Moderate baseline until categorization is confirmed). That supports early drafting;
it does not replace the attestation gates above.

---

## Should SSP and controls be separate agents?

This section answers a **quality** question, not a manual-ATO compliance
question. Manual ATO does not require separate agents. The operator guide uses
one **Generate or update documents** step. The question is whether splitting
would produce **better model results**.

### Current design

| Step | Scope | Output |
| --- | --- | --- |
| **Generate or update documents** | SSP sections + controls + questions (+ optional CIA proposal) | One large structured JSON response |
| **Analyze from evidence** | Categorization only | Small categorization proposal |
| **Analyze from diagram** | System definition draft | Boundary / components proposal |
| **Ask agent** | One section or control (+ instruction) | Bounded patch |

Implementation: `src/ato_service/ssp_workspace/generation.py` (`generate_initial_ssp`,
`generate_categorization_proposal`, `generate_contextual_patch`).

The profile already carries **separate policy blocks** inside one generation
call: SSP required items vs `implementation_statement_policy` (control enums,
statement content rules, ODP rules, inherited/hybrid rules, semantic review).

### Why one combined generate call helps

- **Narrative consistency.** SSP sections (purpose, boundary narrative, architecture)
  and control statements should describe the same system. One pass sees the same
  evidence and can align story and implementation claims.
- **Shared question budget.** Material gaps can be deduplicated across SSP and
  controls instead of each agent opening redundant questions.
- **Operator simplicity.** One wait, one retry, one audit event for a first draft.
- **Lower cost and latency** for the common “empty workspace → first draft” path.

### Why splitting would likely improve results

The combined call is the weakest link for **model quality**, not for manual ATO.

| Factor | Combined call problem | Split benefit |
| --- | --- | --- |
| **Output size** | Agency profile: ~33 SSP items + full baseline control set in one JSON | Smaller schemas per call → fewer truncation/omit errors, easier repair |
| **Task complexity** | One prompt mixes Table 1 narrative drafting with per-control status, responsibility, ODP handling, and evidence citation | Dedicated system prompt and output contract per task |
| **Validation / retry** | One failed control parse can reject or dilute an entire run | Independent retry budgets per artifact type |
| **Policy focus** | Control rules (`implementation_statement_policy`) compete with SSP section constraints in one `task` string | Control agent loads only statement policy; SSP agent loads only section catalog |
| **Timing** | Controls generated before baseline / ODP / system context are confirmed | Control pass can run **after** confirms with confirmed context injected |

### Recommended split (quality-first, not required today)

If the goal is **better results**, prefer **sequenced specialized passes** over
two peer agents run in parallel with no shared state:

```
1. Evidence processed
2. (Optional) Categorization analyze → ISSO confirm          [already separate]
3. (Optional) Diagram analyze → ISSO confirm system definition [already separate]
4. ISSO confirm information types                            [human only]
5. SSP narrative agent     → Table 1 sections only
6. Control statement agent → baseline controls only, fed:
     - evidence facts
     - confirmed categorization + baseline
     - confirmed system definition + information types
     - drafted SSP sections (for consistency)
     - answered ODPs / open questions
7. Ask agent patches       → unchanged, per target
```

**Do not** treat categorization, system definition, or information types as
“just another generate sub-task.” Those already have the right pattern: **propose
→ ISSO confirm → deterministic lock**. Splitting SSP vs controls does not replace
those gates.

### What to split first (highest ROI)

1. **Controls out of the bulk generate** — largest output surface (~hundreds of
   controls), strictest profile policy, most ODP and inheritance edge cases.
2. **Keep SSP sections in a narrower first pass** — establishes system story for
   the control agent without carrying control JSON in the same response.
3. **Leave Ask agent and categorization analyze as-is** — already bounded and
   task-specific.

A lighter-weight alternative without new UI buttons: keep one **Generate** button
but run **two sequential model calls server-side** (SSP pass, then controls pass),
merging into one revision save. That improves quality without changing operator
steps.

### What not to split (diminishing returns)

- **Export, approve, confirm actions** — must stay deterministic.
- **Question answers** — human entry; no model.
- **Per-control Ask agent** — already scoped; splitting further per control family
  rarely helps unless statements are batched by baseline slice for token limits.

### Skills / rules mapping (if split)

| Pass | Prompt focus | Rules source |
| --- | --- | --- |
| SSP narrative | Table 1 sections, evidence grounding, omit unsupported sections | Profile `ssp_required_items`, section constraints |
| Control statements | Status, responsibility, statement content, ODP detection, inherited/hybrid | Profile `implementation_statement_policy` |
| Categorization | FIPS 199 impacts and rationales only | Existing categorization contract |
| Contextual patch | Single target + instruction | Combined policy, minimal scope |

Separate “agents” here means **separate prompts, schemas, parsers, and retry
policy** — not necessarily separate Cursor skills files or separate product
features. The profile bundle already holds the authoritative rules; split passes
should **load subsets** of that bundle rather than duplicating policy in skills.

### Practical verdict

| Goal | Recommendation |
| --- | --- |
| **Match manual ATO** | Current design is sufficient; attestation gates are the real boundary |
| **Better first-draft quality** | Split controls from SSP (sequential, controls second) |
| **Best control accuracy** | Run control pass only after categorization, system definition, information types, and key ODP answers are confirmed |
| **Minimal product change** | One Generate button, two server-side model calls, merge result |

CIA is not special in this analysis. Categorization is already isolated because
its output **changes the baseline** and requires ISSO confirm. System definition
and information types follow the same propose/confirm pattern. The quality gap is
mainly **bulk SSP + controls in one JSON**, not missing CIA isolation.

---

## References

- Operator steps: [`SSP_WORKSPACE_OPERATOR_GUIDE.md`](SSP_WORKSPACE_OPERATOR_GUIDE.md)
- Workflow plan: [`NEW_INTERNAL_SSP_WORKFLOW_PLAN.md`](NEW_INTERNAL_SSP_WORKFLOW_PLAN.md)
- Generation contracts: `src/ato_service/ssp_workspace/generation.py`,
  `src/ato_service/ssp_workspace/generation_contracts.py`
- Approve gates: `approve_workspace_revision()` in
  `src/ato_service/ssp_workspace/service.py`
