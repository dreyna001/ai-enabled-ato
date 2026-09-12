import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  activateProfile,
  importProfile,
  listProfiles,
  type Profile,
} from "@/api/profileClient";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { SessionInfo } from "@/types";
import { isCancelledRequest } from "@/api/client";
import { formatApiError } from "@/utils/formatApiError";

type ProfilesLoadState = "loading" | "ready" | "error";
type ProfileAction = "import" | "activate" | null;

function latestImportedProfile(profiles: Profile[]): Profile | null {
  return profiles.reduce<Profile | null>((latest, profile) => {
    if (!latest) {
      return profile;
    }
    const profileTime = Date.parse(profile.imported_at);
    const latestTime = Date.parse(latest.imported_at);
    if (
      !Number.isNaN(profileTime) &&
      (Number.isNaN(latestTime) || profileTime > latestTime)
    ) {
      return profile;
    }
    return latest;
  }, null);
}

function statusVariant(
  status: Profile["status"],
): "success" | "warning" | "muted" {
  if (status === "active") {
    return "success";
  }
  if (status === "inactive") {
    return "warning";
  }
  return "muted";
}

function profileTime(value: string | null): ReactNode {
  if (!value) {
    return "Not activated";
  }
  return (
    <time className="font-mono text-xs" dateTime={value}>
      {value}
    </time>
  );
}

function activeSummary(profiles: Profile[]): string {
  const active = profiles.filter((profile) => profile.status === "active");
  if (active.length === 0) {
    return "None reported";
  }
  if (active.length === 1) {
    return `${active[0].profile_id} · ${active[0].version}`;
  }
  return `${active.length} active versions`;
}

