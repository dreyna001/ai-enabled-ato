import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  approveExport,
  createExportDraft,
  createReviewRevision,
  listMatrixRows,
  listReviewComments,
  submitExportDraft,
} from "@/api/client";
import { ReviewExportWorkbench } from "@/components/ReviewExportWorkbench";
import type { Approval, ExportDraft, ReviewRevision, SessionInfo } from "@/types";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    approveExport: vi.fn(),
    createExportDraft: vi.fn(),
    createReviewRevision: vi.fn(),
    listMatrixRows: vi.fn(),
    listReviewComments: vi.fn(),
    submitExportDraft: vi.fn(),
  };
});

const runId = "33333333-3333-4333-8333-333333333333";
const review: ReviewRevision = {
  review_revision_id: "66666666-6666-4666-8666-666666666666",
  run_id: runId,
  version: 4,
  status: "submitted",
  dispositions: [],
};
const exportDraft: ExportDraft = {
  export_draft_id: "88888888-8888-4888-8888-888888888888",
  review_revision_id: review.review_revision_id,
  payload_manifest_sha256: "a".repeat(64),
  status: "draft",
};
const pendingApproval: Approval = {
  approval_id: "77777777-7777-4777-8777-777777777777",
  export_draft_id: exportDraft.export_draft_id,
  payload_manifest_sha256: exportDraft.payload_manifest_sha256,
  submitted_by: "operator@example.test",
  decided_by: null,
  decision: "pending",
  expires_at: "2026-07-30T21:49:06Z",
};

function session(singleUserModeEnabled: boolean): SessionInfo {
  return {
    actor_id: "operator@example.test",
    groups: ["owners"],
    csrf_token: "c".repeat(32),
    portal_origin: "http://localhost:5173",
    single_user_mode_enabled: singleUserModeEnabled,
  };
}

async function renderPendingSelfApproval(singleUserModeEnabled: boolean) {
  render(
    <ReviewExportWorkbench
      session={session(singleUserModeEnabled)}
      runId={runId}
      matrixRows={[]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open review revision" }));
  await screen.findByText("submitted");
  fireEvent.click(screen.getByRole("button", { name: "Create export draft" }));
  await screen.findByText("draft");
  fireEvent.click(screen.getByRole("button", { name: "Submit for approval" }));
  await screen.findByText("pending");
}

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(createReviewRevision).mockResolvedValue(review);
  vi.mocked(createExportDraft).mockResolvedValue(exportDraft);
  vi.mocked(submitExportDraft).mockResolvedValue(pendingApproval);
  vi.mocked(approveExport).mockResolvedValue({
    ...pendingApproval,
    decided_by: "operator@example.test",
    decision: "approved",
  });
  vi.mocked(listMatrixRows).mockResolvedValue({
    items: [],
    total: 0,
    next_cursor: null,
  });
  vi.mocked(listReviewComments).mockResolvedValue({
    items: [],
    next_cursor: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ReviewExportWorkbench self-approval", () => {
  it("allows the submitter to approve in explicit single-user demo mode", async () => {
    await renderPendingSelfApproval(true);

    expect(
      screen.getByText(
        "Single-user demo mode allows you to approve or reject your own export.",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Approve export" }));

    await waitFor(() => expect(approveExport).toHaveBeenCalledOnce());
    expect(await screen.findByRole("button", { name: "Download ZIP" })).toBeVisible();
  });

  it("continues to require a different approver when single-user mode is off", async () => {
    await renderPendingSelfApproval(false);

    expect(
      screen.getByText(
        "You submitted this export — a different approver must approve or reject.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve export" })).not.toBeInTheDocument();
  });
});
