import { Badge } from "@/components/ui/badge";

const INTAKE_STAGE_LABELS: Record<string, string> = {
  uploading: "Waiting for upload finalization",
  scanning: "Scanning uploaded artifacts",
  extracting: "Extracting and mapping package content",
};

type IntakeProgressPanelProps = {
  status: string;
};

export function IntakeProgressPanel({ status }: IntakeProgressPanelProps) {
  const label = INTAKE_STAGE_LABELS[status] ?? "Processing revision";

  return (
    <div
      aria-live="polite"
      className="space-y-3 rounded-md border border-primary/20 bg-primary/5 px-4 py-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="muted">{status}</Badge>
        <span className="text-sm font-medium">{label}</span>
      </div>
      <p className="text-sm text-muted-foreground">
        The intake worker is preparing an editable package draft. This page refreshes
        automatically while scanning or extraction is in progress.
      </p>
    </div>
  );
}
