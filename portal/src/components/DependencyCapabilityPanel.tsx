import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PortalReadinessState } from "@/types";

type DependencyCapabilityPanelProps = {
  readiness: PortalReadinessState;
  revisionReady?: boolean;
};

const FEATURES = [
  { id: "preflight", label: "Preflight Checks", requiresReady: true, allowsDegraded: false },
  { id: "analysis", label: "Analysis Runs", requiresReady: true, allowsDegraded: false },
  { id: "search", label: "Package Search", requiresReady: true, allowsDegraded: true },
  { id: "chat", label: "Package Assistant", requiresReady: true, allowsDegraded: true },
  { id: "export", label: "Export Workflow", requiresReady: true, allowsDegraded: false },
] as const;

export function DependencyCapabilityPanel({
  readiness,
  revisionReady = false,
}: DependencyCapabilityPanelProps) {
  const apiHealthy = readiness.loaded && !readiness.error && !readiness.degraded;
  const apiUsable = readiness.loaded && !readiness.error;

  return (
    <Card className="bg-muted/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Dependencies and Capabilities</CardTitle>
        <CardDescription>
          Read-only status from /health/ready and current revision state.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {!readiness.loaded ? (
          <p className="text-muted-foreground">Checking API readiness…</p>
        ) : null}
        {readiness.error ? (
          <p className="text-destructive">{readiness.error}</p>
        ) : null}
        {readiness.checks.length > 0 ? (
          <ul className="space-y-1">
            {readiness.checks.map((check) => (
              <li key={check.name} className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs">{check.name}</span>
                <Badge variant={check.status === "ok" ? "default" : "destructive"}>
                  {check.status}
                </Badge>
              </li>
            ))}
          </ul>
        ) : null}
        <ul className="space-y-1 border-t pt-3">
          {FEATURES.map((feature) => {
            const enabled =
              (apiHealthy || (apiUsable && feature.allowsDegraded)) &&
              (!feature.requiresReady || revisionReady);
            return (
              <li key={feature.id} className="flex items-center justify-between gap-2">
                <span>{feature.label}</span>
                <Badge variant={enabled ? "default" : "muted"}>
                  {enabled ? "available" : "disabled"}
                </Badge>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

export function isAssistantEnabled(
  readiness: PortalReadinessState,
  revisionReady: boolean,
): boolean {
  return readiness.loaded && !readiness.error && revisionReady;
}

export function assistantReadinessWarning(
  readiness: PortalReadinessState,
): string | null {
  if (!readiness.loaded || readiness.error || !readiness.degraded) {
    return null;
  }
  return "Production readiness is degraded. This package workflow and search remain available; citation-backed answers require a succeeded analysis run.";
}
