import type {
  ControlStatement,
  EvidenceArtifact,
  SspSection,
  SspWorkspace,
} from "@/sspWorkspaceTypes";

const SCREENSHOT_MEDIA_TYPES = new Set([
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

function isDrafted(control: ControlStatement): boolean {
  return (
    control.statement.trim().length > 0 &&
    control.state !== "empty"
  );
}

function isPartial(control: ControlStatement): boolean {
  return (
    control.state === "partial" ||
    !isDrafted(control) ||
    control.evidenceLinks.length === 0 ||
    Boolean(control.unresolvedReason?.trim())
  );
}

function satisfiedRequiredIds(sections: SspSection[]): Set<string> {
  return new Set(
    sections.flatMap((section) => section.satisfiedRequirementIds),
  );
}

export type SspWorkspaceMetrics = {
  evidence: number;
  processedEvidence: number;
  screenshots: number;
  selectedControls: number;
  controlsDrafted: number;
  partialControls: number;
  openQuestions: number;
  evidenceLinks: number;
  requiredItems: number;
  satisfiedRequiredItems: number;
  sspCompletion: number;
  approved: boolean;
  requiredItemsResolved: boolean;
  controlsResolved: boolean;
  reviewable: boolean;
};

export function calculateSspWorkspaceMetrics({
  requirements,
  evidence,
  sections,
  controls,
  questions,
  processingJobsTerminal,
  revisionSaved,
  internallyConsistent,
  currentContentHash,
  approvedContentHash,
}: Pick<
  SspWorkspace,
  | "requirements"
  | "evidence"
  | "sections"
  | "controls"
  | "questions"
  | "processingJobsTerminal"
  | "revisionSaved"
  | "internallyConsistent"
  | "currentContentHash"
  | "approvedContentHash"
>): SspWorkspaceMetrics {
  const required = requirements.filter((requirement) => requirement.required);
  const satisfiedIds = satisfiedRequiredIds(sections);
  const satisfiedRequiredItems = required.filter((requirement) =>
    satisfiedIds.has(requirement.id),
  ).length;
  const openQuestions = questions.filter(
    (question) => question.state === "open",
  );
  const controlsWithResolution = controls.every(
    (control) =>
      isDrafted(control) ||
      openQuestions.some(
        (question) =>
          question.targetType === "control" &&
          question.targetId === control.id,
      ) ||
      Boolean(control.unresolvedReason?.trim()),
  );
  const requiredItemsResolved = required.every(
    (requirement) =>
      satisfiedIds.has(requirement.id) ||
      openQuestions.some(
        (question) =>
          question.targetType === "ssp_section" &&
          sections.some(
            (section) =>
              section.id === question.targetId &&
              section.requirementIds.includes(requirement.id),
          ),
      ),
  );

  return {
    evidence: evidence.length,
    processedEvidence: evidence.filter(
      (artifact) => artifact.state === "processed",
    ).length,
    screenshots: evidence.filter((artifact) =>
      SCREENSHOT_MEDIA_TYPES.has(artifact.mediaType.toLowerCase()),
    ).length,
    selectedControls: controls.length,
    controlsDrafted: controls.filter(isDrafted).length,
    partialControls: controls.filter(isPartial).length,
    openQuestions: openQuestions.length,
    evidenceLinks:
      sections.reduce(
        (total, section) => total + section.evidenceLinks.length,
        0,
      ) +
      controls.reduce(
        (total, control) => total + control.evidenceLinks.length,
        0,
      ),
    requiredItems: required.length,
    satisfiedRequiredItems,
    sspCompletion:
      required.length === 0
        ? 0
        : Math.round((100 * satisfiedRequiredItems) / required.length),
    approved:
      Boolean(approvedContentHash) &&
      approvedContentHash === currentContentHash,
    requiredItemsResolved,
    controlsResolved: controlsWithResolution,
    reviewable:
      processingJobsTerminal &&
      requiredItemsResolved &&
      controlsWithResolution &&
      revisionSaved &&
      internallyConsistent,
  };
}

export function evidenceStateLabel(state: EvidenceArtifact["state"]): string {
  return state.charAt(0).toUpperCase() + state.slice(1);
}
