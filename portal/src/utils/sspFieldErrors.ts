import type { ProblemFieldError } from "@/api/client";
import type {
  CategorizationChange,
  EvidenceArtifact,
  EvidenceLink,
  SystemCategorization,
} from "@/sspWorkspaceTypes";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const CATEGORIZATION_ATTESTATION_LOCATOR = {
  kind: "categorization_attestation",
} as const;

const FIELD_LABELS: Record<string, string> = {
  confidentiality: "Confidentiality impact",
  integrity: "Integrity impact",
  availability: "Availability impact",
  confidentiality_rationale: "Confidentiality rationale",
  integrity_rationale: "Integrity rationale",
  availability_rationale: "Availability rationale",
  confidentiality_evidence: "Confidentiality evidence",
  integrity_evidence: "Integrity evidence",
  availability_evidence: "Availability evidence",
  expected_revision_id: "Workspace revision",
};

export function humanizeSspFieldPath(path: string): string {
  const root = path.split(".")[0] ?? path;
  if (FIELD_LABELS[root]) {
    return FIELD_LABELS[root];
  }
  const segment = path.split(".").filter(Boolean).at(-1) ?? path;
  return segment.replace(/_/g, " ");
}

export function formatSspFieldErrors(fieldErrors: ProblemFieldError[]): string {
  if (fieldErrors.length === 0) {
    return "";
  }
  if (fieldErrors.length === 1) {
    const issue = fieldErrors[0];
    return `${humanizeSspFieldPath(issue.path)}: ${issue.message}`;
  }
  return fieldErrors
    .map(
      (issue) =>
        `• ${humanizeSspFieldPath(issue.path)}: ${issue.message}`,
    )
    .join("\n");
}

function parseEvidenceLocator(locator: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(locator) as Record<string, unknown>;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.keys(parsed).length > 0
        ? parsed
        : { ...CATEGORIZATION_ATTESTATION_LOCATOR };
    }
  } catch {
    // fall through
  }
  return { ...CATEGORIZATION_ATTESTATION_LOCATOR };
}

export function normalizeCategorizationEvidenceLinks(
  links: EvidenceLink[],
  processedEvidence: EvidenceArtifact[],
): Array<{ artifact_id: string; locator: Record<string, unknown> }> {
  const allowedIds = new Set(
    processedEvidence
      .filter((artifact) => artifact.state === "processed")
      .map((artifact) => artifact.id),
  );
  const seen = new Set<string>();
  const normalized: Array<{ artifact_id: string; locator: Record<string, unknown> }> =
    [];

  for (const link of links) {
    if (!UUID_PATTERN.test(link.artifactId)) {
      continue;
    }
    if (!allowedIds.has(link.artifactId) || seen.has(link.artifactId)) {
      continue;
    }
    seen.add(link.artifactId);
    normalized.push({
      artifact_id: link.artifactId,
      locator: parseEvidenceLocator(link.locator),
    });
  }

  return normalized;
}

export type CategorizationValidationIssue = {
  field: keyof CategorizationChange | "form";
  message: string;
};

export function hydrateCategorizationValues(
  categorization: Pick<
    SystemCategorization,
    | "confidentiality"
    | "integrity"
    | "availability"
    | "confidentialityRationale"
    | "integrityRationale"
    | "availabilityRationale"
    | "confidentialityEvidence"
    | "integrityEvidence"
    | "availabilityEvidence"
  >,
  processedEvidence: EvidenceArtifact[],
): CategorizationChange {
  const toUiLinks = (links: EvidenceLink[]) =>
    normalizeCategorizationEvidenceLinks(links, processedEvidence).map(
      (item) => ({
        id: `${item.artifact_id}:categorization`,
        artifactId: item.artifact_id,
        locator: JSON.stringify(item.locator),
      }),
    );

  return {
    confidentiality: categorization.confidentiality,
    integrity: categorization.integrity,
    availability: categorization.availability,
    confidentialityRationale: categorization.confidentialityRationale,
    integrityRationale: categorization.integrityRationale,
    availabilityRationale: categorization.availabilityRationale,
    confidentialityEvidence: toUiLinks(categorization.confidentialityEvidence),
    integrityEvidence: toUiLinks(categorization.integrityEvidence),
    availabilityEvidence: toUiLinks(categorization.availabilityEvidence),
  };
}

export function validateCategorizationChange(
  change: CategorizationChange,
  processedEvidence: EvidenceArtifact[],
): CategorizationValidationIssue[] {
  const issues: CategorizationValidationIssue[] = [];
  const impacts: Array<{
    key: "confidentiality" | "integrity" | "availability";
    rationaleKey:
      | "confidentialityRationale"
      | "integrityRationale"
      | "availabilityRationale";
    evidenceKey:
      | "confidentialityEvidence"
      | "integrityEvidence"
      | "availabilityEvidence";
    label: string;
  }> = [
    {
      key: "confidentiality",
      rationaleKey: "confidentialityRationale",
      evidenceKey: "confidentialityEvidence",
      label: "Confidentiality",
    },
    {
      key: "integrity",
      rationaleKey: "integrityRationale",
      evidenceKey: "integrityEvidence",
      label: "Integrity",
    },
    {
      key: "availability",
      rationaleKey: "availabilityRationale",
      evidenceKey: "availabilityEvidence",
      label: "Availability",
    },
  ];

  for (const { key, rationaleKey, evidenceKey, label } of impacts) {
    if (!["low", "moderate", "high"].includes(change[key])) {
      issues.push({
        field: key,
        message: `${label}: select low, moderate, or high.`,
      });
    }
    if (!change[rationaleKey].trim()) {
      issues.push({
        field: rationaleKey,
        message: `${label}: enter a rationale.`,
      });
    }
    if (
      normalizeCategorizationEvidenceLinks(
        change[evidenceKey],
        processedEvidence,
      ).length === 0
    ) {
      issues.push({
        field: evidenceKey,
        message: `${label}: select at least one processed artifact.`,
      });
    }
  }

  return issues;
}
