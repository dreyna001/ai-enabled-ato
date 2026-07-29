# Control Accuracy Improvements

## Core Rule

- Missing LLM response = **Unaddressed**
- **N/A** = supported, justified, and ISSO-confirmed
- Never infer N/A from missing evidence

## Control Dispositions

| Disposition | Required support |
|---|---|
| Implemented | Evidence of the current implementation |
| Partially implemented | Supported implementation plus documented gap |
| Inherited | Common-control provider and control mapping |
| Hybrid | Inherited portion and system-specific portion |
| Not applicable | Applicability rationale and ISSO confirmation |
| Unaddressed | Missing evidence or unresolved responsibility |

## Improvements

1. **Deterministic baseline selection**
   - Calculate overall impact from confirmed C/I/A values.
   - Load the corresponding NIST 800-53 Low, Moderate, or High baseline.

2. **Agency overlay support**
   - Apply agency-required additions, removals, parameters, and guidance.
   - Record the overlay version used.

3. **Common-control catalog**
   - Import agency common controls and providers.
   - Map inherited controls before LLM generation.

4. **Control applicability rules**
   - Evaluate architecture, technology, data, hosting, and system-boundary facts.
   - Produce N/A candidates only when an explicit rule supports them.

5. **Control-level evidence retrieval**
   - Retrieve evidence separately by control or control family.
   - Send only relevant evidence to each generation task.

6. **Structured LLM output**
   - Require disposition, status, responsibility, statement, rationale, citations, and confidence.
   - Reject unsupported control IDs and invalid values.

7. **Evidence validation**
   - Implemented requires implementation evidence.
   - Inherited requires a provider mapping.
   - Hybrid requires inherited and system-specific descriptions.
   - N/A requires an applicability rationale.

8. **Unaddressed-control queue**
   - List controls lacking a valid disposition.
   - Group by family, owner, and missing information.

9. **Targeted follow-up questions**
   - Generate one question for each material information gap.
   - Deduplicate questions shared by multiple controls.

10. **Bulk ISSO review**
    - Bulk-confirm inherited controls from the same provider.
    - Bulk-review related N/A candidates.
    - Preserve per-control exceptions.

11. **Completion validation**
    - Require every selected control to have a valid disposition.
    - Block approval for unsupported, empty, or stale dispositions.

12. **Change-aware regeneration**
    - Reevaluate affected controls after new evidence, boundary changes, profile updates, or overlay updates.
    - Preserve ISSO-approved content unless explicitly reopened.

## Required Inputs

- Confirmed C/I/A categorization
- System boundary and architecture
- Hardware, software, service, and interface inventories
- Data types and information flows
- Identity and access design
- Logging and monitoring design
- Backup and recovery procedures
- Vulnerability and patching procedures
- Incident and contingency procedures
- Agency common-control catalog
- Agency overlays and control parameters
- Scanner, test, configuration, and operational evidence

## Recommended UI States

- Proposed
- Confirmed
- Unaddressed
- Needs evidence
- Needs owner response
- Stale after change
- ISSO approved
