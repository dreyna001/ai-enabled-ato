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

export type CategorizationAgentSuggestion = {
  confidentiality: boolean;
  integrity: boolean;
  availability: boolean;
};

export type SystemCategorization = {
  confidentiality: ImpactLevel | "";
  integrity: ImpactLevel | "";
  availability: ImpactLevel | "";
  confidentialityRationale: string;
  integrityRationale: string;
  availabilityRationale: string;
  confidentialityEvidence: EvidenceLink[];
  integrityEvidence: EvidenceLink[];
  availabilityEvidence: EvidenceLink[];
  status: "unconfirmed" | "confirmed" | "stale";
  confirmed: boolean;
  agentSuggestion: CategorizationAgentSuggestion | null;
};

export type SystemDefinitionStatus = "unconfirmed" | "confirmed" | "stale";

export type BoundaryPlacement = "inside" | "outside" | "crossing";

export type InterconnectionDirection = "inbound" | "outbound" | "bidirectional";

export type SystemDefinitionEvidenceRef = {
  artifactId: string;
  locator: Record<string, unknown>;
};

export type SystemDefinitionDiagramLink = SystemDefinitionEvidenceRef & {
  label?: string;
};

export type SystemDefinitionComponent = {
  componentId: string;
  name: string;
  purpose: string;
  placement: BoundaryPlacement;
  evidence: SystemDefinitionEvidenceRef[];
};

export type SystemDefinitionInterconnection = {
  interconnectionId: string;
  connectedOrganization: string;
  connectedSystem: string;
  direction: InterconnectionDirection;
  dataTypes: string[];
  interfaceProtocol: string;
  connectionOwner: string;
  agreementType: string;
  agreementId: string;
  agreementStatus: string;
  agreementExpiration: string;
  boundaryProtections: string;
  evidence: SystemDefinitionEvidenceRef[];
};

export type SystemDefinition = {
  status: SystemDefinitionStatus | null;
  boundaryNarrative: string;
  diagramLinks: SystemDefinitionDiagramLink[];
  components: SystemDefinitionComponent[];
  interconnections: SystemDefinitionInterconnection[];
  proposal: SystemDefinitionProposal | null;
};

export type SystemDefinitionProposalConflict = {
  field: string;
  diagramValue: string;
  textValue: string;
  note: string;
};

export type SystemDefinitionAnalysisStatus =
  | "semantic_analysis_complete"
  | "analysis_failed"
  | "analysis_failure"
  | "ingested"
  | "ocr"
  | "unknown";

export type SystemDefinitionProposal = {
  source: string;
  analysisStatus: SystemDefinitionAnalysisStatus;
  artifactId: string;
  artifactSha256: string;
  displayFilename: string;
  locator: Record<string, unknown>;
  sourceRevisionId?: string;
  componentCount: number | null;
  interconnectionCount: number | null;
  lowConfidenceComponentCount: number | null;
  lowConfidenceInterconnectionCount: number | null;
  conflictCount: number;
  stale: boolean;
  failureKind?: string;
  conflicts: SystemDefinitionProposalConflict[];
};

export type SystemDefinitionChange = {
  boundaryNarrative: string;
  diagramLinks: SystemDefinitionDiagramLink[];
  components: SystemDefinitionComponent[];
  interconnections: SystemDefinitionInterconnection[];
};

export const STRUCTURED_SYSTEM_DEFINITION_SECTION_IDS = new Set([
  "system.authorization_boundary",
  "system.components",
  "system.interconnections",
]);

export const STRUCTURED_INFORMATION_TYPES_SECTION_IDS = new Set([
  "system.data_types",
]);

export type InformationTypesStatus = "unconfirmed" | "confirmed" | "stale";

export type InformationTypeEvidenceRef = {
  artifactId: string;
  locator: Record<string, unknown>;
};

export type InformationTypeMappingEntry = {
  entryId: string;
  catalogIdentifier: string;
  catalogTitle: string;
  description: string;
  catalogConfidentiality: ImpactLevel;
  catalogIntegrity: ImpactLevel;
  catalogAvailability: ImpactLevel;
  adjustedConfidentiality: ImpactLevel | "";
  adjustedIntegrity: ImpactLevel | "";
  adjustedAvailability: ImpactLevel | "";
  adjustmentRationale: string;
  evidence: InformationTypeEvidenceRef[];
};

export type InformationTypes = {
  status: InformationTypesStatus | null;
  entries: InformationTypeMappingEntry[];
  confirmed: boolean;
};

export type InformationTypesChange = {
  entries: InformationTypeMappingEntry[];
};

export type Sp80060CatalogEntry = {
  identifier: string;
  title: string;
  confidentiality: ImpactLevel;
  integrity: ImpactLevel;
  availability: ImpactLevel;
};

export type Sp80060Catalog = {
  sourceId: string;
  title: string;
  version: string;
  reference: string;
  informationTypes: Sp80060CatalogEntry[];
};

export type SspWorkspace = {
  id: string;
  name: string;
  purpose: string;
  hosting: string;
  impactLevel: string;
  provisionalImpactLevel: ImpactLevel;
  categorization: SystemCategorization;
  informationTypes: InformationTypes;
  systemDefinition: SystemDefinition;
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

export type CategorizationChange = Omit<
  SystemCategorization,
  "confirmed" | "status" | "agentSuggestion"
>;

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
  onAnalyzeCategorization?: () => void;
  onSaveInformationTypes?: (change: InformationTypesChange) => void;
  onSaveSystemDefinition?: (change: SystemDefinitionChange) => void;
  onAnalyzeDiagram?: (artifactId: string, pageNumber: number) => void;
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
