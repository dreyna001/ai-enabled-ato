import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewExportPanel } from "@/components/ssp-workspace/ReviewExportPanel";
import type { SspWorkspaceMetrics } from "@/utils/sspWorkspaceMetrics";
import type { SspWorkspace } from "@/sspWorkspaceTypes";
import { DEFAULT_CONTROL_RESPONSE_OPTIONS } from "@/sspWorkspaceTypes";

const workspace: SspWorkspace = {
  id: "workspace-1",
  name: "Grants Intake Management",
  purpose: "Manage federal grant applications.",
  hosting: "Agency-owned cloud",
  impactLevel: "Moderate",
  provisionalImpactLevel: "moderate",
  categorization: {
    confidentiality: "moderate",
    integrity: "moderate",
    availability: "low",
    confidentialityRationale: "",
    integrityRationale: "",
    availabilityRationale: "",
    confirmed: true,
  },
  authorizationPath: "Agency ATO",
  profile: {
    id: "nist-rev5",
    name: "Agency FISMA — NIST SP 800-53 Rev. 5",
    version: "2026.1",
    baseline: "Moderate",
  },
  controlResponse: DEFAULT_CONTROL_RESPONSE_OPTIONS,
  revisionId: "rev-4",
  revisionUpdatedAt: "2026-07-27T12:00:00Z",
  currentContentHash: "hash-4",
  approvedContentHash: null,
  processingJobsTerminal: true,
  revisionSaved: true,
  internallyConsistent: true,
  requirements: [],
  evidence: [],
  sections: [],
  controls: [],
  questions: [],
  patches: [],
  agencyDocxRenders: [],
};

function metricsFixture(approved: boolean): SspWorkspaceMetrics {
  return {
    evidence: 0,
    processedEvidence: 0,
    screenshots: 0,
    selectedControls: 0,
    controlsDrafted: 0,
    partialControls: 0,
    openQuestions: 0,
    evidenceLinks: 0,
    requiredItems: 0,
    satisfiedRequiredItems: 0,
    sspCompletion: 0,
    approved,
    requiredItemsResolved: true,
    controlsResolved: true,
    reviewable: true,
  };
}

afterEach(cleanup);

describe("ReviewExportPanel", () => {
  it("disables OSCAL export until the revision is approved", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={metricsFixture(false)}
        onExport={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Export draft OSCAL JSON" }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        "Approve the current content before exporting its immutable snapshot.",
      ),
    ).toBeInTheDocument();
  });

  it("calls onExport with oscal-json when approved", () => {
    const onExport = vi.fn();
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={metricsFixture(true)}
        onExport={onExport}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Export draft OSCAL JSON" }),
    );

    expect(onExport).toHaveBeenCalledWith("oscal-json");
  });

  it("shows the draft OSCAL disclaimer near the export action", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={metricsFixture(true)}
        onExport={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Schema-checked draft; not qualified/customer-ready.",
      ),
    ).toBeInTheDocument();
  });
});
