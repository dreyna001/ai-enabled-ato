import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import {
  approveExport,
  createExportDraft,
  createReviewComment,
  createReviewRevision,
  downloadExport,
  listMatrixRows,
  listReviewComments,
  rejectExport,
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
import { PreflightCheckList, buildPreflightCheckMessageMap } from "@/components/PreflightCheckList";
import type {
  Approval,
  Disposition,
  ExportDraft,
  MatrixRow,
  PreflightResult,
  ReviewComment,
  ReviewRevision,
  SessionInfo,
} from "@/types";
import { formatApiError } from "@/utils/formatApiError";
import { exportNotReadyMessage } from "@/utils/preflightLabels";
import { problemMessageForCode } from "@/utils/problemMessages";
import {
  clearStoredReviewRevisionId,
  loadStoredReviewRevisionId,
  saveStoredReviewRevisionId,
} from "@/utils/workflowStorage";

const DISPOSITION_OPTIONS = [
  "pending",
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
  preflight?: PreflightResult | null;
};

export function ReviewExportWorkbench({
  session,
  runId,
  matrixRows,
  preflight = null,
}: ReviewExportWorkbenchProps) {
  const [review, setReview] = useState<ReviewRevision | null>(null);
  const [exportDraft, setExportDraft] = useState<ExportDraft | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [rowCommentBody, setRowCommentBody] = useState("");
  const [activeRowCommentId, setActiveRowCommentId] = useState<string>("");
  const [loadedMatrixRows, setLoadedMatrixRows] = useState<MatrixRow[]>(matrixRows);
  const [rejectReason, setRejectReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reviewEtagValue = useMemo(
    () => (review ? revisionEtag(review.version) : '"v1"'),
    [review],
  );

  const dispositionByRow = useMemo(() => {
    const map = new Map<string, Disposition>();
    for (const item of review?.dispositions ?? []) {
      map.set(item.matrix_row_id, item);
    }
    return map;
  }, [review]);

  const unresolvedCount = useMemo(
    () =>
      (review?.dispositions ?? []).filter((item) => item.decision === "pending").length,
    [review],
  );

  const exportDraftUnavailable =
    exportDraft?.status === "expired" ||
    exportDraft?.status === "superseded" ||
    exportDraft?.status === "rejected";

  const isSubmitter = approval?.submitted_by === session.actor_id;
  const canDecideApproval = !isSubmitter || session.single_user_mode_enabled;

  useEffect(() => {
    if (matrixRows.length > 0) {
      setLoadedMatrixRows(matrixRows);
      return;
    }
    void listMatrixRows(runId, { limit: 100 })
      .then((page) => setLoadedMatrixRows(page.items))
      .catch(() => setLoadedMatrixRows([]));
  }, [runId, matrixRows]);

  const exportReadinessBlocked = preflight !== null && !preflight.export_eligible;
  const exportBlockers = preflight?.export_blockers ?? [];
  const preflightCheckMessages = buildPreflightCheckMessageMap(
    preflight?.deterministic_checks,
  );

  const handleApiError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.errorCode === "export_not_ready" && exportBlockers.length > 0) {
        setError(exportNotReadyMessage(exportBlockers));
        return;
      }
      setError(problemMessageForCode(err.errorCode, formatApiError(err)));
      if (err.status === 412 || err.errorCode === "etag_mismatch") {
        setError("Review version changed on the server. Reload the page to continue.");
      }
      return;
    }
    setError(formatApiError(err));
  };

  const openReview = async () => {
    setBusy(true);
    setError("");
    try {
      const opened = await createReviewRevision(session, runId);
      setReview(opened);
      setMessage(
        opened.status === "submitted"
          ? "Review already submitted. Continue with export."
          : "Review revision opened.",
      );
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const storedId = loadStoredReviewRevisionId(runId);
    if (storedId && !review) {
      void openReview();
    }
    // Resume once per run when a stored review id exists.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (!review) {
      setComments([]);
      return;
    }
    saveStoredReviewRevisionId(runId, review.review_revision_id);
    void listReviewComments(review.review_revision_id)
      .then((result) => setComments(result.items))
      .catch(() => setComments([]));
  }, [review?.review_revision_id, review?.status, runId]);

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
      const updated = await updateDisposition(
        session,
        review.review_revision_id,
        matrixRowId,
        reviewEtagValue,
        {
          decision,
          edited_summary: decision === "edited" ? editedSummary : editedSummary || null,
          notes: null,
        },
      );
      setReview({
        ...review,
        version: review.version + 1,
        dispositions: review.dispositions.map((item) =>
          item.matrix_row_id === matrixRowId ? { ...item, ...updated } : item,
        ),
      });
      const ids: string[] = [];
      if (updated.evidence_request_id) {
        ids.push(`evidence request ${updated.evidence_request_id.slice(0, 8)}…`);
      }
      if (updated.poam_candidate_id) {
        ids.push(`POA&M candidate ${updated.poam_candidate_id.slice(0, 8)}…`);
      }
      setMessage(
        ids.length > 0
          ? `Disposition saved. Created ${ids.join(" and ")}.`
          : `Disposition saved for ${matrixRowId.slice(0, 8)}…`,
      );
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    if (!review) {
      return;
    }
    if (unresolvedCount > 0) {
      setError(`${unresolvedCount} disposition(s) still pending. Resolve each row before submit.`);
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
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const addComment = async (matrixRowId?: string | null) => {
    if (!review) {
      return;
    }
    const body = (matrixRowId ? rowCommentBody : commentBody).trim();
    if (!body) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await createReviewComment(session, review.review_revision_id, {
        body,
        matrix_row_id: matrixRowId ?? null,
      });
      setComments((current) => [created, ...current]);
      if (matrixRowId) {
        setRowCommentBody("");
        setActiveRowCommentId("");
      } else {
        setCommentBody("");
      }
      setMessage("Comment added.");
    } catch (err) {
      handleApiError(err);
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
      setApproval(null);
      setMessage("Export draft created.");
    } catch (err) {
      handleApiError(err);
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
      setExportDraft({ ...exportDraft, status: "pending_approval" });
      setMessage("Export submitted for approval.");
    } catch (err) {
      handleApiError(err);
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
      setExportDraft((current) =>
        current ? { ...current, status: "approved" } : current,
      );
      setMessage("Export approved.");
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!approval || !rejectReason.trim()) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const rejected = await rejectExport(session, approval.approval_id, rejectReason.trim());
      setApproval(rejected);
      setExportDraft((current) =>
        current ? { ...current, status: "rejected" } : current,
      );
      setMessage("Export rejected.");
    } catch (err) {
      handleApiError(err);
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
      const { blob, filename } = await downloadExport(session, approval.approval_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(`Downloaded ${filename}.`);
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const resetReview = () => {
    setReview(null);
    setExportDraft(null);
    setApproval(null);
    clearStoredReviewRevisionId(runId);
    setMessage("Review state cleared for this run.");
  };

  return (
    <Card id="review-export">
      <CardHeader>
        <CardTitle className="text-base">Review and Export</CardTitle>
        <CardDescription>
          Resolve matrix dispositions, submit review, and complete hash-bound export approval.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {!review ? (
          <Button type="button" size="sm" disabled={busy} onClick={() => void openReview()}>
            {loadStoredReviewRevisionId(runId)
              ? "Resume review revision"
              : "Open review revision"}
          </Button>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>Review status</span>
              <Badge variant="muted">{review.status}</Badge>
              <span className="font-mono">· version {review.version}</span>
              {unresolvedCount > 0 ? (
                <Badge variant="destructive">{unresolvedCount} unresolved</Badge>
              ) : (
                <Badge variant="default">All dispositions resolved</Badge>
              )}
              <Button type="button" size="sm" variant="ghost" onClick={resetReview}>
                Clear local resume
              </Button>
            </div>

            {review.status === "draft" ? (
              <div className="space-y-4">
                {loadedMatrixRows.map((row) => {
                  const disposition = dispositionByRow.get(row.matrix_row_id);
                  return (
                    <DispositionEditor
                      key={row.matrix_row_id}
                      row={row}
                      initialDecision={disposition?.decision ?? "pending"}
                      initialSummary={disposition?.edited_summary ?? ""}
                      evidenceRequestId={disposition?.evidence_request_id}
                      poamCandidateId={disposition?.poam_candidate_id}
                      disabled={busy}
                      showRowComment={activeRowCommentId === row.matrix_row_id}
                      rowCommentBody={rowCommentBody}
                      onToggleRowComment={() =>
                        setActiveRowCommentId((current) =>
                          current === row.matrix_row_id ? "" : row.matrix_row_id,
                        )
                      }
                      onRowCommentChange={setRowCommentBody}
                      onAddRowComment={() => void addComment(row.matrix_row_id)}
                      onSave={(decision, summary) =>
                        void saveDisposition(row.matrix_row_id, decision, summary)
                      }
                    />
                  );
                })}
                <div className="space-y-2 rounded-sm border p-4">
                  <Label htmlFor="review-comment">Package-level review comment</Label>
                  <Input
                    id="review-comment"
                    value={commentBody}
                    disabled={busy}
                    onChange={(event) => setCommentBody(event.target.value)}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy || !commentBody.trim()}
                    onClick={() => void addComment()}
                  >
                    Add comment
                  </Button>
                </div>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || unresolvedCount > 0}
                  onClick={() => void submitReview()}
                >
                  Submit review
                </Button>
                {unresolvedCount > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    Pending dispositions must be explicitly resolved — they are not auto-accepted.
                  </p>
                ) : null}
              </div>
            ) : null}

            {comments.length > 0 ? (
              <div className="space-y-2">
                <p className="text-sm font-medium">Comments</p>
                {comments.map((comment) => (
                  <div key={comment.comment_id} className="rounded-sm border p-3 text-sm">
                    <p>{comment.body}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {comment.created_by} · {comment.created_at}
                      {comment.matrix_row_id ? (
                        <>
                          {" · row "}
                          <span className="font-mono">
                            {comment.matrix_row_id.slice(0, 8)}…
                          </span>
                        </>
                      ) : (
                        " · package"
                      )}
                    </p>
                  </div>
                ))}
              </div>
            ) : null}

            {review.status === "submitted" ? (
              <div className="space-y-4">
                {exportReadinessBlocked ? (
                  <div className="space-y-3 rounded-sm border border-destructive/30 bg-destructive/5 p-4">
                    <p className="text-sm font-medium text-foreground">
                      Export is blocked until these sealed-package items are resolved
                    </p>
                    <PreflightCheckList
                      title="Export blockers"
                      codes={exportBlockers}
                      checkMessages={preflightCheckMessages}
                    />
                    <p className="text-sm text-muted-foreground">
                      These blockers must be resolved before confirm on a new revision (upload
                      assessor and privacy artifacts during intake). Sealed revisions cannot be
                      edited in place.{" "}
                      <a className="text-link underline underline-offset-4" href="#preflight">
                        View full Preflight
                      </a>
                    </p>
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy || exportReadinessBlocked}
                    onClick={() => void createDraft()}
                  >
                    Create export draft
                  </Button>
                  {exportDraft ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy || exportDraft.status !== "draft"}
                      onClick={() => void submitDraft()}
                    >
                      Submit for approval
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}

            {exportDraft ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span>Export draft</span>
                <Badge variant={exportDraft.status === "approved" ? "default" : "muted"}>
                  {exportDraft.status}
                </Badge>
                <span className="font-mono text-xs">
                  {exportDraft.payload_manifest_sha256.slice(0, 16)}…
                </span>
              </div>
            ) : null}

            {exportDraftUnavailable ? (
              <p className="text-sm text-destructive">
                Export is no longer available ({exportDraft?.status}). Create a new export draft
                after review changes.
              </p>
            ) : null}

            {approval ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={approval.decision === "approved" ? "default" : "muted"}>
                    {approval.decision}
                  </Badge>
                  {isSubmitter ? (
                    <span className="text-xs text-muted-foreground">
                      {session.single_user_mode_enabled
                        ? "Single-user demo mode allows you to approve or reject your own export."
                        : "You submitted this export — a different approver must approve or reject."}
                    </span>
                  ) : null}
                  {approval.decision === "pending" && canDecideApproval ? (
                    <>
                      <Button type="button" size="sm" disabled={busy} onClick={() => void approve()}>
                        Approve export
                      </Button>
                      <Input
                        aria-label="Reject reason"
                        placeholder="Reject reason"
                        value={rejectReason}
                        disabled={busy}
                        onChange={(event) => setRejectReason(event.target.value)}
                      />
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy || !rejectReason.trim()}
                        onClick={() => void reject()}
                      >
                        Reject export
                      </Button>
                    </>
                  ) : null}
                  {approval.decision === "approved" && exportDraft?.status === "approved" ? (
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
                {approval.decision === "rejected" && approval.reason ? (
                  <p className="text-sm text-muted-foreground">Reason: {approval.reason}</p>
                ) : null}
                {approval.decision === "pending" ? (
                  <p className="text-xs text-muted-foreground">Expires {approval.expires_at}</p>
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
  evidenceRequestId,
  poamCandidateId,
  disabled,
  showRowComment,
  rowCommentBody,
  onToggleRowComment,
  onRowCommentChange,
  onAddRowComment,
  onSave,
}: {
  row: MatrixRow;
  initialDecision: string;
  initialSummary: string;
  evidenceRequestId?: string;
  poamCandidateId?: string;
  disabled: boolean;
  showRowComment: boolean;
  rowCommentBody: string;
  onToggleRowComment: () => void;
  onRowCommentChange: (value: string) => void;
  onAddRowComment: () => void;
  onSave: (decision: string, summary: string) => void;
}) {
  const [decision, setDecision] = useState(initialDecision);
  const [summary, setSummary] = useState(initialSummary);

  useEffect(() => {
    setDecision(initialDecision);
    setSummary(initialSummary);
  }, [initialDecision, initialSummary]);

  return (
    <div className="rounded-sm border p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{row.assessment_item_id}</span>
        <Badge variant="muted">{row.model_proposed_status}</Badge>
        {decision === "pending" ? <Badge variant="destructive">pending</Badge> : null}
      </div>
      <p className="text-sm text-muted-foreground">{row.finding_summary}</p>
      {evidenceRequestId ? (
        <p className="text-xs font-mono text-muted-foreground">
          Evidence request: {evidenceRequestId}
        </p>
      ) : null}
      {poamCandidateId ? (
        <p className="text-xs font-mono text-muted-foreground">
          POA&M candidate: {poamCandidateId}
        </p>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={`decision-${row.matrix_row_id}`}>Disposition</Label>
          <select
            id={`decision-${row.matrix_row_id}`}
            className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
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
          <Label htmlFor={`summary-${row.matrix_row_id}`}>
            Edited summary{decision === "edited" ? " (required)" : ""}
          </Label>
          <Input
            id={`summary-${row.matrix_row_id}`}
            value={summary}
            disabled={disabled}
            onChange={(event) => setSummary(event.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={
            disabled ||
            decision === "pending" ||
            (decision === "edited" && !summary.trim())
          }
          onClick={() => onSave(decision, summary)}
        >
          Save disposition
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onToggleRowComment}>
          Row comment
        </Button>
      </div>
      {showRowComment ? (
        <div className="flex flex-wrap gap-2">
          <Input
            aria-label={`Comment for ${row.assessment_item_id}`}
            value={rowCommentBody}
            disabled={disabled}
            onChange={(event) => onRowCommentChange(event.target.value)}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={disabled || !rowCommentBody.trim()}
            onClick={onAddRowComment}
          >
            Post row comment
          </Button>
        </div>
      ) : null}
    </div>
  );
}
