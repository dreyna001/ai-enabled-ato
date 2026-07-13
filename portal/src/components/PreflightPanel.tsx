import { useEffect, useState } from "react";
import { getPreflight, isCancelledRequest } from "@/api/client";
import { Badge } from "@/components/ui/badge";
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
};

export function PreflightPanel({ revisionId }: PreflightPanelProps) {
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getPreflight(revisionId, { signal: controller.signal })
      .then((result) => {
        setPreflight(result);
        setError("");
      })
      .catch((err) => {
        if (isCancelledRequest(err, controller.signal)) {
          return;
        }
        setError(formatApiError(err));
      });
    return () => controller.abort();
  }, [revisionId]);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Preflight</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-destructive">{error}</CardContent>
      </Card>
    );
  }

  if (!preflight) {
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
      <CardHeader>
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
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
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
