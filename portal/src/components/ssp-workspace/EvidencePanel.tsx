import {
  FileUp,
  Image,
  LoaderCircle,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useRef, useState } from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
  onRemove,
  removalAllowed = false,
}: {
  evidence: EvidenceArtifact[];
  onUpload?: (files: File[]) => void;
  onRemove?: (artifactId: string) => void;
  removalAllowed?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [pendingRemoval, setPendingRemoval] =
    useState<EvidenceArtifact | null>(null);

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
                  <div className="flex items-center gap-2">
                    <Badge variant={stateVariant(artifact.state)}>
                      {evidenceStateLabel(artifact.state)}
                    </Badge>
                    {removalAllowed && onRemove ? (
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        aria-label={`Remove ${artifact.name}`}
                        onClick={() => setPendingRemoval(artifact)}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {evidence.length > 0 && !removalAllowed ? (
            <p className="mt-3 text-xs text-muted-foreground">
              Evidence cannot be removed after analysis has started.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={pendingRemoval !== null}
        title="Remove evidence?"
        description={
          pendingRemoval
            ? `Remove "${pendingRemoval.name}" from this workspace?`
            : ""
        }
        confirmLabel="Remove evidence"
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          if (!pendingRemoval) return;
          onRemove?.(pendingRemoval.id);
          setPendingRemoval(null);
        }}
      />
    </div>
  );
}
