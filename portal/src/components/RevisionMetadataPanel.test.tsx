import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RevisionMetadataPanel } from "@/components/RevisionMetadataPanel";
import type { IntakeReport, PackageRevision, SessionInfo } from "@/types";

const session: SessionInfo = {
  actor_id: "test-user",
  groups: ["owners"],
  csrf_token: "c".repeat(32),
  portal_origin: "http://localhost:5173",
};

const revisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const systemId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const baseRevision: PackageRevision = {
  package_revision_id: revisionId,
  system_id: systemId,
  status: "awaiting_confirmation",
  package_preparation_status: "in_progress",
  revision_version: 3,
  profile_id: null,
  data_origin: null,
  sensitivity: null,
  impact_level: null,
  certification_class: null,
};

const intakeReport: IntakeReport = {
  schema_version: "2.0.0",
  object_type: "intake_report",
  package_revision_id: revisionId,
  revision_version: 3,
  status: "awaiting_confirmation",
  intake_stage: "awaiting_human_review",
  files: [],
  human_attestation: {
    data_origin: "missing",
    sensitivity: "missing",
  },
  suggested_metadata: {
    profile_id: "fisma_agency_security",
    certification_class: null,
    impact_level: "high",
  },
  suggestion_sources: [
    {
      field: "profile_id",
      proposed_value: "fisma_agency_security",
      source_artifact_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      source_sha256: "d".repeat(64),
      source_locator: {},
    },
  ],
  gaps: [],
  conflicts: [],
  omitted_chunks: [],
  context_complete: true,
  map_steps: [],
  confirmation: {
    allowed: false,
    blockers: ["metadata_incomplete"],
  },
  generated_at: "2026-07-17T12:00:00Z",
};

const getIntakeReportMock = vi.fn();
const getRevisionMock = vi.fn();
const patchRevisionMetadataMock = vi.fn();

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    getIntakeReport: (...args: unknown[]) => getIntakeReportMock(...args),
    getRevision: (...args: unknown[]) => getRevisionMock(...args),
    patchRevisionMetadata: (...args: unknown[]) => patchRevisionMetadataMock(...args),
  };
});

