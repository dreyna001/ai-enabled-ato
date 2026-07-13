import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  cancelRun,
  confirmRevision,
  createRevision,
  createSystem,
  finalizeRevision,
  getRevision,
  getRun,
  isCancelledRequest,
  listMatrixRows,
  listRevisions,
  listRuns,
  listSystems,
  startRun,
} from "@/api/client";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { IntakeProgressPanel } from "@/components/IntakeProgressPanel";
import {
  MatrixTableSkeleton,
  RevisionWorkflowSkeleton,
  SystemsListSkeleton,
} from "@/components/LoadingSkeletons";
import { PackageEditor } from "@/components/PackageEditor";
import { PackageUploadPanel } from "@/components/PackageUploadPanel";
import { PortalLoadFailure } from "@/components/PortalLoadFailure";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { usePolling } from "@/hooks/usePolling";
import { usePackageDraft } from "@/hooks/usePackageDraft";
import { cn } from "@/lib/utils";
import type {
  AnalysisRun,
  MatrixRow,
  PackageRevision,
  SessionInfo,
  System,
} from "@/types";
import {
  resolveRevisionsEmptyState,
  resolveRunsEmptyState,
  resolveSystemsEmptyState,
} from "@/utils/emptyStates";
import { formatApiError } from "@/utils/formatApiError";
import {
  revisionStatusVariant,
  runStatusVariant,
} from "@/utils/statusLabels";

type LoadState = "loading" | "ready" | "error" | "empty";

type ConfirmState =
  | {
      kind: "cancel-run";
      runId: string;
    }
  | {
      kind: "confirm-revision";
      revisionId: string;
      etag: string;
    }
  | null;

function AlertBanner({
  tone,
  children,
}: {
  tone: "error" | "info" | "warning";
  children: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-4 py-3 text-sm",
        tone === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
        tone === "info" && "border-primary/20 bg-primary/5 text-foreground",
        tone === "warning" && "border-amber-500/30 bg-amber-500/10 text-amber-50",
      )}
    >
      {children}
    </div>
  );
}

function SelectionList({
  items,
  selectedId,
  onSelect,
  renderLabel,
}: {
  items: Array<{ id: string; label: string; status?: string }>;
  selectedId: string;
  onSelect: (id: string) => void;
  renderLabel?: (item: { id: string; label: string; status?: string }) => string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <Button
          key={item.id}
          type="button"
          size="sm"
          variant={item.id === selectedId ? "default" : "outline"}
          onClick={() => onSelect(item.id)}
        >
          {renderLabel ? renderLabel(item) : item.label}
        </Button>
      ))}
    </div>
  );
}

type WorkflowPageProps = {
  session: SessionInfo;
};

