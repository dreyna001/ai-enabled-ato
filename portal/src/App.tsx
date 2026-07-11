import { useCallback, useEffect, useMemo, useState } from "react";
import { LogOut, Plus } from "lucide-react";
import {
  acceptProposal,
  cancelRun,
  confirmRevision,
  createRevision,
  createSystem,
  fetchSession,
  finalizeRevision,
  getRevision,
  getRun,
  listMatrixRows,
  listProposals,
  listRevisions,
  listRuns,
  listSystems,
  login,
  logout,
  rejectProposal,
  revisionEtag,
  startRun,
  uploadJsonFile,
  type AnalysisRun,
  type FactProposal,
  type MatrixRow,
  type PackageRevision,
  type Problem,
  type SessionInfo,
  type System,
} from "./api";
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
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type LoadState = "loading" | "ready" | "error" | "empty";

function formatProblem(problem: Problem): string {
  return `${problem.error_code} (${problem.status})${problem.detail ? `: ${problem.detail}` : ""}`;
}

function revisionStatusVariant(
  status: string,
): "default" | "secondary" | "success" | "warning" | "muted" {
  switch (status) {
    case "ready":
    case "succeeded":
      return "success";
    case "awaiting_confirmation":
    case "queued":
    case "running":
      return "warning";
    case "scanning":
    case "extracting":
    case "uploading":
      return "secondary";
    default:
      return "muted";
  }
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
        "rounded-md border px-4 py-3 text-sm",
        tone === "error" && "border-destructive/30 bg-destructive/10 text-destructive",
        tone === "info" && "border-primary/20 bg-primary/5 text-foreground",
        tone === "warning" && "border-amber-500/30 bg-amber-500/10 text-amber-900",
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

export default function App() {
  const [session, setSession] = useState<SessionInfo | null | undefined>(undefined);
  const [systems, setSystems] = useState<System[]>([]);
  const [selectedSystemId, setSelectedSystemId] = useState<string>("");
  const [revisions, setRevisions] = useState<PackageRevision[]>([]);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string>("");
  const [revision, setRevision] = useState<PackageRevision | null>(null);
  const [proposals, setProposals] = useState<FactProposal[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [activeRun, setActiveRun] = useState<AnalysisRun | null>(null);
  const [matrixRows, setMatrixRows] = useState<MatrixRow[]>([]);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [systemsState, setSystemsState] = useState<LoadState>("loading");
  const [revisionState, setRevisionState] = useState<LoadState>("empty");

  const refreshSession = useCallback(async () => {
    try {
      const value = await fetchSession();
      setSession(value);
      setError("");
    } catch (problem) {
      setSession(null);
      setError(formatProblem(problem as Problem));
    }
  }, []);

  const refreshSystems = useCallback(async () => {
    if (!session) {
      return;
    }
    setSystemsState("loading");
    try {
      const items = await listSystems();
      setSystems(items);
      setSystemsState(items.length === 0 ? "empty" : "ready");
      if (!selectedSystemId && items.length > 0) {
        setSelectedSystemId(items[0].system_id);
      }
      setError("");
    } catch (problem) {
      setSystemsState("error");
      setError(formatProblem(problem as Problem));
    }
  }, [session, selectedSystemId]);

  const refreshRevisions = useCallback(async () => {
    if (!selectedSystemId) {
      setRevisions([]);
      return;
    }
    try {
      const items = await listRevisions(selectedSystemId);
      setRevisions(items);
      if (!selectedRevisionId && items.length > 0) {
        setSelectedRevisionId(items[0].package_revision_id);
      }
      setError("");
    } catch (problem) {
      setError(formatProblem(problem as Problem));
    }
  }, [selectedSystemId, selectedRevisionId]);

  const refreshRevisionDetail = useCallback(async () => {
    if (!selectedRevisionId) {
      setRevision(null);
      setProposals([]);
      setRuns([]);
      setSelectedRunId("");
      setActiveRun(null);
      setMatrixRows([]);
      setRevisionState("empty");
      return;
    }
    setRevisionState("loading");
    try {
      const detail = await getRevision(selectedRevisionId);
      setRevision(detail);
      const proposalItems = await listProposals(selectedRevisionId);
      setProposals(proposalItems);
      const runItems = await listRuns(selectedRevisionId);
      setRuns(runItems);
      if (!selectedRunId && runItems.length > 0) {
        setSelectedRunId(runItems[0].run_id);
      }
      setRevisionState("ready");
      setError("");
    } catch (problem) {
      setRevisionState("error");
      setError(formatProblem(problem as Problem));
    }
  }, [selectedRevisionId, selectedRunId]);

  const refreshRunDetail = useCallback(async () => {
    if (!selectedRunId) {
      setActiveRun(null);
      setMatrixRows([]);
      return;
    }
    try {
      const run = await getRun(selectedRunId);
      setActiveRun(run);
      if (run.status === "succeeded") {
        const matrix = await listMatrixRows(selectedRunId);
        setMatrixRows(matrix.items);
      } else {
        setMatrixRows([]);
      }
      setError("");
    } catch (problem) {
      setError(formatProblem(problem as Problem));
    }
  }, [selectedRunId]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (session) {
      void refreshSystems();
    }
  }, [session, refreshSystems]);

  useEffect(() => {
    void refreshRevisions();
  }, [refreshRevisions]);

  useEffect(() => {
    void refreshRevisionDetail();
  }, [refreshRevisionDetail]);

  useEffect(() => {
    void refreshRunDetail();
  }, [refreshRunDetail]);

  useEffect(() => {
    if (!selectedRevisionId || !revision) {
      return;
    }
    if (revision.status === "scanning" || revision.status === "extracting") {
      const timer = window.setInterval(() => {
        void refreshRevisionDetail();
      }, 2000);
      return () => window.clearInterval(timer);
    }
    return undefined;
  }, [selectedRevisionId, revision, refreshRevisionDetail]);

  useEffect(() => {
    if (!selectedRunId || !activeRun) {
      return;
    }
    if (activeRun.status === "queued" || activeRun.status === "running") {
      const timer = window.setInterval(() => {
        void refreshRunDetail();
      }, 2000);
      return () => window.clearInterval(timer);
    }
    return undefined;
  }, [selectedRunId, activeRun, refreshRunDetail]);

  const pendingProposals = useMemo(
    () => proposals.filter((item) => item.review_status === "pending"),
    [proposals],
  );

  if (session === undefined) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">Loading session…</p>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6 py-8">
        <Card className="w-full max-w-lg">
          <CardHeader>
            <CardTitle className="text-2xl">ATO Evidence Analysis Portal</CardTitle>
            <CardDescription>
              Sign in with OIDC to manage systems, uploads, and fact proposals.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {error ? <AlertBanner tone="error">{error}</AlertBanner> : null}
            <Button type="button" onClick={login}>
              Sign in
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="portal-scrollbar min-h-screen bg-background">
      <main className="mx-auto w-full max-w-6xl space-y-6 px-6 py-8">
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
            <div className="space-y-1.5">
              <CardTitle className="text-2xl">ATO Evidence Analysis Portal</CardTitle>
              <CardDescription>
                Signed in as {session.actor_id} ({session.groups.join(", ")})
              </CardDescription>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void logout().then(refreshSession)}
            >
              <LogOut />
              Sign out
            </Button>
          </CardHeader>
        </Card>

        {message ? <AlertBanner tone="info">{message}</AlertBanner> : null}
        {error ? <AlertBanner tone="error">{error}</AlertBanner> : null}

        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-4 space-y-0">
            <CardTitle>Systems</CardTitle>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                void createSystem(session, `System ${systems.length + 1}`)
                  .then(() => refreshSystems())
                  .then(() => setMessage("System created."))
                  .catch((problem) => setError(formatProblem(problem as Problem)));
              }}
            >
              <Plus />
              Create system
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {systemsState === "loading" ? (
              <p className="text-sm text-muted-foreground">Loading systems…</p>
            ) : null}
            {systemsState === "empty" ? (
              <p className="text-sm text-muted-foreground">No systems yet.</p>
            ) : null}
            <SelectionList
              items={systems.map((item) => ({
                id: item.system_id,
                label: item.display_name,
              }))}
              selectedId={selectedSystemId}
              onSelect={setSelectedSystemId}
            />
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
                      return refreshRevisions();
                    })
                    .then(() => setMessage("Revision created."))
                    .catch((problem) => setError(formatProblem(problem as Problem)));
                }}
              >
                <Plus />
                Create revision
              </Button>
            </CardHeader>
            <CardContent>
              <SelectionList
                items={revisions.map((item) => ({
                  id: item.package_revision_id,
                  label: `${item.package_revision_id.slice(0, 8)}…`,
                  status: item.status,
                }))}
                selectedId={selectedRevisionId}
                onSelect={setSelectedRevisionId}
                renderLabel={(item) => `${item.label} — ${item.status ?? ""}`}
              />
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
              {revision.status === "uploading" ? (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="evidence-upload">Upload synthetic JSON evidence</Label>
                    <Input
                      id="evidence-upload"
                      type="file"
                      accept="application/json,.json"
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (!file) {
                          return;
                        }
                        void uploadJsonFile(session, revision.package_revision_id, file)
                          .then(refreshRevisionDetail)
                          .then(() => setMessage("File uploaded."))
                          .catch((problem) => setError(formatProblem(problem as Problem)));
                      }}
                    />
                  </div>
                  <Button
                    type="button"
                    onClick={() => {
                      void finalizeRevision(session, revision.package_revision_id)
                        .then(refreshRevisionDetail)
                        .then(() =>
                          setMessage(
                            "Finalize accepted; intake worker will scan and extract.",
                          ),
                        )
                        .catch((problem) => setError(formatProblem(problem as Problem)));
                    }}
                  >
                    Finalize upload
                  </Button>
                </div>
              ) : null}

              {revision.status === "scanning" || revision.status === "extracting" ? (
                <p className="text-sm text-muted-foreground">
                  Intake worker is processing this revision…
                </p>
              ) : null}

              {revision.status === "awaiting_confirmation" ? (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-base font-semibold">Fact proposals</h3>
                    {revisionState === "loading" ? (
                      <p className="mt-2 text-sm text-muted-foreground">Loading proposals…</p>
                    ) : null}
                  </div>
                  <div className="space-y-4">
                    {proposals.map((proposal) => (
                      <Card key={proposal.fact_proposal_id} className="bg-muted/30">
                        <CardContent className="space-y-3 p-4">
                          <code className="rounded bg-background px-2 py-1 text-xs">
                            {proposal.json_pointer}
                          </code>
                          <pre className="overflow-auto rounded-md border bg-background p-3 text-xs">
                            {JSON.stringify(proposal.proposed_value, null, 2)}
                          </pre>
                          <div className="flex items-center gap-2 text-sm">
                            <span className="text-muted-foreground">Status</span>
                            <Badge variant="warning">{proposal.review_status}</Badge>
                          </div>
                          {proposal.review_status === "pending" ? (
                            <div className="flex gap-2">
                              <Button
                                type="button"
                                size="sm"
                                onClick={() => {
                                  void acceptProposal(
                                    session,
                                    proposal.fact_proposal_id,
                                    revisionEtag(revision.revision_version),
                                  )
                                    .then(refreshRevisionDetail)
                                    .then(() => setMessage("Proposal accepted."))
                                    .catch((problem) =>
                                      setError(formatProblem(problem as Problem)),
                                    );
                                }}
                              >
                                Accept
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="destructive"
                                onClick={() => {
                                  void rejectProposal(
                                    session,
                                    proposal.fact_proposal_id,
                                    revisionEtag(revision.revision_version),
                                    "Rejected in portal review",
                                  )
                                    .then(refreshRevisionDetail)
                                    .then(() => setMessage("Proposal rejected."))
                                    .catch((problem) =>
                                      setError(formatProblem(problem as Problem)),
                                    );
                                }}
                              >
                                Reject
                              </Button>
                            </div>
                          ) : null}
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                  <div className="space-y-2">
                    <Button
                      type="button"
                      disabled={pendingProposals.length > 0}
                      onClick={() => {
                        void confirmRevision(
                          session,
                          revision.package_revision_id,
                          revisionEtag(revision.revision_version),
                        )
                          .then(refreshRevisionDetail)
                          .then(() => setMessage("Revision confirmed and sealed."))
                          .catch((problem) => setError(formatProblem(problem as Problem)));
                      }}
                    >
                      Confirm revision
                    </Button>
                    {pendingProposals.length > 0 ? (
                      <p className="text-sm text-muted-foreground">
                        Resolve all pending proposals before confirming.
                      </p>
                    ) : null}
                  </div>
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
                          .then(refreshRunDetail)
                          .then(() => setMessage("Deterministic analysis run started."))
                          .catch((problem) => setError(formatProblem(problem as Problem)));
                      }}
                    >
                      Start deterministic run
                    </Button>
                  </div>

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

                  {activeRun ? (
                    <Card className="bg-muted/20">
                      <CardHeader>
                        <CardTitle className="text-base">Run status</CardTitle>
                        <CardDescription className="flex flex-wrap items-center gap-2">
                          <Badge variant={revisionStatusVariant(activeRun.status)}>
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
                              onClick={() => {
                                void cancelRun(session, activeRun.run_id)
                                  .then(refreshRunDetail)
                                  .then(refreshRevisionDetail)
                                  .then(() => setMessage("Run cancellation accepted."))
                                  .catch((problem) =>
                                    setError(formatProblem(problem as Problem)),
                                  );
                              }}
                            >
                              Cancel run
                            </Button>
                          </>
                        ) : null}

                        {activeRun.status === "succeeded" && matrixRows.length > 0 ? (
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
                                    <tr key={row.matrix_row_id} className="border-b last:border-0">
                                      <td className="px-4 py-3 align-top font-mono text-xs">
                                        {row.assessment_item_id}
                                      </td>
                                      <td className="px-4 py-3 align-top">
                                        <Badge variant="muted">{row.model_proposed_status}</Badge>
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
                        ) : null}
                      </CardContent>
                    </Card>
                  ) : null}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </main>
    </div>
  );
}
