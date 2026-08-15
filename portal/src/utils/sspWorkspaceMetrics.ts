import type {
  ControlStatement,
  EvidenceArtifact,
  InformationTypes,
  SspSection,
  SspWorkspace,
  SystemDefinition,
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

/** Mirror backend agent_control_blocks_approval for ISSO review gating. */
export function agentControlBlocksApproval(
  control: ControlStatement,
  evidenceRequired: boolean,
): boolean {
  if (!evidenceRequired) {
    return false;
  }
  if (control.state === "reviewed") {
    return false;
  }
  if (control.state !== "generated" && control.state !== "partial") {
    return false;
  }
  const status = (control.implementationStatus || "unknown").trim().toLowerCase();
  const responsibility = (control.responsibility || "unknown").trim().toLowerCase();
  const hasAgentMetadata =
    control.statement.trim().length > 0 ||
    status !== "unknown" ||
    responsibility !== "unknown";
  return hasAgentMetadata && control.evidenceLinks.length === 0;
}

function satisfiedRequiredIds(sections: SspSection[]): Set<string> {
  return new Set(
    sections.flatMap((section) => section.satisfiedRequirementIds),
  );
}

/** Mirror backend approval gate for structured system definition status. */
export function systemDefinitionConfirmed(
  systemDefinition: SystemDefinition,
): boolean {
  return systemDefinition.status === "confirmed";
}

export function systemDefinitionStale(
  systemDefinition: SystemDefinition,
): boolean {
  return systemDefinition.status === "stale";
}

/** Mirror backend approval gate for structured information type mapping status. */
export function informationTypesConfirmed(
  informationTypes: InformationTypes,
): boolean {
  return informationTypes.status === "confirmed";
}

export function informationTypesStale(
  informationTypes: InformationTypes,
): boolean {
  return informationTypes.status === "stale";
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
  agentControlsGrounded: boolean;
  categorizationConfirmed: boolean;
  categorizationStale: boolean;
  systemDefinitionConfirmed: boolean;
  systemDefinitionStale: boolean;
  informationTypesConfirmed: boolean;
  informationTypesStale: boolean;
  informationTypesCount: number;
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
  controlResponse,
  categorization,
  systemDefinition,
  informationTypes,
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
  | "controlResponse"
  | "categorization"
  | "systemDefinition"
  | "informationTypes"
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
  const agentControlsGrounded = !controls.some((control) =>
    agentControlBlocksApproval(
      control,
      controlResponse.evidenceRequiredForAgentStatement,
    ),
  );
  const definitionConfirmed = systemDefinitionConfirmed(systemDefinition);
  const definitionStale = systemDefinitionStale(systemDefinition);
  const typesConfirmed = informationTypesConfirmed(informationTypes);
  const typesStale = informationTypesStale(informationTypes);

  const categorizationConfirmed = categorization.status === "confirmed";
  const categorizationStale = categorization.status === "stale";

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
    agentControlsGrounded,
    categorizationConfirmed,
    categorizationStale,
    systemDefinitionConfirmed: definitionConfirmed,
    systemDefinitionStale: definitionStale,
    informationTypesConfirmed: typesConfirmed,
    informationTypesStale: typesStale,
    informationTypesCount: informationTypes.entries.length,
    reviewable:
      processingJobsTerminal &&
      requiredItemsResolved &&
      controlsWithResolution &&
      agentControlsGrounded &&
      categorizationConfirmed &&
      !categorizationStale &&
      definitionConfirmed &&
      !definitionStale &&
      typesConfirmed &&
      !typesStale &&
      revisionSaved &&
      internallyConsistent,
  };
}

export function evidenceStateLabel(state: EvidenceArtifact["state"]): string {
  return state.charAt(0).toUpperCase() + state.slice(1);
}
