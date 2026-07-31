export type EvidenceState = "uploaded" | "processing" | "processed" | "failed";
export type SspSectionState = "empty" | "generated" | "edited" | "reviewed";
export type ControlStatementState =
  | "empty"
  | "generated"
  | "partial"
  | "reviewed";
export type QuestionState = "open" | "answered" | "dismissed";
export type AgentPatchState = "proposed" | "applied" | "rejected" | "stale";
export type ImpactLevel = "low" | "moderate" | "high";

export type AgencyDocxRenderStatus =
  | "awaiting_approval"
  | "review_failed"
  | "approved"
  | "rejected";

export type AgencyDocxIssueSeverity = "blocker" | "warning";

export type AgencyDocxMappingException = {
  severity: AgencyDocxIssueSeverity;
  code: string;
  message: string;
};

export type AgencyDocxIssue = {
  severity: AgencyDocxIssueSeverity;
  code: string;
  message: string;
  locator: string | null;
};

export type AgencyDocxRender = {
  id: string;
  profileVersionId: string;
  sourceRevisionId: string;
  sourceRevisionSha256: string;
  templateSha256: string;
  templateFilename: string;
  outputSha256: string;
  status: AgencyDocxRenderStatus;
  createdBy: string;
  createdAt: string;
  resolvedBy: string | null;
  resolvedAt: string | null;
  mappingSummary: string;
  mappingExceptions: AgencyDocxMappingException[];
  reviewSummary: string;
  reviewIssues: AgencyDocxIssue[];
  canApprove: boolean;
  canPreview: boolean;
  canDownload: boolean;
};

export type EvidenceLink = {
  id: string;
  artifactId: string;
  locator: string;
};

export type EvidenceArtifact = {
  id: string;
  name: string;
  mediaType: string;
  state: EvidenceState;
  uploadedAt: string;
  error?: string | null;
};

export type SspRequirement = {
  id: string;
  label: string;
  required: boolean;
};

export type SspSection = {
  id: string;
  title: string;
  content: string;
  state: SspSectionState;
  requirementIds: string[];
  satisfiedRequirementIds: string[];
  evidenceLinks: EvidenceLink[];
};

export type ControlStatement = {
  id: string;
  title: string;
  family: string;
  state: ControlStatementState;
  implementationStatus: string;
  responsibility: string;
  statement: string;
  evidenceLinks: EvidenceLink[];
  unresolvedReason?: string | null;
};

export type WorkspaceQuestion = {
  id: string;
  targetType: "workspace" | "ssp_section" | "control";
  targetId: string;
  prompt: string;
  owner: string;
  state: QuestionState;
};

export type AgentContext = {
  targetType: "workspace" | "ssp_section" | "control";
  targetId: string;
  label: string;
};

export type AgentPatch = {
  id: string;
  summary: string;
  state: AgentPatchState;
  targetLabels: string[];
};

export type ProfileSummary = {
  id: string;
  name: string;
  version: string;
  baseline: "Low" | "Moderate" | "High" | "Unconfirmed";
};

export type ControlResponseOptions = {
  implementationStatuses: string[];
  responsibilities: string[];
  questionOwnerTypes: string[];
  evidenceRequiredForAgentStatement: boolean;
};

/** Client-side defaults when older envelopes omit control_response. */
export const DEFAULT_CONTROL_RESPONSE_OPTIONS: ControlResponseOptions = {
  implementationStatuses: [
    "implemented",
    "partially_implemented",
    "planned",
    "not_implemented",
    "not_applicable",
    "unknown",
  ],
  responsibilities: ["system_specific", "hybrid", "inherited", "unknown"],
  questionOwnerTypes: ["isso", "agency", "technical", "system_owner"],
  evidenceRequiredForAgentStatement: true,
};

export type SystemCategorization = {
  confidentiality: ImpactLevel | "";
  integrity: ImpactLevel | "";
  availability: ImpactLevel | "";
  confidentialityRationale: string;
  integrityRationale: string;
  availabilityRationale: string;
  confirmed: boolean;
};

export type SspWorkspace = {
  id: string;
  name: string;
  purpose: string;
  hosting: string;
  impactLevel: string;
  provisionalImpactLevel: ImpactLevel;
  categorization: SystemCategorization;
  authorizationPath: string;
  profile: ProfileSummary;
  controlResponse: ControlResponseOptions;
  revisionId: string;
  revisionUpdatedAt: string;
  lastAgentUpdateAt?: string | null;
  currentContentHash: string;
  approvedContentHash?: string | null;
  processingJobsTerminal: boolean;
  revisionSaved: boolean;
  internallyConsistent: boolean;
  requirements: SspRequirement[];
  evidence: EvidenceArtifact[];
  sections: SspSection[];
  controls: ControlStatement[];
  questions: WorkspaceQuestion[];
  patches: AgentPatch[];
  agencyDocxRenders: AgencyDocxRender[];
};

export type SspSectionChange = {
  sectionId: string;
  content: string;
};

export type ControlStatementChange = {
  controlId: string;
  implementationStatus: string;
  responsibility: string;
  statement: string;
};

export type QuestionAnswer = {
  questionId: string;
  answer: string;
};

export type CategorizationChange = Omit<SystemCategorization, "confirmed">;

export type SspWorkspaceActions = {
  onRetry?: () => void;
  onCreateWorkspace?: () => void;
  onOpenWorkspace?: (workspaceId: string) => void;
  onNewWorkspace?: () => void;
  onUploadEvidence?: (files: File[]) => void;
  onRemoveEvidence?: (artifactId: string) => void;
  onGenerate?: () => void;
  onSaveSection?: (change: SspSectionChange) => void;
  onSaveControl?: (change: ControlStatementChange) => void;
  onAnswerQuestion?: (change: QuestionAnswer) => void;
  onSaveCategorization?: (change: CategorizationChange) => void;
  onAskAgent?: (context: AgentContext, message: string) => void;
  onApplyPatch?: (patchId: string) => void;
  onRejectPatch?: (patchId: string) => void;
  onApprove?: () => void;
  onExport?: (format: "docx" | "json" | "oscal-json") => void;
  onUploadAgencyTemplate?: (file: File) => void;
  onPreviewAgencyRender?: (renderId: string) => void;
  onApproveAgencyRender?: (renderId: string) => void;
  onRejectAgencyRender?: (renderId: string) => void;
  onDownloadAgencyRender?: (renderId: string) => void;
};
