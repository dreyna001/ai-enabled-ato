import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AgencyTemplatePanel,
  collectAgencyRenderIssues,
} from "@/components/ssp-workspace/AgencyTemplatePanel";
import type { AgencyDocxRender, SspWorkspace } from "@/sspWorkspaceTypes";

function baseRender(
  overrides: Partial<AgencyDocxRender> = {},
): AgencyDocxRender {
  return {
    id: "render-1",
    profileVersionId: "profile-v1",
    sourceRevisionId: "rev-4",
    sourceRevisionSha256: "a".repeat(64),
    templateSha256: "b".repeat(64),
    templateFilename: "agency-ssp-template.docx",
    outputSha256: "c".repeat(64),
    status: "awaiting_approval",
    createdBy: "isso@example.gov",
    createdAt: "2026-07-28T12:00:00Z",
    resolvedBy: null,
    resolvedAt: null,
    mappingSummary: "Mapped 12 of 12 required placeholders.",
    mappingExceptions: [],
    reviewSummary: "Automated review found no blockers.",
    reviewIssues: [],
    canApprove: true,
    canPreview: true,
    canDownload: false,
    ...overrides,
  };
}

function workspace(renders: AgencyDocxRender[]): Pick<SspWorkspace, "agencyDocxRenders"> {
  return { agencyDocxRenders: renders };
}

afterEach(cleanup);

describe("AgencyTemplatePanel", () => {
  it("disables upload before ISSO approval", () => {
    render(
      <AgencyTemplatePanel
        workspace={workspace([])}
        approved={false}
        onUploadAgencyTemplate={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/Approve the current SSP revision before uploading/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate agency-shaped draft" }),
    ).toBeDisabled();
  });

  it("forwards a selected DOCX file to upload", () => {
    const onUploadAgencyTemplate = vi.fn();
    render(
      <AgencyTemplatePanel
        workspace={workspace([])}
        approved
        onUploadAgencyTemplate={onUploadAgencyTemplate}
      />,
    );

    const file = new File(["docx"], "agency-template.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(screen.getByLabelText("Agency template DOCX file"), {
      target: { files: [file] },
    });

    expect(onUploadAgencyTemplate).toHaveBeenCalledWith(file);
  });

  it("disables approval when blockers are present", () => {
    render(
      <AgencyTemplatePanel
        workspace={workspace([
          baseRender({
            mappingExceptions: [
              {
                severity: "blocker",
                code: "MISSING_PLACEHOLDER",
                message: "Required placeholder is missing.",
              },
            ],
          }),
        ])}
        approved
        onApproveAgencyRender={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Approve mapping and render" }),
    ).toBeDisabled();
    expect(screen.getByText("MISSING_PLACEHOLDER")).toBeInTheDocument();
    expect(screen.queryByText(/locator/i)).not.toBeInTheDocument();
  });

  it("forwards preview and reject actions for awaiting renders", () => {
    const onPreviewAgencyRender = vi.fn();
    const onRejectAgencyRender = vi.fn();
    render(
      <AgencyTemplatePanel
        workspace={workspace([baseRender()])}
        approved
        onPreviewAgencyRender={onPreviewAgencyRender}
        onRejectAgencyRender={onRejectAgencyRender}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onPreviewAgencyRender).toHaveBeenCalledWith("render-1");
    expect(onRejectAgencyRender).toHaveBeenCalledWith("render-1");
  });

  it("enables download for approved renders", () => {
    const onDownloadAgencyRender = vi.fn();
    render(
      <AgencyTemplatePanel
        workspace={workspace([
          baseRender({
            status: "approved",
            canApprove: false,
            canPreview: false,
            canDownload: true,
          }),
        ])}
        approved
        onDownloadAgencyRender={onDownloadAgencyRender}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Download approved draft" }),
    );
    expect(onDownloadAgencyRender).toHaveBeenCalledWith("render-1");
  });
});

describe("collectAgencyRenderIssues", () => {
  it("dedupes mapping and review issues by code, message, and locator", () => {
    const issues = collectAgencyRenderIssues(
      baseRender({
        mappingExceptions: [
          {
            severity: "warning",
            code: "UNMAPPED",
            message: "Optional field left blank.",
          },
        ],
        reviewIssues: [
          {
            severity: "warning",
            code: "UNMAPPED",
            message: "Optional field left blank.",
            locator: "section-1",
          },
          {
            severity: "warning",
            code: "UNMAPPED",
            message: "Optional field left blank.",
            locator: "section-1",
          },
        ],
      }),
    );

    expect(issues).toHaveLength(2);
  });
});
