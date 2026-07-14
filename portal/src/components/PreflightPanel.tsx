import { useEffect, useState } from "react";
import { getPreflight, isCancelledRequest } from "@/api/client";
import { Badge } from "@/components/ui/badge";
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

  if (error) {
    return (
      <Card>
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
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Preflight</CardTitle>
          <CardDescription>Evaluating analysis and export readiness…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div>
          <CardTitle className="text-base">Preflight</CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-2">
            <span>Analysis eligible</span>
            <Badge variant={preflight.analysis_eligible ? "default" : "destructive"}>
              {preflight.analysis_eligible ? "yes" : "no"}
            </Badge>
            <span>· Export eligible</span>
            <Badge variant={preflight.export_eligible ? "default" : "destructive"}>
              {preflight.export_eligible ? "yes" : "no"}
            </Badge>
            <span>
              · Score {preflight.readiness.numerator}/{preflight.readiness.denominator}
            </span>
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="ghost" onClick={() => void refresh()}>
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {!preflight.analysis_eligible ? (
          <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-50">
            Analysis runs are blocked until preflight blockers are resolved.
          </p>
        ) : null}
        {preflight.analysis_blockers.length > 0 ? (
          <div>
            <p className="font-medium">Analysis blockers</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {preflight.analysis_blockers.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {preflight.export_blockers.length > 0 ? (
          <div>
            <p className="font-medium">Export blockers</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {preflight.export_blockers.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {preflight.warnings.length > 0 ? (
          <div>
            <p className="font-medium">Warnings</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {preflight.warnings.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
