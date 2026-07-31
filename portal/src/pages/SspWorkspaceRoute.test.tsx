import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SspWorkspaceRoute } from "@/pages/SspWorkspaceRoute";
import type { SspWorkspace } from "@/sspWorkspaceTypes";
import { DEFAULT_CONTROL_RESPONSE_OPTIONS } from "@/sspWorkspaceTypes";
import type { SessionInfo } from "@/types";

const apiMocks = vi.hoisted(() => ({
  listSspProfiles: vi.fn(),
  listSspWorkspaces: vi.fn(),
  createAgencyDocxRender: vi.fn(),
  previewAgencyDocxRender: vi.fn(),
  rejectAgencyDocxRender: vi.fn(),
}));

vi.mock("@/api/sspWorkspace", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/sspWorkspace")>()),
  ...apiMocks,
}));

const session: SessionInfo = {
  actor_id: "isso@example.gov",
  groups: ["isso"],
  csrf_token: "csrf-token",
  portal_origin: "http://127.0.0.1:5173",
};

function workspace(id: string, name: string): SspWorkspace {
  return {
    id,
    name,
    purpose: "",
    hosting: "",
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
    authorizationPath: "",
    profile: {
      id: "profile-1",
      name: "NIST SP 800-53 Rev. 5",
      version: "1.0.0",
      baseline: "Moderate",
    },
    controlResponse: DEFAULT_CONTROL_RESPONSE_OPTIONS,
    revisionId: `revision-${id}`,
    revisionUpdatedAt: "2026-07-28T00:00:00Z",
    currentContentHash: "a".repeat(64),
    approvedContentHash: "a".repeat(64),
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
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SspWorkspaceRoute", () => {
  it("starts with a new-system form and keeps the system name editable", async () => {
    apiMocks.listSspWorkspaces.mockResolvedValue([]);
    apiMocks.listSspProfiles.mockResolvedValue([
      {
        profile_version_id: "22222222-2222-4222-8222-222222222222",
        profile_id: "nist-rev5",
        version: "1.0.0",
        status: "active",
        display_name: "NIST SP 800-53 Rev. 5",
      },
    ]);

    render(<SspWorkspaceRoute session={session} />);

    const systemName = await screen.findByLabelText("System name");
    expect(systemName).toBeEnabled();
    expect(screen.queryByLabelText("Existing system")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Impact level")).not.toBeInTheDocument();

    fireEvent.change(systemName, { target: { value: "New agency system" } });

    await waitFor(() => {
      expect(systemName).toHaveValue("New agency system");
    });
  });

  it("switches between existing systems and can return from the new-system form", async () => {
    apiMocks.listSspWorkspaces.mockResolvedValue([
      workspace("workspace-1", "Grants System"),
      workspace("workspace-2", "Case Review System"),
    ]);
    apiMocks.listSspProfiles.mockResolvedValue([
      {
        profile_version_id: "22222222-2222-4222-8222-222222222222",
        profile_id: "nist-rev5",
        version: "1.0.0",
        status: "active",
        display_name: "NIST SP 800-53 Rev. 5",
      },
    ]);

    render(<SspWorkspaceRoute session={session} />);

    await screen.findByRole("heading", { name: "Grants System" });
    fireEvent.change(screen.getByLabelText("Open system"), {
      target: { value: "workspace-2" },
    });
    expect(
      screen.getByRole("heading", { name: "Case Review System" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New system" }));
    expect(screen.getByText("Create system workspace")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.getByRole("heading", { name: "Case Review System" }),
    ).toBeInTheDocument();
  });

  it("uploads agency templates from the review view", async () => {
    const approved = workspace("workspace-1", "Grants System");
    apiMocks.listSspWorkspaces.mockResolvedValue([approved]);
    apiMocks.listSspProfiles.mockResolvedValue([
      {
        profile_version_id: "22222222-2222-4222-8222-222222222222",
        profile_id: "nist-rev5",
        version: "1.0.0",
        status: "active",
        display_name: "NIST SP 800-53 Rev. 5",
      },
    ]);
    apiMocks.createAgencyDocxRender.mockImplementation(
      async (_session, current: SspWorkspace, file: File) => ({
        ...current,
        agencyDocxRenders: [
          {
            id: "render-new",
            profileVersionId: "profile-v1",
            sourceRevisionId: current.revisionId,
            sourceRevisionSha256: current.currentContentHash,
            templateSha256: "d".repeat(64),
            templateFilename: file.name,
            outputSha256: "e".repeat(64),
            status: "awaiting_approval",
            createdBy: "isso@example.gov",
            createdAt: "2026-07-28T12:00:00Z",
            resolvedBy: null,
            resolvedAt: null,
            mappingSummary: "Mapped placeholders.",
            mappingExceptions: [],
            reviewSummary: "Ready for review.",
            reviewIssues: [],
            canApprove: true,
            canPreview: true,
            canDownload: false,
          },
        ],
      }),
    );

    render(<SspWorkspaceRoute session={session} />);

    await screen.findByRole("heading", { name: "Grants System" });
    fireEvent.click(
      screen.getByRole("button", { name: /Review & export/i }),
    );

    const file = new File(["docx"], "agency-template.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(screen.getByLabelText("Agency template DOCX file"), {
      target: { files: [file] },
    });

    await waitFor(() => {
      expect(apiMocks.createAgencyDocxRender).toHaveBeenCalledWith(
        session,
        approved,
        file,
      );
    });
    expect(screen.getByText("agency-template.docx")).toBeInTheDocument();
  });
});
