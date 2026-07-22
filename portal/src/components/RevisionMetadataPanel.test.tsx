import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RevisionMetadataPanel } from "@/components/RevisionMetadataPanel";
import type { PackageRevision, SessionInfo } from "@/types";

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
  profile_id: "fisma_agency_security",
  data_origin: "synthetic",
  sensitivity: "internal_unclassified",
  impact_level: "high",
  certification_class: null,
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
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RevisionMetadataPanel", () => {
  it("loads saved revision metadata without intake suggestions", () => {
    render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        onRevisionUpdated={() => undefined}
      />,
    );

    expect(screen.getByLabelText("Profile")).toHaveValue("fisma_agency_security");
    expect(screen.getByLabelText("Impact level")).toHaveValue("high");
    expect(screen.getByLabelText("Data origin")).toHaveValue("synthetic");
    expect(screen.getByLabelText("Sensitivity")).toHaveValue("internal_unclassified");
    expect(screen.queryByText("Suggested")).not.toBeInTheDocument();
    expect(screen.queryByText(/Suggested by intake/i)).not.toBeInTheDocument();
    expect(getIntakeReportMock).not.toHaveBeenCalled();
  });

  it("allows metadata corrections while uploading", () => {
    render(
      <RevisionMetadataPanel
        session={session}
        revision={{ ...baseRevision, status: "uploading", revision_version: 1 }}
        onRevisionUpdated={() => undefined}
      />,
    );

    expect(screen.getByLabelText("Profile")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save metadata" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "moderate" },
    });

    expect(screen.getByText("Unsaved metadata changes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save metadata" })).toBeEnabled();
  });

  it("sends a minimal PATCH payload with the current ETag", async () => {
    const updatedRevision: PackageRevision = {
      ...baseRevision,
      impact_level: "moderate",
      revision_version: 4,
    };
    const onRevisionUpdated = vi.fn();
    patchRevisionMetadataMock.mockResolvedValue({
      revision: updatedRevision,
      etag: '"v4"',
    });

    render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        onRevisionUpdated={onRevisionUpdated}
      />,
    );

    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "moderate" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    await waitFor(() => {
      expect(patchRevisionMetadataMock).toHaveBeenCalledWith(
        session,
        revisionId,
        '"v3"',
        { impact_level: "moderate" },
      );
    });
    expect(onRevisionUpdated).toHaveBeenCalledWith(updatedRevision, '"v4"');
    expect(await screen.findByText("Metadata saved.")).toBeInTheDocument();
  });

  it("renders a read-only summary when metadata is sealed", () => {
    render(
      <RevisionMetadataPanel
        session={session}
        revision={{ ...baseRevision, status: "ready" }}
        onRevisionUpdated={() => undefined}
      />,
    );

    expect(screen.getByText(/Sealed revision metadata is immutable/i)).toBeInTheDocument();
    expect(screen.getByText("Agency FISMA security")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Synthetic (demo / lab)")).toBeInTheDocument();
    expect(screen.getByText("Internal unclassified")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save metadata" })).not.toBeInTheDocument();
  });

  it("preserves unsaved corrections when the refresh key changes", () => {
    const { rerender } = render(
      <RevisionMetadataPanel
        session={session}
        revision={baseRevision}
        refreshKey="first"
        onRevisionUpdated={() => undefined}
      />,
    );

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

    expect(screen.getByLabelText("Data origin")).toHaveValue("customer_production");
  });

  it("reloads current metadata after an ETag conflict and retries with the latest ETag", async () => {
    const { ApiError } = await import("@/api/client");
    const initialRevision: PackageRevision = {
      ...baseRevision,
      revision_version: 1,
      impact_level: "moderate",
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

    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    expect(await screen.findByRole("button", { name: "Reload metadata" })).toBeEnabled();
    expect(patchRevisionMetadataMock).toHaveBeenNthCalledWith(
      1,
      session,
      revisionId,
      '"v1"',
      { impact_level: "high" },
    );

    fireEvent.click(screen.getByRole("button", { name: "Reload metadata" }));
    expect(await screen.findByRole("button", { name: "Reloading…" })).toBeDisabled();
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

  it("keeps unsaved corrections and shows a bounded error when reload fails", async () => {
    const { ApiError } = await import("@/api/client");
    const initialRevision: PackageRevision = {
      ...baseRevision,
      revision_version: 1,
      impact_level: "moderate",
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

    fireEvent.change(screen.getByLabelText("Impact level"), {
      target: { value: "high" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reload metadata" }));

    expect(
      await screen.findByText(/Could not reload current revision metadata/i),
    ).toBeVisible();
    expect(screen.getByLabelText("Impact level")).toHaveValue("high");
    expect(screen.getByRole("button", { name: "Reload metadata" })).toBeEnabled();
  });
});
