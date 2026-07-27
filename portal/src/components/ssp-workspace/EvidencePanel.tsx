import { FileUp, Image, LoaderCircle, TriangleAlert } from "lucide-react";
import { useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { EvidenceArtifact } from "@/sspWorkspaceTypes";
import { evidenceStateLabel } from "@/utils/sspWorkspaceMetrics";

function stateVariant(state: EvidenceArtifact["state"]) {
  if (state === "processed") return "success" as const;
  if (state === "failed") return "destructive" as const;
  if (state === "processing") return "warning" as const;
  return "muted" as const;
}

export function EvidencePanel({
  evidence,
  onUpload,
}: {
  evidence: EvidenceArtifact[];
  onUpload?: (files: File[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add system evidence</CardTitle>
          <CardDescription>
            Upload available documents, screenshots, diagrams, policies, and
            structured exports.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <input
            ref={inputRef}
            className="sr-only"
            type="file"
            multiple
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              if (files.length > 0) onUpload?.(files);
              event.target.value = "";
            }}
          />
          <Button
            type="button"
            disabled={!onUpload}
            onClick={() => inputRef.current?.click()}
          >
            <FileUp aria-hidden="true" />
            Select files
          </Button>
          <p className="mt-2 text-xs text-muted-foreground">
            Files remain visible if processing fails.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Evidence inventory</CardTitle>
          <CardDescription>{evidence.length} uploaded artifacts</CardDescription>
        </CardHeader>
        <CardContent>
          {evidence.length === 0 ? (
            <p className="rounded-sm border border-dashed p-5 text-sm text-muted-foreground">
              No evidence has been uploaded.
            </p>
          ) : (
            <ul className="divide-y rounded-sm border">
              {evidence.map((artifact) => (
                <li
                  key={artifact.id}
                  className="flex flex-wrap items-center justify-between gap-3 p-3"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    {artifact.mediaType.startsWith("image/") ? (
                      <Image className="size-4 shrink-0" aria-hidden="true" />
                    ) : artifact.state === "processing" ? (
                      <LoaderCircle
                        className="size-4 shrink-0 animate-spin"
                        aria-hidden="true"
                      />
                    ) : artifact.state === "failed" ? (
                      <TriangleAlert
                        className="size-4 shrink-0 text-destructive"
                        aria-hidden="true"
                      />
                    ) : (
                      <FileUp className="size-4 shrink-0" aria-hidden="true" />
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{artifact.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {artifact.mediaType} · {artifact.uploadedAt}
                      </p>
                      {artifact.error ? (
                        <p className="mt-1 text-xs text-destructive">
                          {artifact.error}
                        </p>
                      ) : null}
                    </div>
                  </div>
                  <Badge variant={stateVariant(artifact.state)}>
                    {evidenceStateLabel(artifact.state)}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
