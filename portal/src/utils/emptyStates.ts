import type { EmptyStateAction } from "@/components/EmptyState";

export function resolveSystemsEmptyState(): {
  title: string;
  description: string;
  action?: EmptyStateAction;
} {
  return {
    title: "No systems yet",
    description:
      "Create a system to start a synthetic package revision and run the intake workflow.",
  };
}

export function resolveRevisionsEmptyState(): {
  title: string;
  description: string;
} {
  return {
    title: "No package revisions",
    description:
      "Create a revision for the selected system to upload synthetic JSON evidence.",
  };
}

export function resolveRunsEmptyState(): {
  title: string;
  description: string;
} {
  return {
    title: "No analysis runs",
    description:
      "Start a deterministic run after the revision reaches ready status.",
  };
}
