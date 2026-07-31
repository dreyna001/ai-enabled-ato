# SSP Workspace Operator Guide

Use this workflow for one system and one SSP working package.

The built-in agency profile is `agency-fisma-nist-sp800-53-rev5` version **1.2.0**
(NIST SP 800-53 catalog **5.2.0**, NIST SP 800-18 Rev. 2 Table 1 coverage
metadata, profile-bound `implementation_statement_policy`). Workspaces pinned to
**1.1.0** or **1.0.0** keep their immutable bundles. Generated content is draft
working material only. **HS-001** (authority qualification) and **HS-002** (agency
template parity) remain open—exports are not qualified authority products,
customer template packs, qualified OSCAL SSP or conformance claims, privacy plans,
or C-SCRM plans. Draft OSCAL JSON export is structural working material only.

1. Open `http://localhost:5173`.
2. Sign in.
3. Open **SSP Workspace**.
4. Select **New system**.
5. Enter the system name.
6. Select **Create workspace**.
7. Open **Intake & evidence**.
8. Upload the available system artifacts:
   - Architecture and data-flow diagrams
   - System descriptions
   - Configuration exports
   - Policies and procedures
   - Inventories
   - Screenshots
   - Operational or support documentation
9. Wait for every uploaded artifact to show **Processed** or **Failed**.
10. Review failed artifacts and replace them if needed.
11. Remove incorrect or unwanted artifacts before generating content.
12. Open **Overview**.
13. Select **Generate or update documents**.
    - LLM input: extracted evidence facts, source references, pinned profile SSP
      items, provisional control baseline, existing questions, and the profile's
      implementation-statement policy instructions.
    - LLM output: supported SSP text, control implementation statements, C/I/A
      recommendations, rationales, and follow-up questions.
14. Wait for generation to complete.
15. Review the **System categorization** proposal on **Overview**.
16. Confirm or edit:
    - Confidentiality impact and rationale
    - Integrity impact and rationale
    - Availability impact and rationale
17. Select **Confirm categorization**.
    - The overall impact is calculated as the highest C/I/A value.
    - The control baseline is updated automatically.
    - No LLM call occurs.
18. Open **SSP document**.
19. Review each populated SSP section and its evidence references.
20. Edit unsupported, incomplete, or incorrect text and save the section.
21. Use **Ask agent** only when evidence-supported rewriting or synthesis is needed.
    - The agent returns a proposed edit for review; it does not silently overwrite the section.
22. Open **Controls**.
23. Clear **Needs attention only** to see controls already populated by generation.
24. Search or select a control.
25. Review its implementation status, responsibility, statement, and evidence.
    - Status and responsibility choices come from the pinned profile allowlists.
    - Statement drafting follows the pinned profile's implementation-statement
      policy (implementation, responsibility, scope when material, timing only when
      time- or event-dependent, and requirement coverage). Agent semantic quality
      notes are advisory; deterministic approval rules remain authoritative.
    - For **inherited** or **hybrid** responsibility, the statement must describe
      only what evidence supports (provider or common portions). Do not ask the
      agent to invent inheritance boundaries or provider scope.
    - Controls whose profile requirement text includes organization-defined
      parameter placeholders may show follow-up questions. Answer those on
      **Questions** before expecting a complete statement.
26. Edit and save the control when needed.
27. Use **Ask agent** for a selected control only when additional evidence-grounded drafting is needed.
    - The agent must not leave OSCAL `{{ insert: param, ... }}` placeholder syntax
      in the implementation statement; if generation rejects placeholder text, fix
      the statement directly or answer the linked question and regenerate or patch.
28. Open **Questions**.
29. Enter confirmed answers directly and select **Save answer**.
    - For organization-defined parameter questions, provide the agency-selected
      value in plain language (not OSCAL insert tokens).
    - Simple answers do not use an LLM.
    - Matching SSP fields and duplicate questions are updated automatically when supported.
