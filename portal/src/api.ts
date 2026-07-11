export type Problem = {
  error_code: string;
  status: number;
  detail?: string;
};

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

const API_BASE = "/api/v1";

async function parseProblem(response: Response): Promise<Problem> {
  try {
    return (await response.json()) as Problem;
  } catch {
    return {
      error_code: "malformed_request",
      status: response.status,
      detail: response.statusText,
    };
  }
}

export async function fetchSession(): Promise<SessionInfo | null> {
  const response = await fetch(`${API_BASE}/auth/session`, {
    credentials: "include",
  });
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as SessionInfo;
}

export function login(): void {
  window.location.href = `${API_BASE}/auth/login`;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
}

export async function listSystems(): Promise<System[]> {
  const response = await fetch(`${API_BASE}/systems`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  const payload = (await response.json()) as { items: System[] };
  return payload.items;
}

export async function createSystem(
  session: SessionInfo,
  displayName: string,
): Promise<System> {
  const response = await fetch(`${API_BASE}/systems`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
    },
    body: JSON.stringify({
      display_name: displayName,
      external_system_id: null,
      owner_group: "owners",
      viewer_groups: ["viewers"],
    }),
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as System;
}

export async function listRevisions(systemId: string): Promise<PackageRevision[]> {
  const response = await fetch(
    `${API_BASE}/systems/${systemId}/package-revisions`,
    { credentials: "include" },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  const payload = (await response.json()) as { items: PackageRevision[] };
  return payload.items;
}

export async function createRevision(
  session: SessionInfo,
  systemId: string,
): Promise<PackageRevision> {
  const response = await fetch(
    `${API_BASE}/systems/${systemId}/package-revisions`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
        "X-CSRF-Token": session.csrf_token,
        Origin: session.portal_origin,
      },
      body: JSON.stringify({
        parent_revision_id: null,
        profile_id: "fisma_agency_security",
        certification_class: null,
        impact_level: "moderate",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
      }),
    },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as PackageRevision;
}

export async function getRevision(revisionId: string): Promise<PackageRevision> {
  const response = await fetch(`${API_BASE}/package-revisions/${revisionId}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as PackageRevision;
}

export async function uploadJsonFile(
  session: SessionInfo,
  revisionId: string,
  file: File,
): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  form.append("artifact_kind", "evidence_document");
  const response = await fetch(`${API_BASE}/package-revisions/${revisionId}/files`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
    },
    body: form,
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
}

export async function finalizeRevision(
  session: SessionInfo,
  revisionId: string,
): Promise<PackageRevision> {
  const response = await fetch(
    `${API_BASE}/package-revisions/${revisionId}/finalize`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        "X-CSRF-Token": session.csrf_token,
        Origin: session.portal_origin,
      },
    },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as PackageRevision;
}

export async function listProposals(revisionId: string): Promise<FactProposal[]> {
  const response = await fetch(
    `${API_BASE}/package-revisions/${revisionId}/proposals`,
    { credentials: "include" },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  const payload = (await response.json()) as { items: FactProposal[] };
  return payload.items;
}

export async function acceptProposal(
  session: SessionInfo,
  proposalId: string,
  etag: string,
): Promise<void> {
  const response = await fetch(`${API_BASE}/proposals/${proposalId}/accept`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
      "If-Match": etag,
    },
    body: JSON.stringify({ edited_value: null }),
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
}

export async function rejectProposal(
  session: SessionInfo,
  proposalId: string,
  etag: string,
  reason: string,
): Promise<void> {
  const response = await fetch(`${API_BASE}/proposals/${proposalId}/reject`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
      "If-Match": etag,
    },
    body: JSON.stringify({ reason }),
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
}

export async function confirmRevision(
  session: SessionInfo,
  revisionId: string,
  etag: string,
): Promise<PackageRevision> {
  const response = await fetch(
    `${API_BASE}/package-revisions/${revisionId}/confirm`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        "X-CSRF-Token": session.csrf_token,
        Origin: session.portal_origin,
        "If-Match": etag,
      },
    },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as PackageRevision;
}

export function revisionEtag(revisionVersion: number): string {
  return `"v${revisionVersion}"`;
}

export async function startRun(
  session: SessionInfo,
  revisionId: string,
): Promise<AnalysisRun> {
  const response = await fetch(
    `${API_BASE}/package-revisions/${revisionId}/runs`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
        "X-CSRF-Token": session.csrf_token,
        Origin: session.portal_origin,
      },
      body: JSON.stringify({
        run_type: "deterministic_only",
        parent_run_id: null,
        assessment_item_ids: [],
      }),
    },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as AnalysisRun;
}

export async function listRuns(revisionId: string): Promise<AnalysisRun[]> {
  const response = await fetch(
    `${API_BASE}/package-revisions/${revisionId}/runs`,
    { credentials: "include" },
  );
  if (!response.ok) {
    throw await parseProblem(response);
  }
  const payload = (await response.json()) as { items: AnalysisRun[] };
  return payload.items;
}

export async function getRun(runId: string): Promise<AnalysisRun> {
  const response = await fetch(`${API_BASE}/runs/${runId}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as AnalysisRun;
}

export async function cancelRun(
  session: SessionInfo,
  runId: string,
): Promise<AnalysisRun> {
  const response = await fetch(`${API_BASE}/runs/${runId}/cancel`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
    },
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as AnalysisRun;
}

export async function listMatrixRows(
  runId: string,
): Promise<{ items: MatrixRow[]; total: number }> {
  const response = await fetch(`${API_BASE}/runs/${runId}/matrix`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as { items: MatrixRow[]; total: number };
}
