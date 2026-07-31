import { Download, Eye, FileUp, ShieldAlert } from "lucide-react";
import { useMemo, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type {
  AgencyDocxIssue,
  AgencyDocxMappingException,
  AgencyDocxRender,
  AgencyDocxRenderStatus,
  SspWorkspace,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";

function shortenHash(value: string): string {
  if (value.length <= 20) return value;
  return `${value.slice(0, 8)}…${value.slice(-8)}`;
}

function renderStatusLabel(status: AgencyDocxRenderStatus): string {
  if (status === "awaiting_approval") return "Awaiting approval";
  if (status === "review_failed") return "Review failed";
  if (status === "approved") return "Approved";
  return "Rejected";
}

function statusVariant(
  status: AgencyDocxRenderStatus,
): "success" | "warning" | "destructive" | "secondary" {
  if (status === "approved") return "success";
  if (status === "review_failed" || status === "rejected") return "destructive";
  if (status === "awaiting_approval") return "warning";
  return "secondary";
}

type DisplayIssue = {
  severity: "blocker" | "warning";
  code: string;
  message: string;
};

function issueKey(
  issue: AgencyDocxMappingException | AgencyDocxIssue,
  locator?: string | null,
): string {
  return `${issue.code}|${issue.message}|${locator ?? ""}`;
}

export function collectAgencyRenderIssues(
  render: AgencyDocxRender,
): DisplayIssue[] {
  const seen = new Set<string>();
  const items: DisplayIssue[] = [];
  for (const exception of render.mappingExceptions) {
    if (exception.severity !== "blocker" && exception.severity !== "warning") {
      continue;
    }
    const key = issueKey(exception);
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      severity: exception.severity,
      code: exception.code,
      message: exception.message,
    });
  }
  for (const issue of render.reviewIssues) {
    if (issue.severity !== "blocker" && issue.severity !== "warning") {
      continue;
    }
    const key = issueKey(issue, issue.locator);
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      severity: issue.severity,
      code: issue.code,
      message: issue.message,
    });
  }
  return items;
}

function renderHasBlockers(render: AgencyDocxRender): boolean {
  return collectAgencyRenderIssues(render).some(
    (issue) => issue.severity === "blocker",
  );
}