beforeEach(() => {
  getIntakeReportMock.mockReset();
  getRevisionMock.mockReset();
  patchRevisionMetadataMock.mockReset();
  getIntakeReportMock.mockResolvedValue(intakeReport);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RevisionMetadataPanel", () => {
  it("prefills profile and impact suggestions without human-only fields", async () => {
    getIntakeReportMock.mockResolvedValue({
      ...intakeReport,
      suggestion_sources: [],
    });

    render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        onRevisionUpdated={() => undefined}
      />,
    );

    await screen.findByLabelText("Profile");
    expect(screen.getByLabelText("Profile")).toHaveValue("fisma_agency_security");
    expect(screen.getByLabelText("Impact level")).toHaveValue("high");
    expect(screen.getByLabelText("Data origin")).toHaveValue("");
    expect(screen.getByLabelText("Sensitivity")).toHaveValue("");
    expect(screen.getAllByText("Suggested")).toHaveLength(2);
    expect(screen.getAllByText("Suggested by intake. Review and save to apply.")).toHaveLength(2);
    expect(screen.getAllByText("Required human attestation")).toHaveLength(2);
  });

  it("does not overwrite existing human revision values when intake report refreshes", async () => {
    const revision = {
      ...baseRevision,
      profile_id: "fedramp_rev5_transition" as const,
      impact_level: "moderate",
    };
    getIntakeReportMock.mockResolvedValue({
      ...intakeReport,
      suggested_metadata: {
        profile_id: "fisma_agency_security",
        certification_class: null,
        impact_level: "high",
      },
    });

    render(
      <RevisionMetadataPanel
        session={session}
        revision={revision}
        onRevisionUpdated={() => undefined}
      />,
    );

    await screen.findByLabelText("Profile");
    expect(screen.getByLabelText("Profile")).toHaveValue("fedramp_rev5_transition");
    expect(screen.getByLabelText("Impact level")).toHaveValue("moderate");
  });

  it("sends minimal PATCH payload with concurrency headers", async () => {
    patchRevisionMetadataMock.mockResolvedValue({
      revision: {
        ...baseRevision,
        profile_id: "fisma_agency_security",
        impact_level: "high",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
        revision_version: 4,
      },
      etag: '"v4"',
    });

    render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        onRevisionUpdated={() => undefined}
      />,
    );

    await screen.findByLabelText("Data origin");
    fireEvent.change(screen.getByLabelText("Data origin"), {
      target: { value: "synthetic" },
    });
    fireEvent.change(screen.getByLabelText("Sensitivity"), {
      target: { value: "internal_unclassified" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    await waitFor(() => {
      expect(patchRevisionMetadataMock).toHaveBeenCalledWith(
        session,
        revisionId,
        '"v3"',
        expect.objectContaining({
          profile_id: "fisma_agency_security",
          impact_level: "high",
          data_origin: "synthetic",
          sensitivity: "internal_unclassified",
        }),
      );
    });
  });

  it("preserves unsaved edits during a background report refresh", async () => {
    const { rerender } = render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        refreshKey="first"
        onRevisionUpdated={() => undefined}
      />,
    );

    await screen.findByLabelText("Data origin");
    fireEvent.change(screen.getByLabelText("Data origin"), {
      target: { value: "customer_production" },
    });

    rerender(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        refreshKey="second"
        onRevisionUpdated={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(getIntakeReportMock).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByLabelText("Data origin")).toHaveValue("customer_production");
  });

  it("reloads the current revision and retries with the latest ETag after conflict", async () => {
    const { ApiError } = await import("@/api/client");
    const initialRevision: PackageRevision = {
      ...baseRevision,
      revision_version: 1,
      profile_id: "fisma_agency_security",
      impact_level: "moderate",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    };
    const latestRevision: PackageRevision = {
      ...initialRevision,
      revision_version: 2,
    };
    const savedRevision: PackageRevision = {
      ...latestRevision,
      revision_version: 3,
      impact_level: "high",
    };
    let resolveReload: (revision: PackageRevision) => void = () => undefined;
    const onRevisionUpdated = vi.fn();
    patchRevisionMetadataMock
      .mockRejectedValueOnce(
        new ApiError(412, "Stale revision metadata", "http", "etag_mismatch"),
      )
      .mockResolvedValueOnce({
        revision: savedRevision,
        etag: '"v3"',
      });
    getRevisionMock.mockReturnValue(
      new Promise<PackageRevision>((resolve) => {
        resolveReload = resolve;
      }),
    );

    render(
      <RevisionMetadataPanel
        session={session}
        revision={initialRevision}
        onRevisionUpdated={onRevisionUpdated}
      />,
    );

    await screen.findByLabelText("Impact level");
    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    const reloadButton = await screen.findByRole("button", {
      name: "Reload metadata",
    });
    expect(patchRevisionMetadataMock).toHaveBeenNthCalledWith(
      1,
      session,
      revisionId,
      '"v1"',
      { impact_level: "high" },
    );

    fireEvent.click(reloadButton);
    const reloadingButton = await screen.findByRole("button", {
      name: "Reloading…",
    });
    expect(reloadingButton).toBeDisabled();
    fireEvent.click(reloadingButton);
    expect(getRevisionMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveReload(latestRevision);
    });

    await waitFor(() => {
      expect(getRevisionMock).toHaveBeenCalledWith(revisionId);
      expect(screen.getByLabelText("Impact level")).toHaveValue("moderate");
    });
    expect(onRevisionUpdated).toHaveBeenCalledWith(latestRevision, '"v2"');
    expect(
      screen.queryByRole("button", { name: "Reload metadata" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    await waitFor(() => {
      expect(patchRevisionMetadataMock).toHaveBeenNthCalledWith(
        2,
        session,
        revisionId,
        '"v2"',
        { impact_level: "high" },
      );
    });
  });

  it("keeps unsaved edits and shows a bounded error when reload fails", async () => {
    const { ApiError } = await import("@/api/client");
    const initialRevision: PackageRevision = {
      ...baseRevision,
      revision_version: 1,
      profile_id: "fisma_agency_security",
      impact_level: "moderate",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    };
    patchRevisionMetadataMock.mockRejectedValueOnce(
      new ApiError(409, "Revision state changed", "http", "invalid_state"),
    );
    getRevisionMock.mockRejectedValueOnce(
      new ApiError(503, "Revision service unavailable"),
    );

    render(
      <RevisionMetadataPanel
        session={session}
        revision={initialRevision}
        onRevisionUpdated={() => undefined}
      />,
    );

    await screen.findByLabelText("Impact level");
    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Reload metadata" }),
    );

    expect(
      await screen.findByText(/Could not reload current revision metadata/i),
    ).toBeVisible();
    expect(screen.getByLabelText("Impact level")).toHaveValue("high");
    expect(
      screen.getByRole("button", { name: "Reload metadata" }),
    ).toBeEnabled();
  });
});
