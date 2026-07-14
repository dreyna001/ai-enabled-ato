import { useEffect, useState } from "react";
import { getChangeAnalysis, isCancelledRequest } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { ChangeAnalysisResult } from "@/types";
import { formatApiError } from "@/utils/formatApiError";

type ChangeAnalysisPanelProps = {
  revisionId: string;
  parentRevisionId?: string | null;
  onTargetedIds?: (ids: string[]) => void;
};

export function ChangeAnalysisPanel({
  revisionId,
  parentRevisionId,
  onTargetedIds,
}: ChangeAnalysisPanelProps) {
  const [analysis, setAnalysis] = useState<ChangeAnalysisResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!parentRevisionId) {
      setAnalysis(null);
      setError("");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    void getChangeAnalysis(revisionId, { signal: controller.signal })
      .then((result) => {
        setAnalysis(result);
        setError("");
        onTargetedIds?.(result.targeted_assessment_item_ids);
      })
      .catch((err) => {
        if (isCancelledRequest(err, controller.signal)) {
          return;
        }
        setAnalysis(null);
        setError(formatApiError(err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [revisionId, parentRevisionId, onTargetedIds]);

  if (!parentRevisionId) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Change analysis</CardTitle>
        <CardDescription>
          Delta against parent {parentRevisionId.slice(0, 8)}… for targeted re-analysis.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {loading ? <p className="text-muted-foreground">Computing revision delta…</p> : null}
        {error ? <p className="text-destructive">{error}</p> : null}
        {analysis ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge variant={analysis.requires_targeted_reanalysis ? "default" : "muted"}>
                {analysis.requires_targeted_reanalysis
                  ? "Targeted re-analysis recommended"
                  : "No material changes"}
              </Badge>
              <span>{analysis.targeted_assessment_item_ids.length} targeted items</span>
            </div>
            {analysis.delta.changed_control_ids.length > 0 ? (
              <div>
                <p className="font-medium">Changed controls</p>
                <ul className="list-disc pl-5 text-muted-foreground">
                  {analysis.delta.changed_control_ids.map((id) => (
                    <li key={id}>{id}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {analysis.delta.added_artifact_ids.length > 0 ? (
              <div>
                <p className="font-medium">Added artifacts</p>
                <p className="text-muted-foreground">
                  {analysis.delta.added_artifact_ids.length} new artifact(s)
                </p>
              </div>
            ) : null}
            {analysis.targeted_assessment_item_ids.length > 0 ? (
              <div>
                <p className="font-medium">Targeted assessment items</p>
                <ul className="list-disc pl-5 font-mono text-xs text-muted-foreground">
                  {analysis.targeted_assessment_item_ids.map((id) => (
                    <li key={id}>{id}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
