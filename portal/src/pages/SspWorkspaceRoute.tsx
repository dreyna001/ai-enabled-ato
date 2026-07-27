import { useCallback, useEffect, useState } from "react";
import {
  applySspPatch,
  approveSspWorkspace,
  askSspAgent,
  createSspSystem,
  createSspWorkspace,
  downloadSspExport,
  generateSspWorkspace,
  listSspProfiles,
  listSspSystems,
  listSspWorkspaces,
  rejectSspPatch,
  saveSspControl,
  saveSspSection,
  uploadSspEvidence,
  type SspProfile,
} from "@/api/sspWorkspace";
import { SspWorkspacePage } from "@/pages/SspWorkspacePage";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SessionInfo, System } from "@/types";
import type { SspWorkspace } from "@/sspWorkspaceTypes";
import { formatApiError } from "@/utils/formatApiError";

export function SspWorkspaceRoute({ session }: { session: SessionInfo }) {
  const [workspace, setWorkspace] = useState<SspWorkspace | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [profiles, setProfiles] = useState<SspProfile[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedSystemId, setSelectedSystemId] = useState("");
  const [newSystemName, setNewSystemName] = useState("");
  const [impactLevel, setImpactLevel] =
    useState<"low" | "moderate" | "high">("moderate");

  const load = useCallback(async () => {
    setState("loading");
    try {
      const [workspaceRows, systemRows, profileRows] = await Promise.all([
        listSspWorkspaces(),
        listSspSystems(),
        listSspProfiles(),
      ]);
      setWorkspace(workspaceRows[0] ?? null);
      setSystems(systemRows.filter((item) => !item.archived_at));
      setProfiles(profileRows);
      setError("");
      setState("ready");
    } catch (caught) {
      setError(formatApiError(caught));
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (operation: (current: SspWorkspace) => Promise<SspWorkspace>) => {
      if (!workspace || busy) return;
      setBusy(true);
      try {
        setWorkspace(await operation(workspace));
        setError("");
      } catch (caught) {
        setError(formatApiError(caught));
      } finally {
        setBusy(false);
      }
    },
    [busy, workspace],
  );

  if (state === "loading") return <SspWorkspacePage state="loading" />;
  if (state === "error") {
    return <SspWorkspacePage state="error" message={error} onRetry={() => void load()} />;
  }

  if (!workspace) {
    const activeProfile = profiles.find((item) => item.status === "active");
    return (
      <Card className="mx-auto max-w-xl">
        <CardHeader>
          <CardTitle>Create system workspace</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              const name = newSystemName.trim();
              const existingSystem = systems.find(
                (item) => item.system_id === selectedSystemId,
              );
              if ((!existingSystem && !name) || !activeProfile || busy) return;
              setBusy(true);
              void (existingSystem
                ? Promise.resolve(existingSystem)
                : createSspSystem(session, name)
              )
                .then((system) =>
                  createSspWorkspace(
                    session,
                    system,
                    activeProfile.profile_version_id,
                    impactLevel,
                  ),
                )
                .then((created) => {
                  setWorkspace(created);
                  setError("");
                })
                .catch((caught) => setError(formatApiError(caught)))
                .finally(() => setBusy(false));
            }}
          >
            {systems.length > 0 ? (
              <label className="block text-sm">
                <span className="mb-1 block font-medium">Existing system</span>
                <select
                  className="w-full rounded-sm border bg-background px-3 py-2"
                  value={selectedSystemId}
                  onChange={(event) => setSelectedSystemId(event.target.value)}
                >
                  <option value="">Create a new system</option>
                  {systems.map((system) => (
                    <option key={system.system_id} value={system.system_id}>
                      {system.display_name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="block text-sm">
              <span className="mb-1 block font-medium">System name</span>
              <input
                className="w-full rounded-sm border bg-background px-3 py-2"
                disabled={Boolean(selectedSystemId)}
                value={newSystemName}
                onChange={(event) => setNewSystemName(event.target.value)}
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Impact level</span>
              <select
                className="w-full rounded-sm border bg-background px-3 py-2"
                value={impactLevel}
                onChange={(event) =>
                  setImpactLevel(event.target.value as typeof impactLevel)
                }
              >
                <option value="low">Low</option>
                <option value="moderate">Moderate</option>
                <option value="high">High</option>
              </select>
            </label>
            <p className="text-xs text-muted-foreground">
              {activeProfile
                ? `${activeProfile.display_name} · ${activeProfile.version}`
                : "No active local profile is available."}
            </p>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button
              disabled={
                busy ||
                (!selectedSystemId && !newSystemName.trim()) ||
                !activeProfile
              }
              type="submit"
            >
              Create workspace
            </Button>
          </form>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {error ? (
        <div className="mb-3 rounded-sm border border-destructive/40 p-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        actions={{
          onRetry: () => void load(),
          onUploadEvidence: (files) => {
            if (busy) return;
            setBusy(true);
            void (async () => {
              let current = workspace;
              for (const file of files) {
                current = await uploadSspEvidence(session, current, file);
              }
              setWorkspace(current);
            })()
              .catch((caught) => setError(formatApiError(caught)))
              .finally(() => setBusy(false));
          },
          onGenerate: () => void run((current) => generateSspWorkspace(session, current)),
          onSaveSection: (change) =>
            void run((current) => saveSspSection(session, current, change)),
          onSaveControl: (change) =>
            void run((current) => saveSspControl(session, current, change)),
          onAskAgent: (context, message) =>
            void run((current) =>
              askSspAgent(session, current, context, message),
            ),
          onApplyPatch: (patchId) =>
            void run((current) => applySspPatch(session, current, patchId)),
          onRejectPatch: (patchId) =>
            void run((current) => rejectSspPatch(session, current, patchId)),
          onApprove: () =>
            void run((current) => approveSspWorkspace(session, current)),
          onExport: (format) => void downloadSspExport(workspace, format),
        }}
      />
    </>
  );
}
