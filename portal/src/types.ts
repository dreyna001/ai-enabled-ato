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
