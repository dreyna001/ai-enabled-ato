export type SessionInfo = {
  actor_id: string;
  groups: string[];
  csrf_token: string;
  portal_origin: string;
};

export type System = {
  system_id: string;
  display_name: string;
  owner_group: string;
  viewer_groups: string[];
};

export type PackageRevision = {
  package_revision_id: string;
  system_id: string;
  status: string;
  revision_version: number;
  profile_id: string;
  data_origin: string;
  sensitivity: string;
};

export type FactProposal = {
  fact_proposal_id: string;
  json_pointer: string;
  proposed_value: unknown;
  review_status: string;
};

export type ProfileId =
  | "fedramp_20x_program"
  | "fedramp_rev5_transition"
  | "fisma_agency_security";

export type ExtractionMethod =
  | "deterministic"
  | "text"
  | "vision"
  | "llm_normalize";

export type FieldProvenanceEntry = {
  source_artifact_id: string;
  source_sha256: string;
  source_locator: Record<string, unknown>;
  extraction_method: ExtractionMethod;
  model_step_id?: string | null;
};

export type FieldProvenanceMap = Record<string, FieldProvenanceEntry>;

export type SecurityControlEntry = {
  implementation_status: string;
  implementation_statement: string;
  responsible_parties: string[];
  evidence_links: string[];
};

export type PackageDraftDocument = {
  package: {
    profile_id: ProfileId;
    title: string;
    prepared_for: string;
    reporting_period: string | null;
  };
  system: {
    display_name: string;
    authorization_boundary: string;
    mission_summary: string;
    impact_level: string | null;
    authorization_path: string;
  };
  contacts: {
    system_owner: Array<{
      name: string;
      role: string;
      email: string;
      organization?: string;
      phone?: string;
    }>;
    isso: Array<{ name: string; role: string; email: string }>;
    issm: Array<{ name: string; role: string; email: string }>;
    control_owners: Array<{ name: string; role: string; email: string }>;
    assessors: Array<{ name: string; role: string; email: string }>;
    approvers: Array<{ name: string; role: string; email: string }>;
  };
  control_set: {
    source: Record<string, unknown>;
    tailoring: unknown[];
    organization_defined_parameters: Record<string, unknown>;
    inheritance: unknown[];
  };
  security_controls: Record<string, SecurityControlEntry>;
  evidence: Record<string, Record<string, unknown>>;
  findings: Record<string, Record<string, unknown>>;
  poam_candidates: Record<string, Record<string, unknown>>;
  assessor_inputs: Record<string, Record<string, unknown>>;
  privacy: {
    artifacts_present: boolean;
    scope_notice: string;
  };
  fedramp_20x: Record<string, unknown> | null;
  fedramp_rev5_transition: Record<string, unknown> | null;
  fisma_agency_security: Record<string, unknown> | null;
  extensions: Record<string, unknown>;
};

export type PackageRevisionDraft = {
  schema_version: string;
  object_type: "package_revision_draft";
  package_revision_id: string;
  document_schema_version: string;
  document: PackageDraftDocument;
  field_provenance: FieldProvenanceMap;
  updated_by: string;
  updated_at: string;
  revision_version: number;
};

export type AnalysisRun = {
  run_id: string;
  package_revision_id: string;
  run_type: string;
  status: string;
  llm_call_count: number;
  artifact_manifest_sha256: string | null;
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type MatrixRow = {
  matrix_row_id: string;
  assessment_item_id: string;
  assessment_item_type: string;
  model_proposed_status: string;
  system_status: string;
  finding_summary: string;
};

export type PreflightResult = {
  analysis_eligible: boolean;
  export_eligible: boolean;
  analysis_blockers: string[];
  export_blockers: string[];
  warnings: string[];
  readiness: {
    numerator: number;
    denominator: number;
    score: number;
  };
};

export type Disposition = {
  matrix_row_id: string;
  decision: string;
  edited_summary: string | null;
  notes: string | null;
  version: number;
  decided_by: string;
  decided_at: string;
};

export type ReviewRevision = {
  review_revision_id: string;
  run_id: string;
  version: number;
  status: string;
  dispositions: Disposition[];
};

export type ExportDraft = {
  export_draft_id: string;
  review_revision_id: string;
  payload_manifest_sha256: string;
  status: string;
};

export type Approval = {
  approval_id: string;
  export_draft_id: string;
  payload_manifest_sha256: string;
  submitted_by: string;
  decided_by: string | null;
  decision: string;
  expires_at: string;
};

export type ReadinessCheck = {
  name: string;
  status: string;
};

export type ReadinessResponse = {
  status: string;
  checks?: Record<string, string>;
  error_code?: string;
  detail?: string;
};

export type PortalReadinessState = {
  loaded: boolean;
  ready: boolean;
  degraded: boolean;
  error: string | null;
  checks: ReadinessCheck[];
};
