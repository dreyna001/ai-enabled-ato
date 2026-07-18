import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { Archive, Plus } from "lucide-react";
import {
  ApiError,
  archiveSystem,
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
import { isAssistantEnabled, assistantReadinessWarning } from "@/components/DependencyCapabilityPanel";
import { DraftConfirmReadinessPanel } from "@/components/DraftConfirmReadinessPanel";
import { EmptyState } from "@/components/EmptyState";
import { IntakeProgressPanel } from "@/components/IntakeProgressPanel";
import { MatrixResultsPanel } from "@/components/MatrixResultsPanel";
import { PackageAssistantPanel } from "@/components/PackageAssistantPanel";
import { PreflightPanel } from "@/components/PreflightPanel";
import { ReviewExportWorkbench } from "@/components/ReviewExportWorkbench";
import { RevisionCreateForm } from "@/components/RevisionCreateForm";
import {
  IntakeReadinessPanel,
  type IntakeReportLike,
} from "@/components/IntakeReadinessPanel";
import type {
  IntakeConflictManualEdit,
  IntakeConflictResolution,
} from "@/components/IntakeConflictList";
import { RevisionMetadataPanel } from "@/components/RevisionMetadataPanel";
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
  IntakeReport,
  PackageDraftDocument,
  PackageRevision,
  PreflightResult,
  DraftExportReadiness,
  RevisionMetadataSaveState,
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
  packagePreparationStatusLabel,
  revisionStatusLabel,
  revisionStatusVariant,
  runFailureMessage,
  runStatusLabel,
  runStatusVariant,
} from "@/utils/statusLabels";
import { shouldRevealRevisionMetadata } from "@/utils/revisionMetadata";
import {
  applyConflictCandidateSelection,
  isMetadataOnlyConflictField,
  pruneResolvedConflictsAfterEdit,
} from "@/utils/draftConflictResolution";
import { isEditableDraftPointer } from "@/utils/draftEditorFocus";
import { validateDraftJsonPointer } from "@/utils/jsonPointer";

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
  | {
      kind: "archive-system";
      systemId: string;
      displayName: string;
    }
  | null;

function isSystemArchived(system: System): boolean {
  return system.archived_at != null;
}

function visibleSystems(systems: System[], showArchived: boolean): System[] {
  return showArchived ? systems : systems.filter((system) => !isSystemArchived(system));
}

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
        "rounded-sm border px-4 py-3 text-sm",
        tone === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
        tone === "info" && "border-primary/20 bg-primary/5 text-foreground",
        tone === "warning" && "border-amber-400/30 bg-amber-400/10 text-amber-400",
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
  renderLabel?: (item: { id: string; label: string; status?: string }) => ReactNode;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <Button
          key={item.id}
          type="button"
          size="sm"
          variant="outline"
          className={cn(
            item.id === selectedId &&
              "border-link bg-muted text-foreground hover:bg-muted",
          )}
          onClick={() => onSelect(item.id)}
        >
          {renderLabel ? renderLabel(item) : item.label}
        </Button>
      ))}
    </div>
  );
}