function AgencyRenderCard({
  render,
  actionsBusy,
  onPreviewAgencyRender,
  onApproveAgencyRender,
  onRejectAgencyRender,
  onDownloadAgencyRender,
}: {
  render: AgencyDocxRender;
  actionsBusy?: boolean;
  onPreviewAgencyRender?: SspWorkspaceActions["onPreviewAgencyRender"];
  onApproveAgencyRender?: SspWorkspaceActions["onApproveAgencyRender"];
  onRejectAgencyRender?: SspWorkspaceActions["onRejectAgencyRender"];
  onDownloadAgencyRender?: SspWorkspaceActions["onDownloadAgencyRender"];
}) {
  const issues = collectAgencyRenderIssues(render);
  const blockers = renderHasBlockers(render);
  const canReject =
    render.status === "awaiting_approval" || render.status === "review_failed";

  return (
    <article
      className="rounded-sm border bg-muted/10 p-4"
      aria-labelledby={`agency-render-${render.id}-title`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3
            className="font-medium"
            id={`agency-render-${render.id}-title`}
          >
            {render.templateFilename || "Agency template"}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Created {render.createdAt}
          </p>
        </div>
        <Badge variant={statusVariant(render.status)}>
          {renderStatusLabel(render.status)}
        </Badge>
      </div>

      <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Source revision hash</dt>
          <dd className="font-mono">{shortenHash(render.sourceRevisionSha256)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Template hash</dt>
          <dd className="font-mono">{shortenHash(render.templateSha256)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Output hash</dt>
          <dd className="font-mono">{shortenHash(render.outputSha256)}</dd>
        </div>
      </dl>

      {render.mappingSummary ? (
        <p className="mt-3 text-sm">{render.mappingSummary}</p>
      ) : null}
      {render.reviewSummary ? (
        <p className="mt-2 text-sm text-muted-foreground">
          {render.reviewSummary}
        </p>
      ) : null}

      {issues.length > 0 ? (
        <ul className="mt-3 space-y-2" aria-label="Mapping and review issues">
          {issues.map((issue) => (
            <li
              key={`${issue.code}-${issue.message}-${issue.severity}`}
              className="rounded-sm border px-3 py-2 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge
                  variant={
                    issue.severity === "blocker" ? "destructive" : "warning"
                  }
                >
                  {issue.severity === "blocker" ? "Blocker" : "Warning"}
                </Badge>
                <span className="font-mono text-xs">{issue.code}</span>
              </div>
              <p className="mt-1">{issue.message}</p>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={
            actionsBusy || !onPreviewAgencyRender || !render.canPreview
          }
          onClick={() => onPreviewAgencyRender?.(render.id)}
        >
          <Eye aria-hidden="true" />
          Preview draft
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={
            actionsBusy ||
            !onApproveAgencyRender ||
            !render.canApprove ||
            blockers
          }
          onClick={() => onApproveAgencyRender?.(render.id)}
        >
          Approve mapping and render
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={actionsBusy || !onRejectAgencyRender || !canReject}
          onClick={() => onRejectAgencyRender?.(render.id)}
        >
          Reject
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={
            actionsBusy || !onDownloadAgencyRender || !render.canDownload
          }
          onClick={() => onDownloadAgencyRender?.(render.id)}
        >
          <Download aria-hidden="true" />
          Download approved draft
        </Button>
      </div>
    </article>
  );
}

export function AgencyTemplatePanel({
  workspace,
  approved,
  actionsBusy = false,
  onUploadAgencyTemplate,
  onPreviewAgencyRender,
  onApproveAgencyRender,
  onRejectAgencyRender,
  onDownloadAgencyRender,
}: {
  workspace: Pick<SspWorkspace, "agencyDocxRenders">;
  approved: boolean;
  actionsBusy?: boolean;
  onUploadAgencyTemplate?: SspWorkspaceActions["onUploadAgencyTemplate"];
  onPreviewAgencyRender?: SspWorkspaceActions["onPreviewAgencyRender"];
  onApproveAgencyRender?: SspWorkspaceActions["onApproveAgencyRender"];
  onRejectAgencyRender?: SspWorkspaceActions["onRejectAgencyRender"];
  onDownloadAgencyRender?: SspWorkspaceActions["onDownloadAgencyRender"];
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const renders = useMemo(
    () =>
      [...workspace.agencyDocxRenders].sort((left, right) =>
        right.createdAt.localeCompare(left.createdAt),
      ),
    [workspace.agencyDocxRenders],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Agency-shaped draft</CardTitle>
        <CardDescription>
          Upload an agency DOCX template after ISSO approval to generate a
          draft for internal review. This output is a draft helper only and
          does not claim agency format parity or submission readiness.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!approved ? (
          <div className="flex items-start gap-3 rounded-sm border border-amber-500/40 bg-amber-500/5 p-4">
            <ShieldAlert
              className="mt-0.5 size-5 text-amber-400"
              aria-hidden="true"
            />
            <p className="text-sm">
              Approve the current SSP revision before uploading an agency
              template. Generation binds to the approved revision hash.
            </p>
          </div>
        ) : null}

        <div>
          <input
            ref={inputRef}
            className="sr-only"
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            aria-label="Agency template DOCX file"
            disabled={!approved || actionsBusy || !onUploadAgencyTemplate}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUploadAgencyTemplate?.(file);
              event.target.value = "";
            }}
          />
          <Button
            type="button"
            disabled={
              !approved || actionsBusy || !onUploadAgencyTemplate
            }
            onClick={() => inputRef.current?.click()}
          >
            <FileUp aria-hidden="true" />
            Generate agency-shaped draft
          </Button>
        </div>

        {renders.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No agency-shaped drafts yet. Upload a DOCX template to create one.
          </p>
        ) : (
          <div className="space-y-4">
            {renders.map((render) => (
              <AgencyRenderCard
                key={render.id}
                render={render}
                actionsBusy={actionsBusy}
                onPreviewAgencyRender={onPreviewAgencyRender}
                onApproveAgencyRender={onApproveAgencyRender}
                onRejectAgencyRender={onRejectAgencyRender}
                onDownloadAgencyRender={onDownloadAgencyRender}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
