import { describe, expect, it } from "vitest";
import type { SspWorkspace } from "@/sspWorkspaceTypes";
import { calculateSspWorkspaceMetrics } from "@/utils/sspWorkspaceMetrics";

function workspaceFixture(): SspWorkspace {
  return {
    id: "workspace-1",
    name: "Grants Intake Management",
    purpose: "Manage federal grant applications.",
    hosting: "Agency-owned cloud",
    impactLevel: "Moderate",
    provisionalImpactLevel: "moderate",
    categorization: {
      confidentiality: "moderate",
      integrity: "moderate",
      availability: "moderate",
      confidentialityRationale: "Confirmed rationale.",
      integrityRationale: "Confirmed rationale.",
      availabilityRationale: "Confirmed rationale.",
      confirmed: true,
    },
    authorizationPath: "Agency ATO",
    profile: {
      id: "nist-rev5",
      name: "Agency FISMA — NIST SP 800-53 Rev. 5",
      version: "2026.1",
      baseline: "Moderate",
    },
    revisionId: "rev-4",
    revisionUpdatedAt: "2026-07-27T12:00:00Z",
    lastAgentUpdateAt: "2026-07-27T11:30:00Z",
    currentContentHash: "hash-4",
    approvedContentHash: "hash-4",
    processingJobsTerminal: true,
    revisionSaved: true,
    internallyConsistent: true,
    requirements: [
      { id: "purpose", label: "System purpose", required: true },
      { id: "boundary", label: "Authorization boundary", required: true },
      { id: "optional", label: "Optional note", required: false },
    ],
    evidence: [
      {
        id: "artifact-1",
        name: "architecture.png",
        mediaType: "image/png",
        state: "processed",
        uploadedAt: "2026-07-27",
      },
      {
        id: "artifact-2",
        name: "policy.pdf",
        mediaType: "application/pdf",
        state: "failed",
        uploadedAt: "2026-07-27",
      },
    ],
    sections: [
      {
        id: "section-1",
        title: "System Description",
        content: "The system manages grants.",
        state: "generated",
        requirementIds: ["purpose", "boundary"],
        satisfiedRequirementIds: ["purpose"],
        evidenceLinks: [
          { id: "link-1", artifactId: "artifact-1", locator: "image:1" },
        ],
      },
    ],
    controls: [
      {
        id: "AC-2",
        title: "Account Management",
        family: "Access Control",
        state: "partial",
        implementationStatus: "Implemented",
        responsibility: "Hybrid",
        statement: "The application uses agency identity services.",
        evidenceLinks: [
          { id: "link-2", artifactId: "artifact-1", locator: "image:1" },
        ],
        unresolvedReason: "Account review frequency is unknown.",
      },
      {
        id: "AU-2",
        title: "Event Logging",
        family: "Audit and Accountability",
        state: "generated",
        implementationStatus: "Implemented",
        responsibility: "System-specific",
        statement: "The system records authentication events.",
        evidenceLinks: [
          { id: "link-3", artifactId: "artifact-1", locator: "image:2" },
        ],
      },
    ],
    questions: [
      {
        id: "question-1",
        targetType: "ssp_section",
        targetId: "section-1",
        prompt: "What services are inside the boundary?",
        owner: "ISSO",
        state: "open",
      },
      {
        id: "question-2",
        targetType: "control",
        targetId: "AC-2",
        prompt: "How often are accounts reviewed?",
        owner: "System owner",
        state: "open",
      },
      {
        id: "question-3",
        targetType: "control",
        targetId: "AU-2",
        prompt: "Resolved question",
        owner: "System owner",
        state: "answered",
      },
    ],
    patches: [],
  };
}

describe("calculateSspWorkspaceMetrics", () => {
  it("derives coverage, inventory, questions, links, and review state from records", () => {
    const metrics = calculateSspWorkspaceMetrics(workspaceFixture());

    expect(metrics).toMatchObject({
      evidence: 2,
      processedEvidence: 1,
      screenshots: 1,
      selectedControls: 2,
      controlsDrafted: 2,
      partialControls: 1,
      openQuestions: 2,
      evidenceLinks: 3,
      requiredItems: 2,
      satisfiedRequiredItems: 1,
      sspCompletion: 50,
      approved: true,
      requiredItemsResolved: true,
      controlsResolved: true,
      reviewable: true,
    });
  });

  it("does not claim completion when the profile has no required items", () => {
    const workspace = workspaceFixture();
    workspace.requirements = [];
    workspace.sections = [];

    const metrics = calculateSspWorkspaceMetrics(workspace);

    expect(metrics.sspCompletion).toBe(0);
    expect(metrics.requiredItems).toBe(0);
  });

  it("requires the approval hash to match the current revision", () => {
    const workspace = workspaceFixture();
    workspace.approvedContentHash = "older-hash";

    expect(calculateSspWorkspaceMetrics(workspace).approved).toBe(false);
  });

  it("is not reviewable when a required item has no content or tracked question", () => {
    const workspace = workspaceFixture();
    workspace.questions = workspace.questions.filter(
      (question) => question.targetType !== "ssp_section",
    );

    const metrics = calculateSspWorkspaceMetrics(workspace);

    expect(metrics.requiredItemsResolved).toBe(false);
    expect(metrics.reviewable).toBe(false);
  });
});
