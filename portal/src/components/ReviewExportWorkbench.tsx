import { useMemo, useState } from "react";
import {
  approveExport,
  createExportDraft,
  createReviewRevision,
  downloadExport,
  revisionEtag,
  submitExportDraft,
  submitReviewRevision,
  updateDisposition,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  Approval,
  ExportDraft,
  MatrixRow,
  ReviewRevision,
  SessionInfo,
} from "@/types";
import { formatApiError } from "@/utils/formatApiError";

const DISPOSITION_OPTIONS = [
  "accepted",
  "edited",
  "rejected",
  "evidence_requested",
  "weakness_confirmed",
] as const;

type ReviewExportWorkbenchProps = {
  session: SessionInfo;
  runId: string;
  matrixRows: MatrixRow[];
};

export function ReviewExportWorkbench({
  session,
  runId,
  matrixRows,
}: ReviewExportWorkbenchProps) {
  const [review, setReview] = useState<ReviewRevision | null>(null);
  const [exportDraft, setExportDraft] = useState<ExportDraft | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reviewEtag = useMemo(
    () => (review ? revisionEtag(review.version) : '"v1"'),
    [review],
  );

  const dispositionByRow = useMemo(() => {
    const map = new Map<string, ReviewRevision["dispositions"][number]>();
    for (const item of review?.dispositions ?? []) {
      map.set(item.matrix_row_id, item);
    }
    return map;
  }, [review]);

  const startReview = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await createReviewRevision(session, runId);
      setReview(created);
      setMessage("Review revision opened.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const saveDisposition = async (
    matrixRowId: string,
    decision: string,
    editedSummary: string,
  ) => {
    if (!review) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await updateDisposition(session, review.review_revision_id, matrixRowId, reviewEtag, {
        decision,
        edited_summary: editedSummary || null,
        notes: null,
      });
      const refreshed = {
        ...review,
        version: review.version + 1,
        dispositions: review.dispositions.map((item) =>
          item.matrix_row_id === matrixRowId
            ? {
                ...item,
                decision,
                edited_summary: editedSummary || null,
                version: item.version + 1,
              }
            : item,
        ),
      };
      setReview(refreshed);
      setMessage(`Disposition saved for ${matrixRowId.slice(0, 8)}…`);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    if (!review) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const submitted = await submitReviewRevision(
        session,
        review.review_revision_id,
        revisionEtag(review.version),
      );
      setReview(submitted);
      setMessage("Review submitted.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const createDraft = async () => {
    if (!review) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const draft = await createExportDraft(session, review.review_revision_id);
      setExportDraft(draft);
      setMessage("Export draft created.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const submitDraft = async () => {
    if (!exportDraft) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const pending = await submitExportDraft(session, exportDraft.export_draft_id);
      setApproval(pending);
      setMessage("Export submitted for approval.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!approval) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const approved = await approveExport(session, approval.approval_id);
      setApproval(approved);
      setMessage("Export approved.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    if (!approval) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const blob = await downloadExport(session, approval.approval_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `ato-export-${approval.approval_id}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage("Export ZIP downloaded.");
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Review and export</CardTitle>
        <CardDescription>
          Resolve matrix dispositions, submit review, and complete hash-bound export approval.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {!review ? (
          <Button type="button" size="sm" disabled={busy} onClick={() => void startReview()}>
            Open review revision
          </Button>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>Review status</span>
              <Badge variant="muted">{review.status}</Badge>
              <span>· version {review.version}</span>
            </div>

            {review.status === "draft" ? (
              <div className="space-y-4">
                {matrixRows.map((row) => {
                  const disposition = dispositionByRow.get(row.matrix_row_id);
                  return (
                    <DispositionEditor
                      key={row.matrix_row_id}
                      row={row}
                      initialDecision={disposition?.decision ?? "pending"}
                      initialSummary={disposition?.edited_summary ?? ""}
                      disabled={busy}
                      onSave={(decision, summary) =>
                        void saveDisposition(row.matrix_row_id, decision, summary)
                      }
                    />
                  );
                })}
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  onClick={() => void submitReview()}
                >
                  Submit review
                </Button>
              </div>
            ) : null}

            {review.status === "submitted" ? (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  onClick={() => void createDraft()}
                >
                  Create export draft
                </Button>
                {exportDraft ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void submitDraft()}
                  >
                    Submit for approval
                  </Button>
                ) : null}
              </div>
            ) : null}

            {approval ? (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={approval.decision === "approved" ? "default" : "muted"}>
                  {approval.decision}
                </Badge>
                {approval.decision === "pending" ? (
                  <Button type="button" size="sm" disabled={busy} onClick={() => void approve()}>
                    Approve export
                  </Button>
                ) : null}
                {approval.decision === "approved" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void download()}
                  >
                    Download ZIP
                  </Button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function DispositionEditor({
  row,
  initialDecision,
  initialSummary,
  disabled,
  onSave,
}: {
  row: MatrixRow;
  initialDecision: string;
  initialSummary: string;
  disabled: boolean;
  onSave: (decision: string, summary: string) => void;
}) {
  const [decision, setDecision] = useState(
    initialDecision === "pending" ? "accepted" : initialDecision,
  );
  const [summary, setSummary] = useState(initialSummary);

  return (
    <div className="rounded-md border p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{row.assessment_item_id}</span>
        <Badge variant="muted">{row.model_proposed_status}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{row.finding_summary}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={`decision-${row.matrix_row_id}`}>Disposition</Label>
          <select
            id={`decision-${row.matrix_row_id}`}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={decision}
            disabled={disabled}
            onChange={(event) => setDecision(event.target.value)}
          >
            {DISPOSITION_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`summary-${row.matrix_row_id}`}>Edited summary</Label>
          <Input
            id={`summary-${row.matrix_row_id}`}
            value={summary}
            disabled={disabled}
            onChange={(event) => setSummary(event.target.value)}
          />
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => onSave(decision, summary)}
      >
        Save disposition
      </Button>
    </div>
  );
}
