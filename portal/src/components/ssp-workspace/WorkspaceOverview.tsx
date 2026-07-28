import {
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleAlert,
  FileText,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AgentContext, SspWorkspace } from "@/sspWorkspaceTypes";
import type { SspWorkspaceMetrics } from "@/utils/sspWorkspaceMetrics";

function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="mt-2 text-2xl font-semibold">{value}</p>
        <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

export function WorkspaceOverview({
  workspace,
  metrics,
  onGenerate,
  generationPending = false,
  onNavigate,
  onOpenAgent,
}: {
  workspace: SspWorkspace;
  metrics: SspWorkspaceMetrics;
  onGenerate?: () => void;
  generationPending?: boolean;
  onNavigate: (view: "evidence" | "ssp" | "controls" | "questions" | "review") => void;
  onOpenAgent: (context: AgentContext) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Evidence"
          value={metrics.evidence}
          detail={`${metrics.processedEvidence} processed · ${metrics.screenshots} screenshots`}
        />
        <MetricCard
          label="SSP completion"
          value={`${metrics.sspCompletion}%`}
          detail={`${metrics.satisfiedRequiredItems}/${metrics.requiredItems} required items satisfied`}
        />
        <MetricCard
          label="Controls drafted"
          value={metrics.controlsDrafted}
          detail={`${metrics.partialControls} need attention`}
        />
        <MetricCard
          label="Open questions"
          value={metrics.openQuestions}
          detail="Currently recorded unresolved questions"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="text-base">System summary</CardTitle>
                <CardDescription>
                  Current structured information used by the SSP.
                </CardDescription>
              </div>
              <Button size="sm" variant="ghost" onClick={() => onNavigate("ssp")}>
                Open SSP
                <ArrowRight aria-hidden="true" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">Purpose</dt>
                <dd className="mt-1">{workspace.purpose || "Not yet documented"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Hosting</dt>
                <dd className="mt-1">{workspace.hosting || "Not yet documented"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Impact level</dt>
                <dd className="mt-1">{workspace.impactLevel || "Not yet confirmed"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Authorization path</dt>
                <dd className="mt-1">
                  {workspace.authorizationPath || "Not yet confirmed"}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Next actions</CardTitle>
            <CardDescription>
              Determined from current workspace records.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              className="mb-2 w-full"
              disabled={
                !onGenerate ||
                metrics.processedEvidence === 0 ||
                generationPending
              }
              onClick={onGenerate}
            >
              {generationPending ? (
                <LoaderCircle
                  className="animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <Bot aria-hidden="true" />
              )}
              {generationPending
                ? "Generating documents…"
                : "Generate or update documents"}
            </Button>
            {generationPending ? (
              <p
                className="pb-2 text-xs text-muted-foreground"
                role="status"
              >
                Analyzing evidence and drafting supported SSP content.
              </p>
            ) : null}
            <button
              type="button"
              className="flex w-full items-center justify-between rounded-sm border p-3 text-left hover:bg-muted/40"
              onClick={() => onNavigate("questions")}
            >
              <span className="flex items-center gap-2">
                <CircleAlert className="size-4 text-amber-400" aria-hidden="true" />
                Resolve {metrics.openQuestions} open questions
              </span>
              <Badge variant="warning">Questions</Badge>
            </button>
            <button
              type="button"
              className="flex w-full items-center justify-between rounded-sm border p-3 text-left hover:bg-muted/40"
              onClick={() => onNavigate("controls")}
            >
              <span className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-link" aria-hidden="true" />
                Review {metrics.partialControls} partial controls
              </span>
              <Badge variant="outline">Controls</Badge>
            </button>
            <button
              type="button"
              className="flex w-full items-center justify-between rounded-sm border p-3 text-left hover:bg-muted/40"
              onClick={() => onNavigate("evidence")}
            >
              <span className="flex items-center gap-2">
                <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
                Check evidence processing
              </span>
              <Badge variant="muted">Evidence</Badge>
            </button>
            {metrics.reviewable ? (
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-sm border border-emerald-500/30 p-3 text-left hover:bg-muted/40"
                onClick={() => onNavigate("review")}
              >
                <span className="flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-emerald-400" aria-hidden="true" />
                  Review working content
                </span>
                <Badge variant="success">Reviewable</Badge>
              </button>
            ) : null}
            <Button
              className="mt-2 w-full"
              variant="outline"
              onClick={() =>
                onOpenAgent({
                  targetType: "workspace",
                  targetId: workspace.id,
                  label: workspace.name,
                })
              }
            >
              <Bot aria-hidden="true" />
              Ask agent about this workspace
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