30. Upload additional evidence when important information is still missing.
31. Return to **Overview** and select **Generate or update documents** again.
    - This is another LLM call using the updated evidence and workspace content.
32. Recheck the SSP, controls, categorization, and open questions.
33. Open **Review & export**.
34. Confirm evidence processing is complete and the working revision is ready.
35. Select **Approve** to create the ISSO-approved revision snapshot.
    - No LLM call occurs.
36. Export the approved package as **DOCX**, **JSON**, or **Export draft OSCAL JSON**.
    - No LLM call occurs.
    - **Draft OSCAL JSON** is validated against the pinned official NIST OSCAL
      1.2.2 SSP schema only. It is not a qualified OSCAL product, does not prove
      agency template parity (**HS-002**), and does not close authority review
      (**HS-001**). Missing SSP sections appear as explicit unresolved text in
      the export rather than invented content.
37. Optional — **Agency-shaped draft** (customer agency `.docx` template only):
    - Requires step **35** (ISSO-approved revision). Upload is disabled until approval.
    - Upload the customer-provided agency `.docx` in **Review & export** → **Generate agency-shaped draft**.
    - The API runs synchronously in the request: template outline extraction, agent mapping plan, deterministic server render (with draft notice), and reviewer exceptions. There is no background worker.
    - Review **blocker** and **warning** exceptions on the render card. **Review failed** means at least one blocker; approval stays disabled until blockers are cleared.
    - Select **Preview draft** to download the draft DOCX before render approval (available for awaiting approval, review failed, or approved renders per policy).
    - When acceptable, select **Approve mapping and render** (ISSO). Warnings may remain; blockers must not.
    - Select **Download approved draft** only after render approval.
    - Re-uploading the same template file against the same approved revision returns the cached render (same workspace, revision, and template digest) without repeating model work.
    - If the ISSO edits SSP content after approval, create a new working revision and approve again before a new agency render.
    - **HS-002** remains open: output does not prove agency template parity; the customer template owner must review mapping and output; external acceptance evidence is required.

## Operating Notes

- Treat generated content as a working draft until the ISSO approves it.
- The provisional Moderate baseline is not a confirmed system categorization.
- Generated controls may be hidden while **Needs attention only** is selected.
- Add evidence and regenerate instead of asking the model to invent missing facts.
- Direct edits, categorization confirmation, approval, and export are deterministic.
- Draft OSCAL JSON export requires ISSO approval and fails closed if authority
  digest verification for the pinned schema bundle fails.
- Agency-shaped draft generation uses LLM calls for mapping and review only; render copy is server-side deterministic.
- Profile coverage metrics count required SSP items satisfied under the pinned
  profile rules; they do not measure authorization readiness or control effectiveness.
- Administrators may import and activate newer profile bundles offline; only the
  agency Rev. 5 profile is supported today. Use **migrate profile** when moving a
  workspace to a newer pinned version.

## Agency-shaped draft — failures and retry

| Situation | Operator action |
| --- | --- |
| Upload disabled | Complete ISSO **Approve** for the current revision first. |
| Upload rejected (not `.docx`, empty, over size limit, invalid DOCX/ZIP) | Fix the file and upload again. |
| Mapping or review model error | Retry upload after model routing and policy are healthy (**HS-004** blocks production customer model calls). |
| **Review failed** (blockers) | Fix SSP content and/or use a corrected template; upload again to create a new render. |
| Render **rejected** by ISSO | Upload again when ready; prior rejected rows remain auditable. |
| Same template and approved revision | Expect a cache hit; no new mapping unless template bytes or revision change. |
| Approved mapping exists for same template digest and profile version | Server may reuse the approved mapping plan when generating a new render for a different approved revision in the same workspace. |
| Production customer uploads | Blocked until an approved malware scanner is configured (**HS-005**); safe DOCX preflight is not scanning. |
