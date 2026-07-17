import { useEffect, useState } from "react";
import { PreflightCheckList, buildPreflightCheckMessageMap } from "@/components/PreflightCheckList";
import { getPreflight, isCancelledRequest } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { PreflightResult } from "@/types";
import { formatApiError } from "@/utils/formatApiError";

type PreflightPanelProps = {
  revisionId: string;
  onPreflightChange?: (preflight: PreflightResult | null) => void;
};

export function PreflightPanel({ revisionId, onPreflightChange }: PreflightPanelProps) {
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const result = await getPreflight(revisionId, { signal });
      setPreflight(result);
      setError("");
      onPreflightChange?.(result);
    } catch (err) {
      if (isCancelledRequest(err, signal)) {
        return;
      }
      setPreflight(null);
      onPreflightChange?.(null);
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [revisionId]);

  const checkMessages = buildPreflightCheckMessageMap(preflight?.deterministic_checks);

  if (error) {
    return (
      <Card id="preflight">
        <CardHeader>
          <CardTitle className="text-base">Preflight</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-destructive">{error}</p>
          <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>
            Retry preflight
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (loading || !preflight) {
    return (
      <Card id="preflight">
        <CardHeader>
          <CardTitle className="text-base">Preflight</CardTitle>
          <CardDescription>Evaluating analysis and export readiness…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card id="preflight">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div>
          <CardTitle className="text-base">Preflight</CardTitle>
          <CardDescription>
            {preflight.analysis_eligible &&
            preflight.analysis_blockers.length === 0 &&
            preflight.export_blockers.length === 0 &&
            preflight.warnings.length === 0
              ? "Analysis and export checks passed."
              : "Readiness checks for analysis runs and export drafts."}
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={() => void refresh()}>
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {!preflight.analysis_eligible ? (
          <p className="rounded-sm border border-border border-l-4 border-l-amber-500 bg-card px-3 py-2 text-foreground">
            Analysis runs are blocked until preflight blockers are resolved.
          </p>
        ) : null}
        {!preflight.export_eligible ? (
          <p className="rounded-sm border border-border border-l-4 border-l-destructive bg-card px-3 py-2 text-foreground">
            Export drafts are blocked until the export blockers below are resolved in the
            sealed package.
          </p>
        ) : null}
        <PreflightCheckList
          title="Analysis blockers"
          codes={preflight.analysis_blockers}
          checkMessages={checkMessages}
        />
        <PreflightCheckList
          title="Export blockers"
          codes={preflight.export_blockers}
          checkMessages={checkMessages}
        />
        <PreflightCheckList
          title="Warnings"
          codes={preflight.warnings}
          checkMessages={checkMessages}
          tone="warning"
        />
      </CardContent>
    </Card>
  );
}