export function WorkflowPage({ session }: WorkflowPageProps) {
  const navigate = useNavigate();
  const params = useParams();
  const routeSystemId = params.systemId ?? "";
  const routeRevisionId = params.revisionId ?? "";

  const [systems, setSystems] = useState<System[]>([]);
  const [selectedSystemId, setSelectedSystemId] = useState(routeSystemId);
  const [revisions, setRevisions] = useState<PackageRevision[]>([]);
  const [selectedRevisionId, setSelectedRevisionId] = useState(routeRevisionId);
  const [revision, setRevision] = useState<PackageRevision | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null);
  const [matrixRows, setMatrixRows] = useState<MatrixRow[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [systemsState, setSystemsState] = useState<LoadState>("loading");
  const [revisionState, setRevisionState] = useState<LoadState>("empty");
  const [confirmState, setConfirmState] = useState<ConfirmState>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);

  const draftEnabled =
    Boolean(revision) && revision?.status === "awaiting_confirmation";

  const packageDraft = usePackageDraft(session, selectedRevisionId, {
    enabled: draftEnabled,
    onSaved: () => setMessage("Draft saved."),
  });

  const syncRoute = useCallback(
    (systemId: string, revisionId: string) => {
      if (!systemId) {
        navigate("/workflow", { replace: true });
        return;
      }
      if (!revisionId) {
        navigate(`/workflow/systems/${systemId}`, { replace: true });
        return;
      }
      navigate(`/workflow/systems/${systemId}/revisions/${revisionId}`, {
        replace: true,
      });
    },
    [navigate],
  );

  const refreshSystems = useCallback(
    async (signal?: AbortSignal) => {
      setSystemsState("loading");
      try {
        const items = await listSystems({ signal });
        setSystems(items);
        setSystemsState(items.length === 0 ? "empty" : "ready");
        setError("");
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setSystemsState("error");
        setError(formatApiError(err));
      }
    },
    [],
  );

  const refreshRevisions = useCallback(
    async (signal?: AbortSignal) => {
      if (!selectedSystemId) {
        setRevisions([]);
        return;
      }
      try {
        const items = await listRevisions(selectedSystemId, { signal });
        setRevisions(items);
        setError("");
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setError(formatApiError(err));
      }
    },
    [selectedSystemId],
  );

  const refreshRevisionDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!selectedRevisionId) {
        setRevision(null);
        setRuns([]);
        setSelectedRunId("");
        setActiveRun(null);
        setMatrixRows([]);
        setRevisionState("empty");
        return;
      }
      setRevisionState("loading");
      try {
        const detail = await getRevision(selectedRevisionId, { signal });
        setRevision(detail);
        const runItems = await listRuns(selectedRevisionId, { signal });
        setRuns(runItems);
        setRevisionState("ready");
        setError("");
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setRevisionState("error");
        setError(formatApiError(err));
      }
    },
    [selectedRevisionId],
  );

  const refreshRunDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!selectedRunId) {
        setActiveRun(null);
        setMatrixRows([]);
        return;
      }
      try {
        const run = await getRun(selectedRunId, { signal });
        setActiveRun(run);
        if (run.status === "succeeded") {
          const matrix = await listMatrixRows(selectedRunId, { signal });
          setMatrixRows(matrix.items);
        } else {
          setMatrixRows([]);
        }
        setError("");
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setError(formatApiError(err));
      }
    },
    [selectedRunId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshSystems(controller.signal);
    return () => controller.abort();
  }, [refreshSystems]);

  useEffect(() => {
    if (routeSystemId && routeSystemId !== selectedSystemId) {
      setSelectedSystemId(routeSystemId);
    }
    if (routeRevisionId && routeRevisionId !== selectedRevisionId) {
      setSelectedRevisionId(routeRevisionId);
    }
  }, [routeRevisionId, routeSystemId, selectedRevisionId, selectedSystemId]);

  useEffect(() => {
    if (!selectedSystemId && systems.length > 0) {
      const next = routeSystemId || systems[0].system_id;
      setSelectedSystemId(next);
      syncRoute(next, selectedRevisionId);
    }
  }, [
    routeSystemId,
    selectedRevisionId,
    selectedSystemId,
    syncRoute,
    systems,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRevisions(controller.signal);
    return () => controller.abort();
  }, [refreshRevisions]);

  useEffect(() => {
    if (!selectedRevisionId && revisions.length > 0) {
      const next = routeRevisionId || revisions[0].package_revision_id;
      setSelectedRevisionId(next);
      syncRoute(selectedSystemId, next);
    }
  }, [
    revisions,
    routeRevisionId,
    selectedRevisionId,
    selectedSystemId,
    syncRoute,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRevisionDetail(controller.signal);
    return () => controller.abort();
  }, [refreshRevisionDetail]);

  useEffect(() => {
    if (!selectedRunId && runs.length > 0) {
      setSelectedRunId(runs[0].run_id);
    }
  }, [runs, selectedRunId]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRunDetail(controller.signal);
    return () => controller.abort();
  }, [refreshRunDetail]);

  usePolling(() => refreshRevisionDetail(), {
    enabled:
      Boolean(revision) &&
      (revision?.status === "scanning" || revision?.status === "extracting"),
  });

  usePolling(() => refreshRunDetail(), {
    enabled:
      Boolean(activeRun) &&
      (activeRun?.status === "queued" || activeRun?.status === "running"),
  });

  const handleConfirm = async () => {
    if (!confirmState) {
      return;
    }
    setConfirming(true);
    setConfirmError(null);
    try {
      if (confirmState.kind === "cancel-run") {
        await cancelRun(session, confirmState.runId);
        setMessage("Run cancellation accepted.");
        await refreshRunDetail();
        await refreshRevisionDetail();
      }
      if (confirmState.kind === "confirm-revision") {
        await confirmRevision(session, confirmState.revisionId, confirmState.etag);
        setMessage("Revision confirmed and sealed.");
        await refreshRevisionDetail();
      }
      setConfirmState(null);
    } catch (err) {
      setConfirmError(formatApiError(err));
    } finally {
      setConfirming(false);
    }
  };

  if (systemsState === "error" && systems.length === 0) {
    return <PortalLoadFailure message={error || "Could not load systems."} />;
  }

  return (
    <div className="space-y-6">
      {message ? <AlertBanner tone="info">{message}</AlertBanner> : null}
      {error ? <AlertBanner tone="error">{error}</AlertBanner> : null}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Systems</CardTitle>
            <CardDescription>Select or create a system for package revisions.</CardDescription>
          </div>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              void createSystem(session, `System ${systems.length + 1}`)
                .then(() => refreshSystems())
                .then(() => setMessage("System created."))
                .catch((err) => setError(formatApiError(err)));
            }}
          >
            <Plus />
            Create system
          </Button>
        </CardHeader>
        <CardContent>
          {systemsState === "loading" ? <SystemsListSkeleton /> : null}
          {systemsState === "empty" ? (
            <EmptyState
              {...resolveSystemsEmptyState()}
              action={{
                label: "Create system",
                onClick: () => {
                  void createSystem(session, "System 1")
                    .then(() => refreshSystems())
                    .then(() => setMessage("System created."))
                    .catch((err) => setError(formatApiError(err)));
                },
              }}
            />
          ) : null}
          {systemsState === "ready" ? (
            <SelectionList
              items={systems.map((item) => ({
                id: item.system_id,
                label: item.display_name,
              }))}
              selectedId={selectedSystemId}
              onSelect={(id) => {
                setSelectedSystemId(id);
                setSelectedRevisionId("");
                syncRoute(id, "");
              }}
            />
          ) : null}
        </CardContent>
      </Card>

      {selectedSystemId ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0">
            <CardTitle>Package revisions</CardTitle>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                void createRevision(session, selectedSystemId)
                  .then((created) => {
                    setSelectedRevisionId(created.package_revision_id);
                    syncRoute(selectedSystemId, created.package_revision_id);
                    return refreshRevisions();
                  })
                  .then(() => setMessage("Revision created."))
                  .catch((err) => setError(formatApiError(err)));
              }}
            >
              <Plus />
              Create revision
            </Button>
          </CardHeader>
          <CardContent>
            {revisions.length === 0 ? (
              <EmptyState {...resolveRevisionsEmptyState()} />
            ) : (
              <SelectionList
                items={revisions.map((item) => ({
                  id: item.package_revision_id,
                  label: `${item.package_revision_id.slice(0, 8)}…`,
                  status: item.status,
                }))}
                selectedId={selectedRevisionId}
                onSelect={(id) => {
                  setSelectedRevisionId(id);
                  syncRoute(selectedSystemId, id);
                }}
                renderLabel={(item) => `${item.label} — ${item.status ?? ""}`}
              />
            )}
          </CardContent>
        </Card>
      ) : null}

      {revision ? (
        <Card>
          <CardHeader>
            <CardTitle>Revision workflow</CardTitle>
            <CardDescription className="flex flex-wrap items-center gap-2">
              <span>Status</span>
              <Badge variant={revisionStatusVariant(revision.status)}>
                {revision.status}
              </Badge>
              <span>· version {revision.revision_version}</span>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {revisionState === "loading" ? <RevisionWorkflowSkeleton /> : null}

            {revision.status === "uploading" ? (
              <PackageUploadPanel
                session={session}
                revisionId={revision.package_revision_id}
                onUploaded={() => void refreshRevisionDetail()}
                onFinalized={() => {
                  setMessage("Finalize accepted; intake worker will scan and extract.");
                  void refreshRevisionDetail();
                }}
                finalizing={finalizing}
                onFinalize={async () => {
                  setFinalizing(true);
                  try {
                    await finalizeRevision(session, revision.package_revision_id);
                  } finally {
                    setFinalizing(false);
                  }
                }}
              />
            ) : null}

            {revision.status === "scanning" || revision.status === "extracting" ? (
              <IntakeProgressPanel status={revision.status} />
            ) : null}

            {revision.status === "awaiting_confirmation" ? (
              <div className="space-y-4">
                {packageDraft.loadState === "loading" ? (
                  <RevisionWorkflowSkeleton />
                ) : null}
                {packageDraft.loadState === "error" ? (
                  <AlertBanner tone="error">
                    {packageDraft.loadError || "Could not load package draft."}
                  </AlertBanner>
                ) : null}
                {packageDraft.loadState === "empty" ? (
                  <AlertBanner tone="warning">
                    No package draft is available yet. Wait for extraction to finish or
                    reload this page.
                  </AlertBanner>
                ) : null}
                {packageDraft.loadState === "ready" &&
                packageDraft.document &&
                packageDraft.draft ? (
                  <PackageEditor
                    draft={packageDraft.draft}
                    document={packageDraft.document}
                    isDirty={packageDraft.isDirty}
                    saving={packageDraft.saving}
                    saveError={packageDraft.saveError}
                    staleConflict={packageDraft.staleConflict}
                    onDocumentChange={packageDraft.updateDocument}
                    onSave={() => void packageDraft.saveDraft()}
                    onReload={() => void packageDraft.reload()}
                    onConfirm={() =>
                      setConfirmState({
                        kind: "confirm-revision",
                        revisionId: revision.package_revision_id,
                        etag: packageDraft.etag,
                      })
                    }
                  />
                ) : null}
              </div>
            ) : null}

            {revision.status === "ready" ? (
              <div className="space-y-6">
                <AlertBanner tone="warning">
                  Draft analysis readiness - not official status in GRC, FedRAMP, or an
                  agency authorization process.
                </AlertBanner>

                <div className="flex flex-row items-center justify-between gap-4">
                  <h3 className="text-base font-semibold">Analysis runs</h3>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => {
                      void startRun(session, revision.package_revision_id)
                        .then((created) => {
                          setSelectedRunId(created.run_id);
                          return refreshRevisionDetail();
                        })
                        .then(() => refreshRunDetail())
                        .then(() => setMessage("Deterministic analysis run started."))
                        .catch((err) => setError(formatApiError(err)));
                    }}
                  >
                    Start deterministic run
                  </Button>
                </div>

                {runs.length === 0 ? (
                  <EmptyState {...resolveRunsEmptyState()} />
                ) : (
                  <SelectionList
                    items={runs.map((item) => ({
                      id: item.run_id,
                      label: `${item.run_id.slice(0, 8)}…`,
                      status: item.status,
                    }))}
                    selectedId={selectedRunId}
                    onSelect={setSelectedRunId}
                    renderLabel={(item) => `${item.label} — ${item.status ?? ""}`}
                  />
                )}

                {activeRun ? (
                  <Card className="bg-muted/20">
                    <CardHeader>
                      <CardTitle className="text-base">Run status</CardTitle>
                      <CardDescription className="flex flex-wrap items-center gap-2">
                        <Badge variant={runStatusVariant(activeRun.status)}>
                          {activeRun.status}
                        </Badge>
                        <span>· LLM calls: {activeRun.llm_call_count}</span>
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {activeRun.status === "queued" || activeRun.status === "running" ? (
                        <>
                          <p className="text-sm text-muted-foreground">
                            Deterministic analyzer worker is processing this run…
                          </p>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setConfirmState({
                                kind: "cancel-run",
                                runId: activeRun.run_id,
                              })
                            }
                          >
                            Cancel run
                          </Button>
                        </>
                      ) : null}

                      {activeRun.status === "succeeded" ? (
                        matrixRows.length > 0 ? (
                          <>
                            <p className="text-sm text-muted-foreground">
                              Artifact manifest:{" "}
                              {activeRun.artifact_manifest_sha256?.slice(0, 16)}…
                            </p>
                            <Separator />
                            <h4 className="text-sm font-semibold">Matrix</h4>
                            <div className="overflow-x-auto rounded-md border">
                              <table className="w-full border-collapse text-sm">
                                <thead>
                                  <tr className="border-b bg-muted/50">
                                    <th className="px-4 py-2 text-left font-medium">Item</th>
                                    <th className="px-4 py-2 text-left font-medium">Status</th>
                                    <th className="px-4 py-2 text-left font-medium">Summary</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {matrixRows.map((row) => (
                                    <tr
                                      key={row.matrix_row_id}
                                      className="border-b border-border/60 last:border-0"
                                    >
                                      <td className="px-4 py-3 align-top font-mono text-xs">
                                        {row.assessment_item_id}
                                      </td>
                                      <td className="px-4 py-3 align-top">
                                        <Badge variant="muted">
                                          {row.model_proposed_status}
                                        </Badge>
                                      </td>
                                      <td className="px-4 py-3 align-top text-muted-foreground">
                                        {row.finding_summary}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </>
                        ) : (
                          <MatrixTableSkeleton />
                        )
                      ) : null}
                    </CardContent>
                  </Card>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <ConfirmDialog
        open={confirmState !== null}
        title={
          confirmState?.kind === "cancel-run"
            ? "Cancel analysis run"
            : confirmState?.kind === "confirm-revision"
              ? "Confirm package"
              : "Confirm action"
        }
        description={
          confirmState?.kind === "cancel-run"
            ? "Cancel the in-flight deterministic analysis run?"
            : confirmState?.kind === "confirm-revision"
              ? "Seal the displayed package draft as an immutable ready revision?"
              : ""
        }
        confirmLabel={
          confirmState?.kind === "cancel-run"
            ? "Cancel run"
            : "Confirm package"
        }
        confirming={confirming}
        error={confirmError}
        onCancel={() => {
          if (!confirming) {
            setConfirmState(null);
            setConfirmError(null);
          }
        }}
        onConfirm={() => void handleConfirm()}
      />
    </div>
  );
}

export function WorkflowRoute({ session }: WorkflowPageProps) {
  return <WorkflowPage session={session} />;
}

export function WorkflowIndexRedirect() {
  return <Navigate replace to="/workflow" />;
}

export function LoginPage({
  error,
  onSignIn,
}: {
  error: string;
  onSignIn: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle className="text-2xl">ATO Evidence Analysis Portal</CardTitle>
          <CardDescription>
            Sign in with OIDC to manage systems, uploads, and package drafts.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? <AlertBanner tone="error">{error}</AlertBanner> : null}
          <Button type="button" onClick={onSignIn}>
            Sign in
          </Button>
          <p className="text-sm text-muted-foreground">
            Need an account? Contact your operator for OIDC access.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export function SignedOutNotice() {
  return (
    <div className="text-xs text-muted-foreground">
      <Link className="underline underline-offset-4" to="/login">
        Return to sign in
      </Link>
    </div>
  );
}
