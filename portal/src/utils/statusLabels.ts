import type { BadgeProps } from "@/components/ui/badge";

export function revisionStatusLabel(status: string): string {
  return status.replaceAll("_", " ");
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
    case "failed":
    case "cancelled":
      return "destructive";
    default:
      return "muted";
  }
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
      return "destructive";
    default:
      return "muted";
  }
}
