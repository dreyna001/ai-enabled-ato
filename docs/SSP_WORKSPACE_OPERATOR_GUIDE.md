# SSP Workspace Operator Guide

Use this workflow for one system and one SSP working package.

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
    - LLM input: extracted evidence facts, source references, SSP section definitions, provisional control baseline, and existing questions.
    - LLM output: supported SSP text, control implementation statements, C/I/A recommendations, rationales, and follow-up questions.
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
26. Edit and save the control when needed.
27. Use **Ask agent** for a selected control only when additional evidence-grounded drafting is needed.
28. Open **Questions**.
29. Enter confirmed answers directly and select **Save answer**.
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
36. Export the approved package as **DOCX** or **JSON**.
    - No LLM call occurs.

## Operating Notes

- Treat generated content as a working draft until the ISSO approves it.
- The provisional Moderate baseline is not a confirmed system categorization.
- Generated controls may be hidden while **Needs attention only** is selected.
- Add evidence and regenerate instead of asking the model to invent missing facts.
- Direct edits, categorization confirmation, approval, and export are deterministic.
