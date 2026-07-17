import { Badge } from "@/components/ui/badge";
import { revisionStatusLabel } from "@/utils/statusLabels";

const INTAKE_STAGE_LABELS: Record<string, string> = {
  uploading: "Waiting For Upload Finalization",
  scanning: "Scanning Uploaded Artifacts",
  extracting: "Extracting and Mapping Package Content",
};

type IntakeProgressPanelProps = {
  status: string;
};

export function IntakeProgressPanel({ status }: IntakeProgressPanelProps) {
  const label = INTAKE_STAGE_LABELS[status] ?? "Processing Revision";

  return (
    <div
      aria-live="polite"
      className="space-y-3 rounded-sm border border-primary/20 bg-primary/5 px-4 py-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="muted">{revisionStatusLabel(status)}</Badge>
        <span className="text-sm font-medium">{label}</span>
      </div>
      <p className="text-sm text-muted-foreground">
        The intake worker is preparing an editable package draft. This page refreshes
        automatically while scanning or extraction is in progress.
      </p>
    </div>
  );
}