export function ProfilesPage({ session }: { session: SessionInfo }) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loadState, setLoadState] = useState<ProfilesLoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [canManage, setCanManage] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileInputVersion, setFileInputVersion] = useState(0);
  const [busyAction, setBusyAction] = useState<ProfileAction>(null);
  const [activationTarget, setActivationTarget] = useState<Profile | null>(null);
  const loadControllerRef = useRef<AbortController | null>(null);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const sessionIdentity = `${session.actor_id}:${session.csrf_token}`;

  const loadProfiles = useCallback(async (signal: AbortSignal) => {
    setLoadState("loading");
    setLoadError("");
    try {
      const result = await listProfiles({ signal });
      if (signal.aborted) {
        return false;
      }
      setProfiles(result.items);
      setCanManage(result.can_manage);
      setLoadState("ready");
      return true;
    } catch (caught) {
      if (isCancelledRequest(caught, signal)) {
        return false;
      }
      if (!signal.aborted) {
        setLoadError(formatApiError(caught));
        setLoadState("error");
      }
      return false;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadControllerRef.current = controller;
    setProfiles([]);
    setCanManage(false);
    setSelectedFile(null);
    setFileInputVersion((version) => version + 1);
    setActionError("");
    setActivationTarget(null);
    setBusyAction(null);
    void loadProfiles(controller.signal);
    return () => {
      controller.abort();
      if (loadControllerRef.current === controller) {
        loadControllerRef.current = null;
      }
      mutationControllerRef.current?.abort();
      mutationControllerRef.current = null;
    };
  }, [loadProfiles, sessionIdentity]);

  const retry = () => {
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    void loadProfiles(controller.signal);
  };

  const handleImport = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canManage || busyAction) {
      return;
    }
    if (!selectedFile) {
      setActionError("Select a profile bundle ZIP file before importing.");
      return;
    }

    const file = selectedFile;
    const controller = new AbortController();
    mutationControllerRef.current = controller;
    setActionError("");
    setBusyAction("import");
    void (async () => {
      try {
        await importProfile(session, file, { signal: controller.signal });
        if (controller.signal.aborted) {
          return;
        }
        setSelectedFile(null);
        setFileInputVersion((version) => version + 1);
        await loadProfiles(controller.signal);
      } catch (caught) {
        if (!isCancelledRequest(caught, controller.signal)) {
          setActionError(formatApiError(caught));
        }
      } finally {
        if (!controller.signal.aborted) {
          setBusyAction(null);
        }
        if (mutationControllerRef.current === controller) {
          mutationControllerRef.current = null;
        }
      }
    })();
  };

  const handleActivation = () => {
    if (!canManage || !activationTarget || busyAction) {
      return;
    }
    const target = activationTarget;
    const controller = new AbortController();
    mutationControllerRef.current = controller;
    setActionError("");
    setBusyAction("activate");
    void (async () => {
      try {
        await activateProfile(session, target.profile_version_id, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) {
          return;
        }
        setActivationTarget(null);
        await loadProfiles(controller.signal);
      } catch (caught) {
        if (!isCancelledRequest(caught, controller.signal)) {
          setActionError(formatApiError(caught));
        }
      } finally {
        if (!controller.signal.aborted) {
          setBusyAction(null);
        }
        if (mutationControllerRef.current === controller) {
          mutationControllerRef.current = null;
        }
      }
    })();
  };

  const latestImported = latestImportedProfile(profiles);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className="font-mono text-xs uppercase tracking-[0.16em] text-muted-foreground">
          Profile administration
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Profiles</h1>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
          Review locally imported SSP profile versions. The server validates
          bundles and remains authoritative for activation and status.
        </p>
      </header>

      {loadState === "loading" ? (
        <div
          aria-busy="true"
          className="rounded-sm border border-border/70 bg-card px-4 py-5 text-sm text-muted-foreground"
          role="status"
        >
          Loading profiles…
        </div>
      ) : null}

      {loadState === "error" ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-destructive/40 bg-destructive/10 p-4"
          role="alert"
        >
          <div>
            <p className="font-medium text-destructive">Unable to load profiles.</p>
            <p className="mt-1 text-sm text-destructive/90">{loadError}</p>
          </div>
          <Button type="button" variant="outline" onClick={retry}>
            Retry
          </Button>
        </div>
      ) : null}

      {loadState === "ready" ? (
        <>
          {canManage ? (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Import profile bundle</CardTitle>
              </CardHeader>
              <CardContent>
                <form
                  aria-busy={busyAction === "import"}
                  className="flex flex-col gap-3 sm:flex-row sm:items-end"
                  onSubmit={handleImport}
                >
                  <div className="min-w-0 flex-1 space-y-2">
                    <Label htmlFor="profile-bundle">Profile bundle ZIP</Label>
                    <Input
                      key={`${sessionIdentity}-${fileInputVersion}`}
                      id="profile-bundle"
                      accept=".zip,application/zip"
                      type="file"
                      onChange={(event) => {
                        setSelectedFile(event.currentTarget.files?.[0] ?? null);
                        setActionError("");
                      }}
                    />
                    <p className="text-xs text-muted-foreground">
                      The server validates the ZIP contents, manifest, and checksums.
                    </p>
                  </div>
                  <Button disabled={busyAction !== null} type="submit">
                    {busyAction === "import" ? "Importing…" : "Import profile"}
                  </Button>
                </form>
              </CardContent>
            </Card>
          ) : (
            <div className="rounded-sm border border-border/70 bg-muted/20 p-4 text-sm text-muted-foreground">
              Read-only access. Profile import and activation require the
              platform-admins role; the server enforces this boundary.
            </div>
          )}

          {actionError ? (
            <div
              className="rounded-sm border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive"
              role="alert"
            >
              {actionError}
            </div>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
                  Latest imported version
                </CardTitle>
              </CardHeader>
              <CardContent>
                {latestImported ? (
                  <>
                    <p className="font-mono text-sm">
                      {latestImported.profile_id} · {latestImported.version}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Imported <span className="font-mono">{latestImported.imported_at}</span> by {latestImported.imported_by}
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">None imported</p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
                  Globally current (server status)
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="font-mono text-sm">{activeSummary(profiles)}</p>
                <p className="mt-2 text-xs text-muted-foreground">
                  Active is reported per profile ID. It is distinct from the latest imported version.
                </p>
              </CardContent>
            </Card>
          </div>

          {profiles.length === 0 ? (
            <EmptyState
              title="No profile versions are available"
              description={
                canManage
                  ? "Import a validated local ZIP bundle to make a profile version available."
                  : "No profile versions were returned by the server."
              }
            />
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Imported profile versions</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[900px] border-collapse text-sm">
                    <caption className="sr-only">
                      Imported SSP profile versions and server-reported lifecycle status
                    </caption>
                    <thead className="border-y border-border/70 bg-muted/30 text-left text-xs uppercase tracking-[0.08em] text-muted-foreground">
                      <tr>
                        <th className="px-4 py-3" scope="col">Profile</th>
                        <th className="px-4 py-3" scope="col">Version</th>
                        <th className="px-4 py-3" scope="col">Status</th>
                        <th className="px-4 py-3" scope="col">Bundle SHA-256</th>
                        <th className="px-4 py-3" scope="col">Imported</th>
                        <th className="px-4 py-3" scope="col">Activated</th>
                        {canManage ? <th className="px-4 py-3" scope="col">Action</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {profiles.map((profile) => (
                        <tr className="border-b border-border/60 align-top last:border-b-0" key={profile.profile_version_id}>
                          <td className="px-4 py-4">
                            <div className="font-medium">{profile.display_name}</div>
                            <code className="mt-1 block break-all text-xs text-muted-foreground">{profile.profile_id}</code>
                            <div className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{profile.profile_version_id}</div>
                          </td>
                          <td className="px-4 py-4 font-mono">{profile.version}</td>
                          <td className="px-4 py-4">
                            <Badge variant={statusVariant(profile.status)}>{profile.status}</Badge>
                          </td>
                          <td className="max-w-[260px] px-4 py-4">
                            <code className="break-all text-xs">{profile.bundle_sha256}</code>
                          </td>
                          <td className="px-4 py-4">
                            {profileTime(profile.imported_at)}
                            <div className="mt-1 text-xs text-muted-foreground">by {profile.imported_by}</div>
                          </td>
                          <td className="px-4 py-4">{profileTime(profile.activated_at)}</td>
                          {canManage ? (
                            <td className="px-4 py-4">
                              {profile.status === "inactive" ? (
                                <Button
                                  disabled={busyAction !== null}
                                  size="sm"
                                  type="button"
                                  variant="outline"
                                  onClick={() => {
                                    setActionError("");
                                    setActivationTarget(profile);
                                  }}
                                >
                                  Activate
                                </Button>
                              ) : (
                                <span className="text-xs text-muted-foreground">No action</span>
                              )}
                            </td>
                          ) : null}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      ) : null}

      <ConfirmDialog
        confirming={busyAction === "activate"}
        description={
          activationTarget
            ? `Activate ${activationTarget.profile_id} version ${activationTarget.version}? This changes the server-reported active version for this profile ID. Existing workspaces remain pinned until explicitly migrated.`
            : ""
        }
        error={activationTarget ? actionError : null}
        open={activationTarget !== null}
        title="Activate profile version"
        confirmLabel="Activate profile"
        onCancel={() => {
          if (!busyAction) {
            setActivationTarget(null);
            setActionError("");
          }
        }}
        onConfirm={handleActivation}
      />
    </div>
  );
}
