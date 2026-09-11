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
    confidentialityEvidence: [],
    integrityEvidence: [],
    availabilityEvidence: [],
    status: "confirmed",
    confirmed: true,
    agentSuggestion: null,
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
  systemDefinition: {
    status: "confirmed",
    boundaryNarrative:
      "The authorization boundary includes all production application hosts.",
    diagramLinks: [],
    components: [],
    interconnections: [],
    proposal: null,
  },
  informationTypes: {
    status: "confirmed",
    entries: [],
    confirmed: true,
  },
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
    agentControlsGrounded: true,
    categorizationConfirmed: true,
    categorizationStale: false,
    systemDefinitionConfirmed: true,
    systemDefinitionStale: false,
    informationTypesConfirmed: true,
    informationTypesStale: false,
    informationTypesCount: 0,
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

  it("shows stale system definition messaging before approve", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={{
          ...metricsFixture(false),
          systemDefinitionConfirmed: false,
          systemDefinitionStale: true,
          reviewable: false,
        }}
        onApprove={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Re-confirm the system definition after structured boundary, component, or interconnection edits before approving.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve working content" }),
    ).toBeDisabled();
  });

  it("shows unconfirmed system definition messaging before approve", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={{
          ...metricsFixture(false),
          systemDefinitionConfirmed: false,
          systemDefinitionStale: false,
          reviewable: false,
        }}
        onApprove={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Confirm the authorization boundary, component inventory, and interconnection register before approving.",
      ),
    ).toBeInTheDocument();
  });

  it("shows stale information types messaging before approve", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={{
          ...metricsFixture(false),
          informationTypesConfirmed: false,
          informationTypesStale: true,
          reviewable: false,
        }}
        onApprove={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Re-confirm SP 800-60 information type mappings after structured edits before approving.",
      ),
    ).toBeInTheDocument();
  });

  it("shows unconfirmed information types messaging before approve", () => {
    render(
      <ReviewExportPanel
        workspace={workspace}
        metrics={{
          ...metricsFixture(false),
          informationTypesConfirmed: false,
          informationTypesStale: false,
          reviewable: false,
        }}
        onApprove={vi.fn()}
      />,
    );

    expect(
      screen.getByText(
        "Confirm SP 800-60 information type mappings before approving.",
      ),
    ).toBeInTheDocument();
  });
});
