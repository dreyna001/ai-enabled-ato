import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowPage } from "@/pages/WorkflowPage";
import { ApiError } from "@/api/client";
import type { IntakeReport, PackageDraftDocument, PackageRevision, SessionInfo } from "@/types";

const session: SessionInfo = {
  actor_id: "test-user",
  groups: ["owners"],
  csrf_token: "c".repeat(32),
  portal_origin: "http://localhost:5173",
};

const activeSystem = {
  system_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  display_name: "Active System",
  owner_group: "owners",
  viewer_groups: ["viewers"],
  archived_at: null,
};

const archivedSystem = {
  system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  display_name: "Archived System",
  owner_group: "owners",
  viewer_groups: ["viewers"],
  archived_at: "2026-07-17T12:00:00Z",
};

const revisionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const uploadingRevision: PackageRevision = {
  package_revision_id: revisionId,
  system_id: activeSystem.system_id,
  status: "uploading",
  package_preparation_status: "in_progress",
  revision_version: 1,
  profile_id: "fisma_agency_security",
  data_origin: "synthetic",
  sensitivity: "internal_unclassified",
  impact_level: "moderate",
  certification_class: null,
};

const intakeReport: IntakeReport = {
  schema_version: "2.0.0",
  object_type: "intake_report",
  package_revision_id: revisionId,
  revision_version: 2,
  status: "awaiting_confirmation",
  intake_stage: "awaiting_human_review",
  files: [],
  human_attestation: {
    data_origin: "present",
    sensitivity: "present",
  },
  suggested_metadata: {
    profile_id: null,
    certification_class: null,
    impact_level: null,
  },
  suggestion_sources: [],
  gaps: [],
  conflicts: [],
  omitted_chunks: [],
  context_complete: true,
  map_steps: [],
  confirmation: {
    allowed: true,
    blockers: [],
  },
  generated_at: "2026-07-17T12:00:00Z",
};

const listSystemsMock = vi.fn();
const listRevisionsMock = vi.fn(
  async (_systemId?: unknown, _options?: unknown): Promise<PackageRevision[]> => [],
);
const archiveSystemMock = vi.fn();
const getRevisionMock = vi.fn(
  async (_revisionId?: unknown, _options?: unknown): Promise<PackageRevision> =>
    uploadingRevision,
);
const listRunsMock = vi.fn(async () => []);
const getIntakeReportMock = vi.fn();
const getRevisionDraftMock = vi.fn();
const getDraftExportReadinessMock = vi.fn();
const saveRevisionDraftMock = vi.fn();

function buildDraftDocument(
  overrides: Partial<PackageDraftDocument> = {},
): PackageDraftDocument {
  return {
    package: {
      profile_id: "fisma_agency_security",
      title: "Example",
      prepared_for: "Agency",
      reporting_period: null,
    },
    system: {
      display_name: "Example System",
      authorization_boundary: "Boundary",
      mission_summary: "Mission",
      impact_level: "moderate",
      authorization_path: "agency",
    },
    contacts: {
      system_owner: [{ name: "Owner", role: "Owner", email: "owner@example.com" }],
      isso: [],
      issm: [],
      control_owners: [],
      assessors: [],
      approvers: [],
    },
    control_set: {
      source: {},
      tailoring: [],
      organization_defined_parameters: {},
      inheritance: [],
    },
    security_controls: {},
    evidence: {},
    findings: {},
    poam_candidates: {},
    assessor_inputs: {},
    privacy: { artifacts_present: false, scope_notice: "" },
    fedramp_20x: null,
    fedramp_rev5_transition: null,
    fisma_agency_security: null,
    extensions: {
      intake_conflicts: [
        {
          conflict_id: "conflict-1",
          target_pointer: "/system/display_name",
          resolution: "pending",
          candidates: [{ value: "Cloud Platform Alpha" }, { value: "Platform Alpha Cloud" }],
        },
      ],
    },
    ...overrides,
  };
}

const awaitingRevision: PackageRevision = {
  ...uploadingRevision,
  status: "awaiting_confirmation",
  revision_version: 2,
  profile_id: "fisma_agency_security",
  data_origin: "synthetic",
  sensitivity: "internal_unclassified",
  impact_level: "moderate",
};

