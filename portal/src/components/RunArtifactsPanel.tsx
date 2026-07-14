import { useEffect, useState } from "react";
import { ApiError, listRunArtifacts, isCancelledRequest } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AnalysisRun, ArtifactDescriptor } from "@/types";
import { formatApiError } from "@/utils/formatApiError";

type RunArtifactsPanelProps = {
  run: AnalysisRun;
};

export function RunArtifactsPanel({ run }: RunArtifactsPanelProps) {
  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);
  const [error, setError] = useState("");
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    if (run.status !== "succeeded") {
      setArtifacts([]);
      return;
    }
    const controller = new AbortController();
    void listRunArtifacts(run.run_id, { signal: controller.signal })
      .then((page) => {
        setArtifacts(page.items);
        setUnsupported(false);
        setError("");
      })
      .catch((err) => {
        if (isCancelledRequest(err, controller.signal)) {
          return;
        }
        setArtifacts([]);
        if (err instanceof ApiError && err.status === 404) {
          setUnsupported(true);
          setError("");
          return;
        }
        setUnsupported(false);
        setError(formatApiError(err));
      });
    return () => controller.abort();
  }, [run.run_id, run.status]);

  if (run.status !== "succeeded") {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Run artifacts</CardTitle>
        <CardDescription className="flex flex-wrap items-center gap-2">
          {run.artifact_manifest_sha256 ? (
            <>
              <span>Manifest</span>
              <span className="font-mono text-xs">
                {run.artifact_manifest_sha256.slice(0, 16)}…
              </span>
            </>
          ) : (
            <span>No artifact manifest recorded</span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error ? <p className="text-destructive">{error}</p> : null}
        {unsupported ? (
          <p className="text-muted-foreground">
            Artifact listing is not available on this API build. Use the manifest hash above
            for integrity checks.
          </p>
        ) : null}
        {artifacts.length === 0 && !error && !unsupported ? (
          <p className="text-muted-foreground">No run artifacts returned.</p>
        ) : null}
        {artifacts.length > 0 ? (
          <ul className="space-y-2">
            {artifacts.map((artifact) => (
              <li key={artifact.artifact_id} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="muted">{artifact.media_type}</Badge>
                  <span className="font-mono text-xs">{artifact.path}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {artifact.sha256.slice(0, 16)}… · {artifact.size_bytes} bytes
                </p>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
