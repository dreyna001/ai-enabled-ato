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
  listSspWorkspaces,
  removeSspEvidence,
  rejectSspPatch,
  saveSspControl,
  saveSspSection,
  uploadSspEvidence,
  type SspProfile,
} from "@/api/sspWorkspace";
import { SspWorkspacePage } from "@/pages/SspWorkspacePage";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { SessionInfo } from "@/types";
import type { SspWorkspace } from "@/sspWorkspaceTypes";
import { formatApiError } from "@/utils/formatApiError";

export function SspWorkspaceRoute({ session }: { session: SessionInfo }) {
  const [workspace, setWorkspace] = useState<SspWorkspace | null>(null);
  const [workspaces, setWorkspaces] = useState<SspWorkspace[]>([]);
  const [profiles, setProfiles] = useState<SspProfile[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newSystemName, setNewSystemName] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [impactLevel, setImpactLevel] =
    useState<"low" | "moderate" | "high">("moderate");

  const load = useCallback(async () => {
    setState("loading");
    try {
      const [workspaceRows, profileRows] = await Promise.all([
        listSspWorkspaces(),
        listSspProfiles(),
      ]);
      setWorkspaces(workspaceRows);
      setWorkspace((current) =>
        workspaceRows.find((item) => item.id === current?.id) ??
        workspaceRows[0] ??
        null,
      );
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
        const updated = await operation(workspace);
        setWorkspace(updated);
        setWorkspaces((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
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

  if (!workspace || creatingWorkspace) {
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
              if (!name || !activeProfile || busy) return;
              setBusy(true);
              void createSspSystem(session, name)
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
                  setWorkspaces((current) => [
                    created,
                    ...current.filter((item) => item.id !== created.id),
                  ]);
                  setCreatingWorkspace(false);
                  setNewSystemName("");
                  setError("");
                })
                .catch((caught) => setError(formatApiError(caught)))
                .finally(() => setBusy(false));
            }}
          >
            <label className="block text-sm">
              <span className="mb-1 block font-medium">System name</span>
              <input
                className="w-full rounded-sm border bg-background px-3 py-2"
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
                !newSystemName.trim() ||
                !activeProfile
              }
              type="submit"
            >
              Create workspace
            </Button>
            {workspace ? (
              <Button
                className="ml-2"
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setCreatingWorkspace(false);
                  setError("");
                }}
              >
                Cancel
              </Button>
            ) : null}
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
        availableWorkspaces={workspaces.map((item) => ({
          id: item.id,
          name: item.name,
        }))}
        actions={{
          onRetry: () => void load(),
          onOpenWorkspace: (workspaceId) => {
            const selected = workspaces.find((item) => item.id === workspaceId);
            if (selected) {
              setWorkspace(selected);
              setError("");
            }
          },
          onNewWorkspace: () => {
            setCreatingWorkspace(true);
            setNewSystemName("");
            setError("");
          },
          onUploadEvidence: (files) => {
            if (busy) return;
            setBusy(true);
            void (async () => {
              let current = workspace;
              for (const file of files) {
                current = await uploadSspEvidence(session, current, file);
              }
              setWorkspace(current);
              setWorkspaces((items) =>
                items.map((item) => (item.id === current.id ? current : item)),
              );
            })()
              .catch((caught) => setError(formatApiError(caught)))
              .finally(() => setBusy(false));
          },
          onRemoveEvidence: (artifactId) =>
            void run((current) =>
              removeSspEvidence(session, current, artifactId),
            ),
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