const conflictIntakeReport: IntakeReport = {
  ...intakeReport,
  conflicts: [
    {
      field: "/system/display_name",
      values: [{ value: "Cloud Platform Alpha" }, { value: "Platform Alpha Cloud" }],
    },
  ],
  human_attestation: {
    data_origin: "present",
    sensitivity: "present",
  },
  confirmation: {
    allowed: false,
    blockers: ["unresolved_intake_conflicts"],
  },
};

function mockAwaitingDraft(documentOverrides: Partial<PackageDraftDocument> = {}) {
  getRevisionMock.mockResolvedValue(awaitingRevision);
  listRevisionsMock.mockResolvedValue([awaitingRevision]);
  getIntakeReportMock.mockResolvedValue(conflictIntakeReport);
  getRevisionDraftMock.mockResolvedValue({
    draft: {
      schema_version: "2.0.0",
      object_type: "package_revision_draft",
      package_revision_id: revisionId,
      document_schema_version: "1.0.0",
      document: buildDraftDocument(documentOverrides),
      field_provenance: {},
      updated_by: "test-user",
      updated_at: "2026-07-17T12:00:00Z",
      revision_version: 2,
    },
    etag: '"v2"',
  });
}

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    listSystems: (options?: unknown) => listSystemsMock(options),
    listRevisions: (systemId?: unknown, options?: unknown) =>
      listRevisionsMock(systemId, options),
    archiveSystem: (sessionArg?: unknown, systemId?: unknown, options?: unknown) =>
      archiveSystemMock(sessionArg, systemId, options),
    getRevision: (revisionId?: unknown, options?: unknown) =>
      getRevisionMock(revisionId, options),
    listRuns: () => listRunsMock(),
    getIntakeReport: (revisionId?: unknown, options?: unknown) =>
      getIntakeReportMock(revisionId, options),
    getRevisionDraft: (revisionId?: unknown, options?: unknown) =>
      getRevisionDraftMock(revisionId, options),
    getDraftExportReadiness: (revisionId?: unknown, options?: unknown) =>
      getDraftExportReadinessMock(revisionId, options),
    saveRevisionDraft: (sessionArg?: unknown, revisionIdArg?: unknown, document?: unknown, etag?: unknown) =>
      saveRevisionDraftMock(sessionArg, revisionIdArg, document, etag),
  };
});