function Phase4IntakePanels({
  intakeReport,
  reportLoading,
  reportError,
  onRefresh,
  conflictControlsDisabled,
  conflictStatusMessage,
  conflictActionError,
  onSelectConflictCandidate,
  onManualConflictEdit,
}: {
  intakeReport: IntakeReport | null;
  reportLoading: boolean;
  reportError: string;
  onRefresh: () => void;
  conflictControlsDisabled: boolean;
  conflictStatusMessage: string;
  conflictActionError: string;
  onSelectConflictCandidate?: (resolution: IntakeConflictResolution) => void;
  onManualConflictEdit?: (resolution: IntakeConflictManualEdit) => void;
}) {
  return (
    <div className="space-y-3">
      {conflictStatusMessage ? (
        <p className="text-sm text-foreground" role="status">
          {conflictStatusMessage}
        </p>
      ) : null}
      {conflictActionError ? (
        <p className="text-sm text-destructive" role="alert">
          {conflictActionError}
        </p>
      ) : null}
      <IntakeReadinessPanel
        report={intakeReport as IntakeReportLike | null}
        loading={reportLoading}
        error={reportError || null}
        onRetry={onRefresh}
        conflictControlsDisabled={conflictControlsDisabled}
        onManualConflictEdit={onManualConflictEdit}
        onSelectConflictCandidate={onSelectConflictCandidate}
      />
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
  const [draftExportReadiness, setDraftExportReadiness] = useState<DraftExportReadiness | null>(
    null,
  );
  const [targetedItemIds, setTargetedItemIds] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [systemsState, setSystemsState] = useState<LoadState>("loading");
  const [showArchived, setShowArchived] = useState(false);
  const [revisionState, setRevisionState] = useState<LoadState>("empty");
  const [confirmState, setConfirmState] = useState<ConfirmState>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const [metadataSaveState, setMetadataSaveState] = useState<RevisionMetadataSaveState>({
    isDirty: false,
    saving: false,
    staleConflict: false,
    isComplete: false,
  });
  const [intakeReport, setIntakeReport] = useState<IntakeReport | null>(null);
  const [intakeReportLoadState, setIntakeReportLoadState] = useState({
    loading: false,
    error: "",
  });
  const [intakeReportRefreshKey, setIntakeReportRefreshKey] = useState(0);
  const [conflictStatusMessage, setConflictStatusMessage] = useState("");
  const [conflictActionError, setConflictActionError] = useState("");
  const [editorFocusPointer, setEditorFocusPointer] = useState<string | null>(null);
  const [editorFocusNonce, setEditorFocusNonce] = useState(0);

  const draftEnabled =
    Boolean(revision) && revision?.status === "awaiting_confirmation";

  const draftExportBlocked =
    draftExportReadiness !== null && !draftExportReadiness.export_eligible;

  const metadataBlocked =
    metadataSaveState.isDirty ||
    metadataSaveState.saving ||
    metadataSaveState.staleConflict ||
    !metadataSaveState.isComplete;

  const intakeReportIsCurrent =
    intakeReport?.package_revision_id === revision?.package_revision_id;
  const intakeBlocked =
    revision?.status === "awaiting_confirmation" &&
    (intakeReportLoadState.loading ||
      Boolean(intakeReportLoadState.error) ||
      !intakeReportIsCurrent ||
      intakeReport?.confirmation.allowed !== true);
  const confirmationBlockers = Array.from(
    new Set([
      ...(metadataSaveState.isDirty ? ["unsaved_metadata"] : []),
      ...(metadataSaveState.saving ? ["metadata_saving"] : []),
      ...(metadataSaveState.staleConflict ? ["metadata_stale"] : []),
      ...(!metadataSaveState.isComplete ? ["metadata_incomplete"] : []),
      ...(intakeReportLoadState.loading ? ["intake_report_loading"] : []),
      ...(intakeReportLoadState.error ? ["intake_report_error"] : []),
      ...(!intakeReportIsCurrent && !intakeReportLoadState.loading
        ? ["intake_report_unavailable"]
        : []),
      ...(intakeReportIsCurrent && intakeReport && !intakeReport.confirmation.allowed
        ? intakeReport.confirmation.blockers.length > 0
          ? intakeReport.confirmation.blockers
          : ["intake_confirmation_not_allowed"]
        : []),
    ]),
  );

  const readinessState = {
    loaded: readinessLoaded,
    ready: !readinessDegraded && !readinessError,
    degraded: readinessDegraded,
    error: readinessError,
    checks: [],
  };

  const packageDraft = usePackageDraft(session, selectedRevisionId, {
    enabled: draftEnabled,
    revisionImpactLevel: revision?.impact_level ?? null,
    onSaved: () => {
      setMessage("Draft saved.");
      setIntakeReportRefreshKey((value) => value + 1);
    },
  });

  const conflictControlsDisabled =
    revision?.status !== "awaiting_confirmation" ||
    packageDraft.loadState !== "ready" ||
    !packageDraft.document ||
    packageDraft.saving ||
    packageDraft.staleConflict;

  const handleDraftDocumentChange = useCallback(
    (nextDocument: PackageDraftDocument) => {
      const previousDocument = packageDraft.document;
      if (!previousDocument) {
        packageDraft.updateDocument(nextDocument);
        return;
      }
      const conflicts = intakeReport?.conflicts ?? [];
      const prunedDocument = pruneResolvedConflictsAfterEdit(
        previousDocument,
        nextDocument,
        conflicts,
      );
      packageDraft.updateDocument(prunedDocument);
    },
    [intakeReport?.conflicts, packageDraft.document, packageDraft.updateDocument],
  );

  const handleSelectConflictCandidate = useCallback(
    (resolution: IntakeConflictResolution) => {
      setConflictActionError("");
      setConflictStatusMessage("");
      if (!packageDraft.document) {
        setConflictActionError("Load the package draft before resolving conflicts.");
        return;
      }
      if (isMetadataOnlyConflictField(resolution.field)) {
        setConflictActionError(
          "This conflict targets revision metadata. Edit it in the metadata panel above.",
        );
        return;
      }
      const pointerError = validateDraftJsonPointer(resolution.field);
      if (pointerError) {
        setConflictActionError(pointerError.message);
        return;
      }
      const result = applyConflictCandidateSelection(
        packageDraft.document,
        intakeReport?.conflicts ?? [],
        resolution.field,
        resolution.candidateIndex,
      );
      if (!result.ok) {
        setConflictActionError(result.error);
        return;
      }
      packageDraft.updateDocument(result.document);
      setConflictStatusMessage(
        "Applied the selected value to the draft. Save draft to refresh intake readiness.",
      );
    },
    [intakeReport?.conflicts, packageDraft.document, packageDraft.updateDocument],
  );

  const handleManualConflictEdit = useCallback(
    (resolution: IntakeConflictManualEdit) => {
      setConflictActionError("");
      setConflictStatusMessage("");
      if (isMetadataOnlyConflictField(resolution.field)) {
        setConflictActionError(
          "This conflict targets revision metadata. Edit it in the metadata panel above.",
        );
        return;
      }
      const pointerError = validateDraftJsonPointer(resolution.field);
      if (pointerError) {
        setConflictActionError(pointerError.message);
        return;
      }
      if (!isEditableDraftPointer(resolution.field)) {
        setConflictActionError(
          "This field is not editable in the package editor. Resolve it another way or leave the conflict for review.",
        );
        return;
      }
      setEditorFocusPointer(resolution.field);
      setEditorFocusNonce((value) => value + 1);
      setConflictStatusMessage(
        "Edit the highlighted field in the package editor below. The conflict stays open until the value changes.",
      );
    },
    [],
  );

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
        const items = await listSystems({ signal, includeArchived: showArchived });
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
    [showArchived],
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
    async (
      signal?: AbortSignal,
      options?: {
        /** Keep current workflow content visible while polling intake/run state. */
        silent?: boolean;
      },
    ) => {
      if (!selectedRevisionId) {
        setRevision(null);
        setRuns([]);
        setSelectedRunId("");
        setActiveRun(null);
        setPreflight(null);
        setRevisionState("empty");
        return;
      }
      if (!options?.silent) {
        setRevisionState("loading");
      }
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
      const available = visibleSystems(systems, showArchived);
      if (available.length === 0) {
        return;
      }
      const next =
        routeSystemId && available.some((system) => system.system_id === routeSystemId)
          ? routeSystemId
          : available[0].system_id;
      setSelectedSystemId(next);
      syncRoute(next, selectedRevisionId);
    }
  }, [
    routeSystemId,
    selectedRevisionId,
    selectedSystemId,
    showArchived,
    syncRoute,
    systems,
  ]);

  useEffect(() => {
    if (systemsState !== "ready") {
      return;
    }
    const available = visibleSystems(systems, showArchived);
    if (available.length === 0) {
      if (selectedSystemId) {
        setSelectedSystemId("");
        setSelectedRevisionId("");
        syncRoute("", "");
      }
      return;
    }
    const selectedVisible = available.some((system) => system.system_id === selectedSystemId);
    if (selectedVisible) {
      return;
    }
    const next =
      routeSystemId && available.some((system) => system.system_id === routeSystemId)
        ? routeSystemId
        : available[0].system_id;
    setSelectedSystemId(next);
    setSelectedRevisionId("");
    syncRoute(next, "");
  }, [
    routeSystemId,
    selectedSystemId,
    showArchived,
    syncRoute,
    systems,
    systemsState,
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

  useEffect(() => {
    setMetadataSaveState({
      isDirty: false,
      saving: false,
      staleConflict: false,
      isComplete: false,
    });
    setIntakeReport(null);
    setIntakeReportLoadState({ loading: false, error: "" });
    setIntakeReportRefreshKey(0);
  }, [selectedRevisionId]);

  usePolling(() => refreshRevisionDetail(undefined, { silent: true }), {
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
      if (confirmState.kind === "archive-system") {
        await archiveSystem(session, confirmState.systemId);
        setMessage(`System "${confirmState.displayName}" archived.`);
        await refreshSystems();
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

  const displayedSystems = visibleSystems(systems, showArchived);
  const selectedSystem = systems.find((system) => system.system_id === selectedSystemId);
  const canArchiveSelectedSystem =
    Boolean(selectedSystem) && !isSystemArchived(selectedSystem!);
  const showRevisionMetadata = Boolean(revision && shouldRevealRevisionMetadata(revision));

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
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              size="sm"
              variant={showArchived ? "default" : "outline"}
              aria-pressed={showArchived}
              onClick={() => setShowArchived((value) => !value)}
            >
              Show archived
            </Button>
            {canArchiveSelectedSystem ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() =>
                  setConfirmState({
                    kind: "archive-system",
                    systemId: selectedSystem!.system_id,
                    displayName: selectedSystem!.display_name,
                  })
                }
              >
                <Archive />
                Archive System
              </Button>
            ) : null}
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
          </div>
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
            displayedSystems.length === 0 ? (
              <EmptyState
                title="No active systems"
                description={
                  showArchived
                    ? "No systems match the current view."
                    : "All systems are archived. Turn on Show archived to view them."
                }
              />
            ) : (
              <SelectionList
                items={displayedSystems.map((item) => ({
                  id: item.system_id,
                  label: item.display_name,
                  status: isSystemArchived(item) ? "archived" : undefined,
                }))}
                selectedId={selectedSystemId}
                onSelect={(id) => {
                  setSelectedSystemId(id);
                  setSelectedRevisionId("");
                  syncRoute(id, "");
                }}
                renderLabel={(item) => (
                  <>
                    {item.label}
                    {item.status === "archived" ? (
                      <>
                        {" "}
                        <Badge variant="muted">Archived</Badge>
                      </>
                    ) : null}
                  </>
                )}
              />
            )
          ) : null}
        </CardContent>
      </Card>

      {selectedSystemId ? (
        <Card>
          <CardHeader>
            <CardTitle>Package Revisions</CardTitle>
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
                renderLabel={(item) => (
                  <>
                    <span className="font-mono">{item.label}</span>
                    {" — "}
                    {revisionStatusLabel(item.status ?? "")}
                  </>
                )}
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
              <span>Technical</span>
              <Badge variant={revisionStatusVariant(revision.status)}>
                {revisionStatusLabel(revision.status)}
              </Badge>
              <span>Preparation</span>
              <Badge variant="muted">
                {packagePreparationStatusLabel(
                  revision.package_preparation_status,
                )}
              </Badge>
              <span className="font-mono">· version {revision.revision_version}</span>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {revisionState === "loading" &&
            revision?.package_revision_id !== selectedRevisionId ? (
              <RevisionWorkflowSkeleton />
            ) : null}

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

            {showRevisionMetadata ? (
              <>
                <RevisionMetadataPanel
                  session={session}
                  revision={revision}
                  refreshKey={`${revision.revision_version}:${intakeReportRefreshKey}`}
                  onRevisionUpdated={(updated) => {
                    setRevision(updated);
                    setIntakeReportRefreshKey((value) => value + 1);
                    if (updated.status === "awaiting_confirmation") {
                      void packageDraft.reload();
                    }
                  }}
                  onMetadataStateChange={setMetadataSaveState}
                  onIntakeReportChange={setIntakeReport}
                  onReportLoadStateChange={setIntakeReportLoadState}
                />
                <Phase4IntakePanels
                  intakeReport={intakeReport}
                  reportLoading={intakeReportLoadState.loading}
                  reportError={intakeReportLoadState.error}
                  onRefresh={() => setIntakeReportRefreshKey((value) => value + 1)}
                  conflictControlsDisabled={conflictControlsDisabled}
                  conflictStatusMessage={conflictStatusMessage}
                  conflictActionError={conflictActionError}
                  onManualConflictEdit={handleManualConflictEdit}
                  onSelectConflictCandidate={handleSelectConflictCandidate}
                />
              </>
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
                {packageDraft.loadState === "loading" && !packageDraft.document ? (
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
                  <div className="space-y-4">
                    <DraftConfirmReadinessPanel
                      revisionId={revision.package_revision_id}
                      refreshKey={`${packageDraft.etag}:${packageDraft.isDirty ? "dirty" : "saved"}`}
                      onReadinessChange={setDraftExportReadiness}
                    />
                    <PackageEditor
                      draft={packageDraft.draft}
                      document={packageDraft.document}
                      isDirty={packageDraft.isDirty}
                      saving={packageDraft.saving}
                      saveError={packageDraft.saveError}
                      staleConflict={packageDraft.staleConflict}
                      validationIssues={packageDraft.validationIssues}
                      exportBlocked={draftExportBlocked}
                      exportBlockers={draftExportReadiness?.export_blockers ?? []}
                      confirmationBlocked={metadataBlocked || intakeBlocked}
                      confirmationBlockers={confirmationBlockers}
                      onDocumentChange={handleDraftDocumentChange}
                      onSave={() => void packageDraft.saveDraft()}
                      onReload={() => void packageDraft.reload()}
                      onConfirm={() =>
                        setConfirmState({
                          kind: "confirm-revision",
                          revisionId: revision.package_revision_id,
                          etag: packageDraft.etag,
                        })
                      }
                      focusRequestPointer={editorFocusPointer}
                      focusRequestNonce={editorFocusNonce}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            {revision.status === "ready" ? (
              <div className="space-y-6">
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
                  runId={
                    activeRun?.status === "succeeded" ? activeRun.run_id : null
                  }
                  enabled={isAssistantEnabled(readinessState, true)}
                  readinessWarning={assistantReadinessWarning(readinessState)}
                />
                <AlertBanner tone="warning">
                  Analysis readiness only — not an official GRC, FedRAMP, or agency
                  authorization decision.
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
                      <CardDescription>
                        <Badge variant={runStatusVariant(activeRun.status)}>
                          {runStatusLabel(activeRun.status)}
                        </Badge>
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
                          <MatrixResultsPanel run={activeRun} />
                          <ReviewExportWorkbench
                            session={session}
                            runId={activeRun.run_id}
                            matrixRows={[]}
                            preflight={preflight}
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
              : confirmState?.kind === "archive-system"
                ? "Archive System"
                : "Confirm Action"
        }
        description={
          confirmState?.kind === "cancel-run"
            ? "Cancel the in-flight deterministic analysis run?"
            : confirmState?.kind === "confirm-revision"
              ? "Seal the displayed package draft as an immutable ready revision?"
              : confirmState?.kind === "archive-system"
                ? `Archive "${confirmState.displayName}"? It will be hidden from the default system list. You can show archived systems again with Show archived.`
                : ""
        }
        confirmLabel={
          confirmState?.kind === "cancel-run"
            ? "Cancel Run"
            : confirmState?.kind === "archive-system"
              ? "Archive System"
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
      <Link className="text-link underline underline-offset-4" to="/login">
        Return to sign in
      </Link>
    </div>
  );
}
