import { useCallback, useEffect, useState } from "react";
import { listMatrixRows, isCancelledRequest } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/EmptyState";
import { MatrixTableSkeleton } from "@/components/LoadingSkeletons";
import type { AnalysisRun, MatrixRow } from "@/types";
import { formatApiError } from "@/utils/formatApiError";
import { runStatusVariant } from "@/utils/statusLabels";

const PAGE_SIZE = 25;

type MatrixResultsPanelProps = {
  run: AnalysisRun;
};

export function MatrixResultsPanel({ run }: MatrixResultsPanelProps) {
  const [rows, setRows] = useState<MatrixRow[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedRowId, setSelectedRowId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadPage = useCallback(
    async (pageCursor: string | null, signal?: AbortSignal) => {
      setLoading(true);
      setError("");
      try {
        const page = await listMatrixRows(run.run_id, {
          cursor: pageCursor,
          limit: PAGE_SIZE,
          signal,
        });
        setRows(page.items);
        setTotal(page.total);
        setNextCursor(page.next_cursor);
        setCursor(pageCursor);
        if (page.items.length > 0 && !selectedRowId) {
          setSelectedRowId(page.items[0].matrix_row_id);
        }
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setError(formatApiError(err));
      } finally {
        setLoading(false);
      }
    },
    [run.run_id, selectedRowId],
  );

  useEffect(() => {
    if (run.status !== "succeeded") {
      setRows([]);
      setTotal(0);
      return;
    }
    const controller = new AbortController();
    void loadPage(null, controller.signal);
    return () => controller.abort();
  }, [run.run_id, run.status, loadPage]);

  const selectedRow = rows.find((row) => row.matrix_row_id === selectedRowId) ?? null;

  if (run.status === "failed" || run.status === "cancelled" || run.status === "policy_blocked") {
    return (
      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="text-base">Run ended without matrix</CardTitle>
          <CardDescription className="flex flex-wrap items-center gap-2">
            <Badge variant={runStatusVariant(run.status)}>{run.status}</Badge>
            {run.error_code ? <span className="font-mono text-xs">{run.error_code}</span> : null}
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {run.status === "cancelled"
            ? "This run was cancelled before matrix rows were produced."
            : run.status === "policy_blocked"
              ? "Policy blocked model or analyzer execution for this run."
              : "Analysis failed before matrix rows were available."}
        </CardContent>
      </Card>
    );
  }

  if (run.status === "queued" || run.status === "running") {
    return (
      <p className="text-sm text-muted-foreground">
        Matrix rows appear after the run succeeds.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {loading && rows.length === 0 ? <MatrixTableSkeleton /> : null}
      {!loading && rows.length === 0 && total === 0 ? (
        <EmptyState
          title="No matrix rows"
          description="This run completed but produced zero sufficiency matrix rows."
        />
      ) : null}
      {rows.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>
              Showing {rows.length} of {total} row{total === 1 ? "" : "s"}
            </span>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={loading || cursor === null}
                onClick={() => void loadPage(null)}
              >
                First page
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={loading || !nextCursor}
                onClick={() => void loadPage(nextCursor)}
              >
                Next page
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="px-4 py-2 text-left font-medium">Item</th>
                  <th className="px-4 py-2 text-left font-medium">Proposed</th>
                  <th className="px-4 py-2 text-left font-medium">System</th>
                  <th className="px-4 py-2 text-left font-medium">Summary</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.matrix_row_id}
                    className={`border-b border-border/60 last:border-0 cursor-pointer ${
                      row.matrix_row_id === selectedRowId ? "bg-muted/30" : ""
                    }`}
                    onClick={() => setSelectedRowId(row.matrix_row_id)}
                  >
                    <td className="px-4 py-3 align-top font-mono text-xs">
                      {row.assessment_item_id}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge variant="muted">{row.model_proposed_status}</Badge>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge variant="muted">{row.system_status}</Badge>
                    </td>
                    <td className="px-4 py-3 align-top text-muted-foreground">
                      {row.finding_summary}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selectedRow ? <MatrixRowDetail row={selectedRow} /> : null}
        </>
      ) : null}
    </div>
  );
}

function MatrixRowDetail({ row }: { row: MatrixRow }) {
  return (
    <Card className="bg-muted/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-mono">{row.assessment_item_id}</CardTitle>
        <CardDescription>{row.assessment_item_type}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{row.finding_summary}</p>
        {row.gaps && row.gaps.length > 0 ? (
          <div>
            <p className="font-medium">Gaps</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {row.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {row.assessor_questions && row.assessor_questions.length > 0 ? (
          <div>
            <p className="font-medium">Assessor questions</p>
            <ul className="list-disc pl-5 text-muted-foreground">
              {row.assessor_questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {row.citations && row.citations.length > 0 ? (
          <div>
            <p className="font-medium">Citations</p>
            <ul className="space-y-2">
              {row.citations.map((citation, index) => (
                <li key={`${citation.source_sha256 ?? index}-${index}`} className="rounded border p-2 text-xs">
                  <span className="font-mono">
                    {citation.source_kind ?? "source"} ·{" "}
                    {(citation.source_sha256 ?? citation.sha256 ?? "").slice(0, 16)}
                  </span>
                  {citation.excerpt ? (
                    <p className="mt-1 text-muted-foreground">{String(citation.excerpt)}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export type { MatrixRow };
