import type { BadgeProps } from "@/components/ui/badge";
import { toTitleCaseWords } from "@/utils/labelFormatting";

export function revisionStatusLabel(status: string): string {
  if (status === "ready") {
    return "Sealed — ready for analysis";
  }
  return toTitleCaseWords(status);
}

export function packagePreparationStatusLabel(status: string): string {
  switch (status) {
    case "in_progress":
      return "In progress";
    case "ready_for_external_review":
      return "Ready for external review";
    default:
      return toTitleCaseWords(status);
  }
}

export function revisionStatusVariant(
  status: string,
): NonNullable<BadgeProps["variant"]> {
  switch (status) {
    case "ready":
      return "success";
    case "awaiting_confirmation":
    case "queued":
    case "running":
      return "warning";
    case "scanning":
    case "extracting":
    case "uploading":
      return "secondary";
    case "invalid":
    case "quarantined":
    case "failed":
    case "cancelled":
      return "destructive";
    case "archived":
      return "muted";
    default:
      return "muted";
  }
}

export function runStatusLabel(status: string): string {
  return toTitleCaseWords(status);
}

export function runStatusVariant(
  status: string,
): NonNullable<BadgeProps["variant"]> {
  switch (status) {
    case "succeeded":
      return "success";
    case "queued":
    case "running":
      return "warning";
    case "failed":
    case "cancelled":
    case "policy_blocked":
      return "destructive";
    default:
      return "muted";
  }
}

export function runFailureMessage(status: string, errorCode?: string | null): string {
  if (status === "cancelled") {
    return "Run was cancelled before completion.";
  }
  if (status === "policy_blocked") {
    return errorCode
      ? `Run blocked by policy (${errorCode}).`
      : "Run blocked by policy before model execution.";
  }
  if (status === "failed") {
    return errorCode ? `Run failed (${errorCode}).` : "Run failed before producing matrix output.";
  }
  return `Run status: ${status}`;
}
