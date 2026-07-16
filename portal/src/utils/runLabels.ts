import type { AnalysisRun } from "@/types";
import { toTitleCaseWords } from "@/utils/labelFormatting";

export function runTypeLabel(runType: string): string {
  switch (runType) {
    case "deterministic_only":
      return "Deterministic";
    case "targeted":
      return "Targeted";
    case "full":
      return "Full";
    default:
      return toTitleCaseWords(runType.replaceAll("_", " "));
  }
}

export function formatRunRequestedAt(requestedAt: string): string {
  const timestamp = new Date(requestedAt);
  if (Number.isNaN(timestamp.getTime())) {
    return "Unknown time";
  }
  return timestamp.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRunListLabel(run: Pick<AnalysisRun, "run_type" | "requested_at">): string {
  return `${runTypeLabel(run.run_type)} · ${formatRunRequestedAt(run.requested_at)}`;
}
