import { describe, expect, it } from "vitest";
import { mapWorkspaceEnvelope } from "./sspWorkspace";

describe("mapWorkspaceEnvelope", () => {
  it("maps persisted metrics inputs without model-calculated counts", () => {
    const workspace = mapWorkspaceEnvelope({
      workspace_id: "10000000-0000-4000-8000-000000000001",
      system_id: "10000000-0000-4000-8000-000000000002",
      status: "working",
      system: { display_name: "Case Portal", external_system_id: null },
      profile: {
        profile_version_id: "10000000-0000-4000-8000-000000000003",
        profile_id: "agency-fisma-nist-sp800-53-rev5",
        version: "5.2.0-1",
        status: "active",
        impact_level: "moderate",
      },
      current_revision: {
        revision_id: "10000000-0000-4000-8000-000000000004",
        version: 2,
        status: "working",
        content_sha256: "a".repeat(64),
        created_at: "2026-07-27T12:00:00Z",
        content: {
          facts: [
            {
              key: "system.purpose",
              value: "Manages internal cases.",
              provenance: "isso_entered",
              evidence: [],
              state: "active",
            },
          ],
          sections: [
            {
              key: "system.purpose",
              title: "System Purpose",
              content: "Manages internal cases.",
              state: "edited",
              evidence: [],
            },
          ],
          controls: [
            {
              control_id: "AC-1",
              title: "Policy and Procedures",
              implementation_status: "implemented",
              implementation_statement: "The agency maintains the policy.",
              responsibility: "system_specific",
              state: "reviewed",
              evidence: [],
              unresolved_reason: null,
            },
          ],
          questions: [],
        },
      },
      evidence: [],
      approvals: [],
      agent_patches: [],
      requirements: [
        {
          key: "system.purpose",
          value_type: "string",
          required: true,
          enum_values: [],
          min_length: 20,
          evidence_required_for_agent_value: true,
        },
      ],
      satisfied_requirement_ids: ["system.purpose"],
      metrics: {
        evidence: 0,
        processed_evidence: 0,
        screenshots: 0,
        selected_controls: 1,
        controls_drafted: 1,
        partial_controls: 0,
        open_questions: 0,
        evidence_links: 0,
        satisfied_required_items: 1,
        total_required_items: 1,
        ssp_completion_percent: 100,
      },
    });

    expect(workspace.name).toBe("Case Portal");
    expect(workspace.purpose).toBe("Manages internal cases.");
    expect(workspace.sections[0]?.satisfiedRequirementIds).toEqual([
      "system.purpose",
    ]);
    expect(workspace.controls[0]?.id).toBe("AC-1");
  });
});
