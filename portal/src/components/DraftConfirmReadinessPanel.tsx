import { useCallback, useEffect, useState } from "react";
import { getDraftExportReadiness, isCancelledRequest } from "@/api/client";
import { PreflightCheckList } from "@/components/PreflightCheckList";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { DraftExportReadiness } from "@/types";
import { formatProblemError } from "@/utils/formatProblemError";

type DraftConfirmReadinessPanelProps = {
  revisionId: string;
  refreshKey: string;
  onReadinessChange?: (readiness: DraftExportReadiness | null) => void;
};

export function DraftConfirmReadinessPanel({
  revisionId,
  refreshKey,
  onReadinessChange,
}: DraftConfirmReadinessPanelProps) {
  const [readiness, setReadiness] = useState<DraftExportReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!revisionId) {
        setReadiness(null);
        onReadinessChange?.(null);
        setLoading(false);
        return;
      }
      setLoading(true);
      setError("");
      try {
        const result = await getDraftExportReadiness(revisionId, { signal });
        setReadiness(result);
        onReadinessChange?.(result);
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setReadiness(null);
        onReadinessChange?.(null);
        setError(formatProblemError(err));
      } finally {
        setLoading(false);
      }
    },
    [onReadinessChange, revisionId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh, refreshKey]);

  if (loading && !readiness) {
    return (
      <Card className="bg-muted/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Confirm readiness</CardTitle>
          <CardDescription>Checking export blockers before seal…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive/30">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Confirm readiness</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (!readiness) {
    return null;
  }

  return (
    <Card id="draft-export-readiness" className="bg-muted/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Confirm readiness</CardTitle>
        <CardDescription>
          {readiness.export_eligible
            ? "This draft satisfies export structural requirements. Confirming seals the package for analysis and later export."
            : "Resolve the blockers below before confirming. Assessor and privacy items are populated by upload and intake, not manual JSON edits."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {readiness.export_eligible ? (
          <p className="text-sm text-muted-foreground">
            Warnings may still appear at export time after review; they do not block confirm.
          </p>
        ) : (
          <PreflightCheckList
            title="Resolve before confirm"
            codes={readiness.export_blockers}
            tone="blocker"
          />
        )}
        {readiness.warnings.length > 0 ? (
          <PreflightCheckList
            title="Warnings (do not block confirm)"
            codes={readiness.warnings}
            tone="warning"
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
