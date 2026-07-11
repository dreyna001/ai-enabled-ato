import { useCallback, useEffect, useMemo, useState } from "react";
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

type LoadState = "loading" | "ready" | "error" | "empty";

function formatProblem(problem: Problem): string {
  return `${problem.error_code} (${problem.status})${problem.detail ? `: ${problem.detail}` : ""}`;
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
    return <main className="page"><p className="status">Loading session…</p></main>;
  }

  if (!session) {
    return (
      <main className="page">
        <header className="hero">
          <h1>ATO Evidence Analysis Portal</h1>
          <p>Sign in with OIDC to manage systems, uploads, and fact proposals.</p>
        </header>
        {error ? <p className="error">{error}</p> : null}
        <button type="button" onClick={login}>Sign in</button>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="topbar">
        <div>
          <h1>ATO Evidence Analysis Portal</h1>
          <p className="muted">
            Signed in as {session.actor_id} ({session.groups.join(", ")})
          </p>
        </div>
        <button type="button" onClick={() => void logout().then(refreshSession)}>
          Sign out
        </button>
      </header>

      {message ? <p className="message">{message}</p> : null}
      {error ? <p className="error">{error}</p> : null}

      <section className="panel">
        <div className="panel-header">
          <h2>Systems</h2>
          <button
            type="button"
            onClick={() => {
              void createSystem(session, `System ${systems.length + 1}`)
                .then(() => refreshSystems())
                .then(() => setMessage("System created."))
                .catch((problem) => setError(formatProblem(problem as Problem)));
            }}
          >
            Create system
          </button>
        </div>
        {systemsState === "loading" ? <p className="status">Loading systems…</p> : null}
        {systemsState === "empty" ? <p className="status">No systems yet.</p> : null}
        <ul className="list">
          {systems.map((item) => (
            <li key={item.system_id}>
              <button
                type="button"
                className={item.system_id === selectedSystemId ? "selected" : ""}
                onClick={() => setSelectedSystemId(item.system_id)}
              >
                {item.display_name}
              </button>
            </li>
          ))}
        </ul>
      </section>

      {selectedSystemId ? (
        <section className="panel">
          <div className="panel-header">
            <h2>Package revisions</h2>
            <button
              type="button"
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
              Create revision
            </button>
          </div>
          <ul className="list">
            {revisions.map((item) => (
              <li key={item.package_revision_id}>
                <button
                  type="button"
                  className={
                    item.package_revision_id === selectedRevisionId ? "selected" : ""
                  }
                  onClick={() => setSelectedRevisionId(item.package_revision_id)}
                >
                  {item.package_revision_id.slice(0, 8)}… — {item.status}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {revision ? (
        <section className="panel">
          <h2>Revision workflow</h2>
          <p>
            Status: <strong>{revision.status}</strong> · version {revision.revision_version}
          </p>

          {revision.status === "uploading" ? (
            <label className="upload">
              <span>Upload synthetic JSON evidence</span>
              <input
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
            </label>
          ) : null}

          {revision.status === "uploading" ? (
            <button
              type="button"
              onClick={() => {
                void finalizeRevision(session, revision.package_revision_id)
                  .then(refreshRevisionDetail)
                  .then(() => setMessage("Finalize accepted; intake worker will scan and extract."))
                  .catch((problem) => setError(formatProblem(problem as Problem)));
              }}
            >
              Finalize upload
            </button>
          ) : null}

          {revision.status === "scanning" || revision.status === "extracting" ? (
            <p className="status">Intake worker is processing this revision…</p>
          ) : null}

          {revision.status === "awaiting_confirmation" ? (
            <>
              <h3>Fact proposals</h3>
              {revisionState === "loading" ? <p className="status">Loading proposals…</p> : null}
              <ul className="proposal-list">
                {proposals.map((proposal) => (
                  <li key={proposal.fact_proposal_id}>
                    <code>{proposal.json_pointer}</code>
                    <pre>{JSON.stringify(proposal.proposed_value, null, 2)}</pre>
                    <p>Status: {proposal.review_status}</p>
                    {proposal.review_status === "pending" ? (
                      <div className="actions">
                        <button
                          type="button"
                          onClick={() => {
                            void acceptProposal(
                              session,
                              proposal.fact_proposal_id,
                              revisionEtag(revision.revision_version),
                            )
                              .then(refreshRevisionDetail)
                              .then(() => setMessage("Proposal accepted."))
                              .catch((problem) => setError(formatProblem(problem as Problem)));
                          }}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            void rejectProposal(
                              session,
                              proposal.fact_proposal_id,
                              revisionEtag(revision.revision_version),
                              "Rejected in portal review",
                            )
                              .then(refreshRevisionDetail)
                              .then(() => setMessage("Proposal rejected."))
                              .catch((problem) => setError(formatProblem(problem as Problem)));
                          }}
                        >
                          Reject
                        </button>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
              <button
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
              </button>
              {pendingProposals.length > 0 ? (
                <p className="muted">Resolve all pending proposals before confirming.</p>
              ) : null}
            </>
          ) : null}

          {revision.status === "ready" ? (
            <>
              <p className="message">
                Draft analysis readiness - not official status in GRC, FedRAMP, or an agency
                authorization process.
              </p>
              <div className="panel-header">
                <h3>Analysis runs</h3>
                <button
                  type="button"
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
                </button>
              </div>
              <ul className="list">
                {runs.map((item) => (
                  <li key={item.run_id}>
                    <button
                      type="button"
                      className={item.run_id === selectedRunId ? "selected" : ""}
                      onClick={() => setSelectedRunId(item.run_id)}
                    >
                      {item.run_id.slice(0, 8)}… — {item.status}
                    </button>
                  </li>
                ))}
              </ul>
              {activeRun ? (
                <section className="panel">
                  <h3>Run status</h3>
                  <p>
                    Status: <strong>{activeRun.status}</strong> · LLM calls:{" "}
                    {activeRun.llm_call_count}
                  </p>
                  {activeRun.status === "queued" || activeRun.status === "running" ? (
                    <>
                      <p className="status">
                        Deterministic analyzer worker is processing this run…
                      </p>
                      <button
                        type="button"
                        onClick={() => {
                          void cancelRun(session, activeRun.run_id)
                            .then(refreshRunDetail)
                            .then(refreshRevisionDetail)
                            .then(() => setMessage("Run cancellation accepted."))
                            .catch((problem) => setError(formatProblem(problem as Problem)));
                        }}
                      >
                        Cancel run
                      </button>
                    </>
                  ) : null}
                  {activeRun.status === "succeeded" && matrixRows.length > 0 ? (
                    <>
                      <p className="message">
                        Artifact manifest: {activeRun.artifact_manifest_sha256?.slice(0, 16)}…
                      </p>
                      <h4>Matrix</h4>
                      <table className="matrix-table">
                        <thead>
                          <tr>
                            <th>Item</th>
                            <th>Status</th>
                            <th>Summary</th>
                          </tr>
                        </thead>
                        <tbody>
                          {matrixRows.map((row) => (
                            <tr key={row.matrix_row_id}>
                              <td>{row.assessment_item_id}</td>
                              <td>{row.model_proposed_status}</td>
                              <td>{row.finding_summary}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}
