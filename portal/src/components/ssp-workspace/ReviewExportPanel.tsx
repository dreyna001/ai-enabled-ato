import { CheckCircle2, Download, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { AgencyTemplatePanel } from "@/components/ssp-workspace/AgencyTemplatePanel";
import type {
  SspWorkspace,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";
import type { SspWorkspaceMetrics } from "@/utils/sspWorkspaceMetrics";

function ReviewCheck({
  label,
  satisfied,
}: {
  label: string;
  satisfied: boolean;
}) {
  return (
    <li className="flex items-center justify-between gap-3 border-b py-3 last:border-b-0">
      <span className="text-sm">{label}</span>
      <Badge variant={satisfied ? "success" : "warning"}>
        {satisfied ? "Satisfied" : "Attention"}
      </Badge>
    </li>
  );
}

export function ReviewExportPanel({
  workspace,
  metrics,
  actionsBusy = false,
  onApprove,
  onExport,
  onUploadAgencyTemplate,
  onPreviewAgencyRender,
  onApproveAgencyRender,
  onRejectAgencyRender,
  onDownloadAgencyRender,
}: {
  workspace: SspWorkspace;
  metrics: SspWorkspaceMetrics;
  actionsBusy?: boolean;
  onApprove?: SspWorkspaceActions["onApprove"];
  onExport?: SspWorkspaceActions["onExport"];
  onUploadAgencyTemplate?: SspWorkspaceActions["onUploadAgencyTemplate"];
  onPreviewAgencyRender?: SspWorkspaceActions["onPreviewAgencyRender"];
  onApproveAgencyRender?: SspWorkspaceActions["onApproveAgencyRender"];
  onRejectAgencyRender?: SspWorkspaceActions["onRejectAgencyRender"];
  onDownloadAgencyRender?: SspWorkspaceActions["onDownloadAgencyRender"];
}) {
  return (
    <div className="space-y-4">
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">ISSO review</CardTitle>
          <CardDescription>
            Approve the current saved revision once the working content is ready.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul>
            <ReviewCheck
              label="Evidence processing is terminal"
              satisfied={workspace.processingJobsTerminal}
            />
            <ReviewCheck
              label="Required SSP items are populated or tracked"
              satisfied={metrics.requiredItemsResolved}
            />
            <ReviewCheck
              label="Every selected control is drafted or tracked"
              satisfied={metrics.controlsResolved}
            />
            <ReviewCheck
              label="Working revision is saved and internally consistent"
              satisfied={workspace.revisionSaved && workspace.internallyConsistent}
            />
          </ul>
          <div className="mt-5 rounded-sm border bg-muted/20 p-4">
            <div className="flex items-start gap-3">
              {metrics.approved ? (
                <CheckCircle2
                  className="mt-0.5 size-5 text-emerald-400"
                  aria-hidden="true"
                />
              ) : (
                <ShieldAlert
                  className="mt-0.5 size-5 text-amber-400"
                  aria-hidden="true"
                />
              )}
              <div>
                <p className="font-medium">
                  {metrics.approved
                    ? "Current revision approved"
                    : metrics.reviewable
                      ? "Current revision is reviewable"
                      : "Current revision needs attention"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Approval binds the ISSO decision to revision{" "}
                  <span className="font-mono">{workspace.revisionId}</span> and
                  its exact content hash.
                </p>
              </div>
            </div>
            <Button
              className="mt-4"
              type="button"
              disabled={!onApprove || !metrics.reviewable || metrics.approved}
              onClick={onApprove}
            >
              Approve working content
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Export documents</CardTitle>
          <CardDescription>
            Export the approved SSP, control statements, and unresolved items.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button
            className="w-full justify-start"
            variant="outline"
            disabled={!onExport || !metrics.approved}
            onClick={() => onExport?.("docx")}
          >
            <Download aria-hidden="true" />
            Export DOCX
          </Button>
          <Button
            className="w-full justify-start"
            variant="outline"
            disabled={!onExport || !metrics.approved}
            onClick={() => onExport?.("json")}
          >
            <Download aria-hidden="true" />
            Export structured JSON
          </Button>
          <div className="space-y-1">
            <Button
              className="w-full justify-start"
              variant="outline"
              disabled={!onExport || !metrics.approved}
              onClick={() => onExport?.("oscal-json")}
            >
              <Download aria-hidden="true" />
              Export draft OSCAL JSON
            </Button>
            <p className="text-xs text-muted-foreground">
              Schema-checked draft; not qualified/customer-ready.
            </p>
          </div>
          {!metrics.approved ? (
            <p className="text-xs text-muted-foreground">
              Approve the current content before exporting its immutable snapshot.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
    <AgencyTemplatePanel
      workspace={workspace}
      approved={metrics.approved}
      actionsBusy={actionsBusy}
      onUploadAgencyTemplate={onUploadAgencyTemplate}
      onPreviewAgencyRender={onPreviewAgencyRender}
      onApproveAgencyRender={onApproveAgencyRender}
      onRejectAgencyRender={onRejectAgencyRender}
      onDownloadAgencyRender={onDownloadAgencyRender}
    />
    </div>
  );
}