function renderWorkflow(initialPath = "/workflow") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/workflow" element={<WorkflowPage session={session} />} />
        <Route
          path="/workflow/systems/:systemId"
          element={<WorkflowPage session={session} />}
        />
        <Route
          path="/workflow/systems/:systemId/revisions/:revisionId"
          element={<WorkflowPage session={session} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listSystemsMock.mockReset();
  listRevisionsMock.mockReset();
  archiveSystemMock.mockReset();
  getRevisionMock.mockReset();
  listRunsMock.mockReset();
  getIntakeReportMock.mockReset();
  getRevisionDraftMock.mockReset();
  getDraftExportReadinessMock.mockReset();
  saveRevisionDraftMock.mockReset();
  listSystemsMock.mockResolvedValue([activeSystem]);
  listRevisionsMock.mockResolvedValue([uploadingRevision]);
  getRevisionMock.mockResolvedValue(uploadingRevision);
  listRunsMock.mockResolvedValue([]);
  getIntakeReportMock.mockResolvedValue(intakeReport);
  getRevisionDraftMock.mockRejectedValue(new ApiError(404, "Draft not found"));
  getDraftExportReadinessMock.mockResolvedValue({
    export_eligible: true,
    export_blockers: [],
    warnings: [],
    profile_id: "fisma_agency_security",
    structural_checks_passed: true,
  });
  saveRevisionDraftMock.mockImplementation(async (_session, _revisionId, document) => ({
    draft: {
      schema_version: "2.0.0",
      object_type: "package_revision_draft",
      package_revision_id: revisionId,
      document_schema_version: "1.0.0",
      document,
      field_provenance: {},
      updated_by: "test-user",
      updated_at: "2026-07-17T12:00:00Z",
      revision_version: 2,
    },
    etag: '"v3"',
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkflowPage system archive UI", () => {
  it("hides archived systems by default and labels them when shown", async () => {
    listSystemsMock.mockImplementation(async (options?: { includeArchived?: boolean }) =>
      options?.includeArchived ? [activeSystem, archivedSystem] : [activeSystem],
    );

    renderWorkflow();

    await screen.findByRole("button", { name: "Active System" });
    expect(screen.queryByRole("button", { name: /Archived System/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show archived" }));

    await screen.findByRole("button", { name: /Archived System/i });
    expect(screen.getByText("Archived")).toBeVisible();
    expect(listSystemsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ includeArchived: true }),
    );
  });

  it("confirms archive action and refreshes the default system list", async () => {
    listSystemsMock
      .mockResolvedValueOnce([activeSystem])
      .mockResolvedValueOnce([]);
    archiveSystemMock.mockResolvedValue({
      ...activeSystem,
      archived_at: "2026-07-17T12:00:00Z",
    });

    renderWorkflow(`/workflow/systems/${activeSystem.system_id}`);

    await screen.findByRole("button", { name: "Archive System" });
    fireEvent.click(screen.getByRole("button", { name: "Archive System" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Archive System" }));

    await waitFor(() => {
      expect(archiveSystemMock).toHaveBeenCalledWith(
        session,
        activeSystem.system_id,
        undefined,
      );
    });
    expect(
      screen.getByText('System "Active System" archived.'),
    ).toBeVisible();
  });

  it("reselects an active system when archived selection disappears from the default view", async () => {
    listSystemsMock.mockImplementation(async (options?: { includeArchived?: boolean }) =>
      options?.includeArchived
        ? [activeSystem, archivedSystem]
        : [activeSystem],
    );

    renderWorkflow(`/workflow/systems/${archivedSystem.system_id}`);

    fireEvent.click(await screen.findByRole("button", { name: "Show archived" }));
    await screen.findByRole("button", { name: /Archived System/i });
    fireEvent.click(screen.getByRole("button", { name: "Show archived" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Active System" })).toHaveClass(
        "border-link",
      );
    });
  });
});

describe("WorkflowPage metadata-first workflow", () => {
  it("renders revision metadata while uploading before the upload panel", async () => {
    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByText("Revision Workflow");
    expect(screen.getByText("Revision metadata")).toBeInTheDocument();
    expect(getIntakeReportMock).not.toHaveBeenCalled();
  });

  it("updates the revision list label from the current revision detail", async () => {
    listRevisionsMock.mockResolvedValue([uploadingRevision]);
    getRevisionMock.mockResolvedValue(awaitingRevision);

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    expect(
      await screen.findByRole("button", {
        name: /cccccccc.*Awaiting Confirmation/i,
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /cccccccc.*Uploading/i }),
    ).not.toBeInTheDocument();
  });

  it("enables confirm when create-time metadata and intake readiness are complete", async () => {
    getRevisionMock.mockResolvedValue(awaitingRevision);
    listRevisionsMock.mockResolvedValue([awaitingRevision]);
    getRevisionDraftMock.mockResolvedValue({
      draft: {
        schema_version: "2.0.0",
        object_type: "package_revision_draft",
        package_revision_id: revisionId,
        document_schema_version: "1.0.0",
        document: buildDraftDocument({ extensions: {} }),
        field_provenance: {},
        updated_by: "test-user",
        updated_at: "2026-07-17T12:00:00Z",
        revision_version: 2,
      },
      etag: '"v2"',
    });

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByText("Revision metadata");
    await waitFor(() => {
      expect(getIntakeReportMock).toHaveBeenCalled();
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Confirm Package" })).toBeEnabled();
    });
  });
});

describe("WorkflowPage conflict resolution", () => {
  it("applies a candidate, marks the draft dirty, and enables save", async () => {
    mockAwaitingDraft();

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByRole("button", {
      name: "Use candidate 1 for /system/display_name",
    });
    expect(
      screen.getByRole("button", {
        name: "Use candidate 1 for /system/display_name",
      }),
    ).not.toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Use candidate 1 for /system/display_name",
      }),
    );

    expect(screen.getByText(/Applied the selected value to the draft/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Draft" })).not.toBeDisabled();
  });

  it("rejects human-only metadata pointers for candidate selection", async () => {
    mockAwaitingDraft();
    getIntakeReportMock.mockResolvedValue({
      ...conflictIntakeReport,
      conflicts: [
        {
          field: "/metadata/data_origin",
          values: [{ value: "synthetic" }, { value: "customer_production" }],
        },
      ],
    });

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByRole("button", {
      name: "Use candidate 1 for /metadata/data_origin",
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Use candidate 1 for /metadata/data_origin",
      }),
    );

    expect(
      await screen.findByText(/attestation fields cannot be changed through draft edits/i),
    ).toBeInTheDocument();
  });

  it("rejects invalid prototype-pollution pointers", async () => {
    mockAwaitingDraft();
    getIntakeReportMock.mockResolvedValue({
      ...conflictIntakeReport,
      conflicts: [
        {
          field: "/__proto__/title",
          values: [{ value: "Bad" }, { value: "Worse" }],
        },
      ],
    });

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByRole("button", { name: "Use candidate 1 for /__proto__/title" });
    fireEvent.click(
      screen.getByRole("button", { name: "Use candidate 1 for /__proto__/title" }),
    );

    expect(
      await screen.findByText(/invalid or uses forbidden segments/i),
    ).toBeInTheDocument();
  });

  it("focuses editable fields on manual edit without resolving immediately", async () => {
    mockAwaitingDraft();

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Edit /system/display_name manually",
      }),
    );

    const displayNameInput = await screen.findByDisplayValue("Example System");
    expect(
      await screen.findByText(/conflict stays open until the value changes/i),
    ).toBeInTheDocument();
    expect(displayNameInput).toHaveValue("Example System");
    await waitFor(() => expect(displayNameInput).toHaveFocus());
    expect(
      screen.getByRole("button", { name: "Use candidate 1 for /system/display_name" }),
    ).toBeInTheDocument();
  });

  it("removes the draft conflict extension after a manual edit changes the value", async () => {
    mockAwaitingDraft();

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Edit /system/display_name manually",
      }),
    );
    const displayNameInput = await screen.findByDisplayValue("Example System");
    fireEvent.change(displayNameInput, { target: { value: "Operator edited name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));

    await waitFor(() => {
      expect(saveRevisionDraftMock).toHaveBeenCalled();
    });
    const savedDocument = saveRevisionDraftMock.mock.calls.at(-1)?.[2] as {
      extensions: { intake_conflicts: Array<{ target_pointer: string }> };
    };
    expect(savedDocument.extensions.intake_conflicts).toEqual([]);
  });

  it("shows a bounded message for unsupported pointers and leaves the conflict visible", async () => {
    mockAwaitingDraft();
    getIntakeReportMock.mockResolvedValue({
      ...conflictIntakeReport,
      conflicts: [
        {
          field: "/package/profile_id",
          values: [
            { value: "fisma_agency_security" },
            { value: "fedramp_20x_program" },
          ],
        },
      ],
    });

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    await screen.findByRole("button", { name: "Edit /package/profile_id manually" });
    fireEvent.click(
      screen.getByRole("button", { name: "Edit /package/profile_id manually" }),
    );

    expect(
      await screen.findByText(/not editable in the package editor/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Use candidate 1 for /package/profile_id" }),
    ).toBeInTheDocument();
  });

  it("disables conflict controls while draft save is in progress", async () => {
    mockAwaitingDraft();
    saveRevisionDraftMock.mockImplementation(
      () =>
        new Promise(() => {
          /* keep pending */
        }),
    );

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    const saveButton = await screen.findByRole("button", { name: "Save Draft" });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Use candidate 1 for /system/display_name",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save Draft" })).not.toBeDisabled(),
    );
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: "Use candidate 1 for /system/display_name",
        }),
      ).toBeDisabled();
    });
  });

  it("disables conflict controls when the draft has a stale server conflict", async () => {
    mockAwaitingDraft();
    saveRevisionDraftMock.mockRejectedValueOnce(new ApiError(412, "Draft version conflict"));

    renderWorkflow(
      `/workflow/systems/${activeSystem.system_id}/revisions/${revisionId}`,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Use candidate 1 for /system/display_name",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save Draft" })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: "Use candidate 1 for /system/display_name",
        }),
      ).toBeDisabled();
    });
    expect(screen.getByText(/changed on the server/i)).toBeInTheDocument();
  });
});
