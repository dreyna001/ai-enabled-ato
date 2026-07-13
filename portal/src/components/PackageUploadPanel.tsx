import { forwardRef, useRef, useState, type InputHTMLAttributes } from "react";
import { Upload } from "lucide-react";
import { uploadPackageFile } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  ARTIFACT_KIND_LABELS,
  type ArtifactKind,
  inferArtifactKind,
  isArtifactKind,
  UPLOAD_ACCEPT,
} from "@/utils/artifactKinds";
import { formatApiError } from "@/utils/formatApiError";
import type { SessionInfo } from "@/types";

type PackageUploadPanelProps = {
  session: SessionInfo;
  revisionId: string;
  onUploaded: () => void;
  onFinalized: () => void;
  onFinalize: () => Promise<void>;
  finalizing: boolean;
};

type PendingUpload = {
  file: File;
  artifactKind: ArtifactKind;
  status: "pending" | "uploading" | "done" | "error";
  error?: string;
};

const InputLike = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  (props, ref) => <input ref={ref} type="file" className="sr-only" {...props} />,
);
InputLike.displayName = "InputLike";

export function PackageUploadPanel({
  session,
  revisionId,
  onUploaded,
  onFinalized,
  onFinalize,
  finalizing,
}: PackageUploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [queue, setQueue] = useState<PendingUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [panelError, setPanelError] = useState("");

  const enqueueFiles = (files: FileList | File[]) => {
    const next = Array.from(files).map((file) => ({
      file,
      artifactKind: inferArtifactKind(file),
      status: "pending" as const,
    }));
    setQueue((current) => [...current, ...next]);
    setPanelError("");
  };

  const uploadAll = async () => {
    const pending = queue.filter((item) => item.status === "pending");
    if (pending.length === 0) {
      return;
    }
    setUploading(true);
    setPanelError("");
    let hadError = false;
    for (const item of pending) {
      setQueue((current) =>
        current.map((entry) =>
          entry.file === item.file ? { ...entry, status: "uploading" } : entry,
        ),
      );
      try {
        await uploadPackageFile(session, revisionId, item.file, item.artifactKind);
        setQueue((current) =>
          current.map((entry) =>
            entry.file === item.file ? { ...entry, status: "done" } : entry,
          ),
        );
        onUploaded();
      } catch (err) {
        hadError = true;
        const message = formatApiError(err);
        setQueue((current) =>
          current.map((entry) =>
            entry.file === item.file
              ? { ...entry, status: "error", error: message }
              : entry,
          ),
        );
      }
    }
    setUploading(false);
    if (hadError) {
      setPanelError("One or more uploads failed. Fix errors and retry pending files.");
    }
  };

  const updateKind = (file: File, artifactKind: ArtifactKind) => {
    setQueue((current) =>
      current.map((entry) =>
        entry.file === file ? { ...entry, artifactKind, status: "pending" } : entry,
      ),
    );
  };

  const removeEntry = (file: File) => {
    setQueue((current) => current.filter((entry) => entry.file !== file));
  };

  const pendingCount = queue.filter((item) => item.status === "pending").length;
  const doneCount = queue.filter((item) => item.status === "done").length;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="package-upload">Upload package files</Label>
        <p className="text-sm text-muted-foreground">
          Add JSON, PDF, DOCX, XLSX, scanner exports, OSCAL baselines, and other
          supported evidence. Select an artifact kind per file when needed.
        </p>
        <InputLike
          ref={inputRef}
          id="package-upload"
          accept={UPLOAD_ACCEPT}
          multiple
          onChange={(event) => {
            const files = event.target.files;
            if (files && files.length > 0) {
              enqueueFiles(files);
            }
            event.target.value = "";
          }}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => inputRef.current?.click()}
          >
            <Upload className="size-4" />
            Choose files
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={pendingCount === 0 || uploading}
            onClick={() => void uploadAll()}
          >
            {uploading
              ? "Uploading…"
              : `Upload ${pendingCount || ""} file${pendingCount === 1 ? "" : "s"}`}
          </Button>
        </div>
      </div>

      {queue.length > 0 ? (
        <ul className="space-y-2 rounded-md border bg-muted/20 p-3">
          {queue.map((item) => (
            <li
              key={`${item.file.name}-${item.file.size}-${item.file.lastModified}`}
              className="flex flex-wrap items-center gap-2 text-sm"
            >
              <span className="min-w-0 flex-1 truncate font-medium">{item.file.name}</span>
              <select
                className="rounded-md border bg-background px-2 py-1 text-xs"
                value={item.artifactKind}
                disabled={item.status === "uploading" || item.status === "done"}
                onChange={(event) => {
                  const value = event.target.value;
                  if (isArtifactKind(value)) {
                    updateKind(item.file, value);
                  }
                }}
              >
                {Object.entries(ARTIFACT_KIND_LABELS).map(([kind, label]) => (
                  <option key={kind} value={kind}>
                    {label}
                  </option>
                ))}
              </select>
              <Badge variant={item.status === "error" ? "destructive" : "muted"}>
                {item.status}
              </Badge>
              {item.status !== "uploading" && item.status !== "done" ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => removeEntry(item.file)}
                >
                  Remove
                </Button>
              ) : null}
              {item.error ? (
                <span className="w-full text-xs text-destructive">{item.error}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {panelError ? <p className="text-sm text-destructive">{panelError}</p> : null}

      <div className="space-y-2 border-t pt-4">
        <p className="text-sm text-muted-foreground">
          {doneCount > 0
            ? `${doneCount} file${doneCount === 1 ? "" : "s"} uploaded. Finalize when all required artifacts are present.`
            : "Upload at least one artifact, then finalize to start scanning and extraction."}
        </p>
        <Button
          type="button"
          disabled={doneCount === 0 || uploading || finalizing}
          onClick={() => {
            void onFinalize()
              .then(() => onFinalized())
              .catch((err) => setPanelError(formatApiError(err)));
          }}
        >
          {finalizing ? "Finalizing…" : "Finalize upload"}
        </Button>
      </div>
    </div>
  );
}
