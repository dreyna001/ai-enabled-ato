import type { EmptyStateAction } from "@/components/EmptyState";

export function resolveSystemsEmptyState(): {
  title: string;
  description: string;
  action?: EmptyStateAction;
} {
  return {
    title: "No Systems Yet",
    description:
      "Create a system to start a synthetic Package Revision and run the Intake Workflow.",
  };
}

export function resolveRevisionsEmptyState(): {
  title: string;
  description: string;
} {
  return {
    title: "No Package Revisions",
    description:
      "Create a revision for the selected system to upload synthetic JSON evidence.",
  };
}

export function resolveRunsEmptyState(): {
  title: string;
  description: string;
} {
  return {
    title: "No Analysis Runs",
    description:
      "Start a deterministic run after the revision reaches Ready status.",
  };
}
