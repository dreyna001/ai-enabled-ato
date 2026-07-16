import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  ApiError,
  cancelRun,
  confirmRevision,
  createRevision,
  createSystem,
  finalizeRevision,
  getRevision,
  getRun,
  isCancelledRequest,
  listRevisions,
  listRuns,
  listSystems,
  startRun,
} from "@/api/client";
import { ChangeAnalysisPanel } from "@/components/ChangeAnalysisPanel";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DependencyCapabilityPanel,
  isAssistantEnabled,
} from "@/components/DependencyCapabilityPanel";
import { EmptyState } from "@/components/EmptyState";
import { IntakeProgressPanel } from "@/components/IntakeProgressPanel";
import { MatrixResultsPanel } from "@/components/MatrixResultsPanel";
import { PackageAssistantPanel } from "@/components/PackageAssistantPanel";
import { PreflightPanel } from "@/components/PreflightPanel";
import { ReviewExportWorkbench } from "@/components/ReviewExportWorkbench";
import { RevisionCreateForm } from "@/components/RevisionCreateForm";
import { RunArtifactsPanel } from "@/components/RunArtifactsPanel";
import { TerminalIntakePanel } from "@/components/TerminalIntakePanel";
import {
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
import { usePolling } from "@/hooks/usePolling";
import { usePackageDraft } from "@/hooks/usePackageDraft";
import { cn } from "@/lib/utils";
import type {
  AnalysisRun,
  CreateRevisionInput,
  PackageRevision,
  PreflightResult,
  SessionInfo,
  System,
} from "@/types";
import {
  resolveRevisionsEmptyState,
  resolveRunsEmptyState,
  resolveSystemsEmptyState,
} from "@/utils/emptyStates";
import { formatApiError } from "@/utils/formatApiError";
import { formatProblemError } from "@/utils/formatProblemError";
import { formatRunListLabel } from "@/utils/runLabels";
import {
  revisionStatusLabel,
  revisionStatusVariant,
  runFailureMessage,
  runStatusLabel,
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
  readinessLoaded?: boolean;
  readinessDegraded?: boolean;
  readinessError?: string | null;
};

export function WorkflowPage({
  session,
  readinessLoaded = true,
  readinessDegraded = false,
  readinessError = null,
}: WorkflowPageProps) {
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
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [targetedItemIds, setTargetedItemIds] = useState<string[]>([]);
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
    revisionImpactLevel: revision?.impact_level ?? null,
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
        setPreflight(null);
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
        return;
      }
      try {
        const run = await getRun(selectedRunId, { signal });
        setActiveRun(run);
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
      if (
        confirmState.kind === "confirm-revision" &&
        err instanceof ApiError &&
        (err.status === 412 || err.errorCode === "etag_mismatch")
      ) {
        setConfirmError(
          "This draft changed on the server. Reload the latest version before confirming again.",
        );
        void packageDraft.reload();
      } else {
        setConfirmError(formatProblemError(err));
      }
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
            <CardDescription>Select or create a system for Package Revisions.</CardDescription>
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
            Create System
          </Button>
        </CardHeader>
        <CardContent>
          {systemsState === "loading" ? <SystemsListSkeleton /> : null}
          {systemsState === "empty" ? (
            <EmptyState
              {...resolveSystemsEmptyState()}
              action={{
                label: "Create System",
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
            <CardTitle>Package Revisions</CardTitle>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                void createRevision(session, selectedSystemId, {
                  parent_revision_id: null,
                  profile_id: "fisma_agency_security",
                  certification_class: null,
                  impact_level: "moderate",
                  data_origin: "synthetic",
                  sensitivity: "internal_unclassified",
                })
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
              Create Revision
            </Button>
          </CardHeader>
          <CardContent>
            <RevisionCreateForm
              revisions={revisions}
              onCreate={(input: CreateRevisionInput) => {
                void createRevision(session, selectedSystemId, input)
                  .then((created) => {
                    setSelectedRevisionId(created.package_revision_id);
                    syncRoute(selectedSystemId, created.package_revision_id);
                    return refreshRevisions();
                  })
                  .then(() => setMessage("Revision created."))
                  .catch((err) => setError(formatApiError(err)));
              }}
            />
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
                renderLabel={(item) =>
                  `${item.label} — ${revisionStatusLabel(item.status ?? "")}`
                }
              />
            )}
          </CardContent>
        </Card>
      ) : null}

      {revision ? (
        <Card>
          <CardHeader>
            <CardTitle>Revision Workflow</CardTitle>
            <CardDescription className="flex flex-wrap items-center gap-2">
              <span>Status</span>
              <Badge variant={revisionStatusVariant(revision.status)}>
                {revisionStatusLabel(revision.status)}
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

            {revision.status === "invalid" ||
            revision.status === "quarantined" ||
            revision.status === "archived" ? (
              <TerminalIntakePanel
                status={revision.status}
                reconciliationMessage={
                  readinessError?.includes("reconciliation")
                    ? readinessError
                    : null
                }
              />
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
                    validationIssues={packageDraft.validationIssues}
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
                <DependencyCapabilityPanel
                  readiness={{
                    loaded: readinessLoaded,
                    ready: !readinessDegraded && !readinessError,
                    degraded: readinessDegraded,
                    error: readinessError,
                    checks: [],
                  }}
                  revisionReady
                />
                <ChangeAnalysisPanel
                  revisionId={revision.package_revision_id}
                  parentRevisionId={revision.parent_revision_id}
                  onTargetedIds={setTargetedItemIds}
                />
                <PreflightPanel
                  revisionId={revision.package_revision_id}
                  onPreflightChange={setPreflight}
                />
                <PackageAssistantPanel
                  session={session}
                  revisionId={revision.package_revision_id}
                  enabled={isAssistantEnabled(
                    {
                      loaded: readinessLoaded,
                      ready: !readinessDegraded && !readinessError,
                      degraded: readinessDegraded,
                      error: readinessError,
                      checks: [],
                    },
                    true,
                  )}
                />
                <AlertBanner tone="warning">
                  Draft analysis readiness - not official status in GRC, FedRAMP, or an
                  agency authorization process.
                </AlertBanner>

                <div className="flex flex-row flex-wrap items-center justify-between gap-4">
                  <h3 className="text-base font-semibold">Analysis Runs</h3>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      disabled={preflight !== null && !preflight.analysis_eligible}
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
                      Start Deterministic Run
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={
                        (preflight !== null && !preflight.analysis_eligible) ||
                        targetedItemIds.length === 0
                      }
                      onClick={() => {
                        void startRun(session, revision.package_revision_id, {
                          runType: "targeted",
                          assessmentItemIds: targetedItemIds,
                        })
                          .then((created) => {
                            setSelectedRunId(created.run_id);
                            return refreshRevisionDetail();
                          })
                          .then(() => refreshRunDetail())
                          .then(() =>
                            setMessage(
                              `Targeted run started for ${targetedItemIds.length} item(s).`,
                            ),
                          )
                          .catch((err) => setError(formatApiError(err)));
                      }}
                    >
                      Start Targeted Run
                    </Button>
                  </div>
                </div>
                {preflight && !preflight.analysis_eligible ? (
                  <p className="text-sm text-muted-foreground">
                    Resolve preflight analysis blockers before starting runs.
                  </p>
                ) : null}

                {runs.length === 0 ? (
                  <EmptyState {...resolveRunsEmptyState()} />
                ) : (
                  <SelectionList
                    items={runs.map((item) => ({
                      id: item.run_id,
                      label: formatRunListLabel(item),
                      status: item.status,
                    }))}
                    selectedId={selectedRunId}
                    onSelect={setSelectedRunId}
                    renderLabel={(item) =>
                      `${item.label} — ${runStatusLabel(item.status ?? "")}`
                    }
                  />
                )}

                {activeRun ? (
                  <Card className="bg-muted/20">
                    <CardHeader>
                      <CardTitle className="text-base">Run Status</CardTitle>
                      <CardDescription className="flex flex-wrap items-center gap-2">
                        <Badge variant={runStatusVariant(activeRun.status)}>
                          {runStatusLabel(activeRun.status)}
                        </Badge>
                        <span>· LLM calls: {activeRun.llm_call_count}</span>
                        <span className="font-mono text-xs text-muted-foreground">
                          · {activeRun.run_id.slice(0, 8)}…
                        </span>
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
                            Cancel Run
                          </Button>
                        </>
                      ) : null}

                      {activeRun.status === "failed" ||
                      activeRun.status === "cancelled" ||
                      activeRun.status === "policy_blocked" ? (
                        <p className="text-sm text-destructive">
                          {runFailureMessage(activeRun.status, activeRun.error_code)}
                        </p>
                      ) : null}

                      {activeRun.status === "succeeded" ? (
                        <>
                          <RunArtifactsPanel run={activeRun} />
                          <MatrixResultsPanel run={activeRun} />
                          <ReviewExportWorkbench
                            session={session}
                            runId={activeRun.run_id}
                            matrixRows={[]}
                          />
                        </>
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
            ? "Cancel Analysis Run"
            : confirmState?.kind === "confirm-revision"
              ? "Confirm Package"
              : "Confirm Action"
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
            ? "Cancel Run"
            : "Confirm Package"
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

export function WorkflowRoute({
  session,
  readiness,
}: {
  session: SessionInfo;
  readiness?: {
    loaded: boolean;
    degraded: boolean;
    error: string | null;
  };
}) {
  return (
    <WorkflowPage
      session={session}
      readinessLoaded={readiness?.loaded ?? true}
      readinessDegraded={readiness?.degraded ?? false}
      readinessError={readiness?.error ?? null}
    />
  );
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
