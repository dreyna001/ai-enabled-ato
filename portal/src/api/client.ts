import {
  INVALID_RESPONSE_MESSAGE,
  parseAnalysisRun,
  parseApproval,
  parseArtifactList,
  parseChangeAnalysis,
  parseChatResponse,
  parseDisposition,
  parseExportDraft,
  parseMatrixList,
  parsePackageRevision,
  parsePackageRevisionDraft,
  parseDraftExportReadiness,
  parseIntakeReport,
  parsePreflight,
  parseReadinessResponse,
  parseReviewComment,
  parseReviewCommentList,
  parseReviewRevision,
  parseRevisionList,
  parseRunList,
  parseSearchResults,
  parseSessionInfo,
  parseSystem,
  parseSystemList,
} from "./responseSchemas";
import type {
  AnalysisRun,
  Approval,
  ArtifactDescriptor,
  ChangeAnalysisResult,
  ChatResponse,
  CreateRevisionInput,
  Disposition,
  ExportDraft,
  ExportDownloadResult,
  MatrixPage,
  MatrixRow,
  PackageDraftDocument,
  PackageRevision,
  PackageRevisionDraft,
  DraftExportReadiness,
  IntakeReport,
  PatchPackageRevisionMetadataInput,
  PreflightResult,
  ReviewComment,
  ReviewRevision,
  ReadinessResponse,
  SearchResults,
  SessionInfo,
  System,
} from "../types";
import { prepareUploadFile, type ArtifactKind } from "@/utils/artifactKinds";
import { parseContentDispositionFilename } from "@/utils/downloadFilename";

export type ApiErrorKind = "cancelled" | "timeout" | "http" | "invalid_response";

export type ProblemFieldError = {
  path: string;
  code: string;
  message: string;
};

export const INVALID_RESPONSE_STATUS = 502;

export class ApiError extends Error {
  status: number;
  kind: ApiErrorKind;
  errorCode?: string;
  fieldErrors?: ProblemFieldError[];

  constructor(
    status: number,
    message: string,
    kind: ApiErrorKind = "http",
    errorCode?: string,
    fieldErrors?: ProblemFieldError[],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
    this.errorCode = errorCode;
    this.fieldErrors = fieldErrors;
  }
}

export function isCancelledRequest(
  error: unknown,
  signal?: AbortSignal,
): boolean {
  if (signal?.aborted) {
    return true;
  }
  return error instanceof ApiError && error.kind === "cancelled";
}

type ResponseParser<T> = (value: unknown) => T | null;

function parseProblemFieldErrors(value: unknown): ProblemFieldError[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const fieldErrors: ProblemFieldError[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const record = item as Record<string, unknown>;
    if (
      typeof record.path === "string" &&
      typeof record.code === "string" &&
      typeof record.message === "string"
    ) {
      fieldErrors.push({
        path: record.path,
        code: record.code,
        message: record.message,
      });
    }
  }
  return fieldErrors.length > 0 ? fieldErrors : undefined;
}

async function readProblemBody(response: Response): Promise<{
  detail: string;
  errorCode?: string;
  fieldErrors?: ProblemFieldError[];
}> {
  let detail = response.statusText;
  let errorCode: string | undefined;
  let fieldErrors: ProblemFieldError[] | undefined;
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      error_code?: unknown;
      error?: unknown;
      title?: unknown;
      field_errors?: unknown;
    };
    if (typeof body.error_code === "string") {
      errorCode = body.error_code;
    } else if (typeof body.error === "string") {
      errorCode = body.error;
    }
    fieldErrors = parseProblemFieldErrors(body.field_errors);
    if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (typeof body.title === "string") {
      detail = body.title;
    } else if (body.detail != null) {
      detail = JSON.stringify(body.detail);
    }
  } catch {
    // ignore parse errors
  }
  return { detail, errorCode, fieldErrors };
}

async function readValidatedJson<T>(
  response: Response,
  parse: ResponseParser<T>,
): Promise<T> {
  if (!response.ok) {
    const { detail, errorCode, fieldErrors } = await readProblemBody(response);
    throw new ApiError(response.status, detail, "http", errorCode, fieldErrors);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(
      INVALID_RESPONSE_STATUS,
      INVALID_RESPONSE_MESSAGE,
      "invalid_response",
    );
  }

  const parsed = parse(body);
  if (parsed === null) {
    throw new ApiError(
      INVALID_RESPONSE_STATUS,
      INVALID_RESPONSE_MESSAGE,
      "invalid_response",
    );
  }
  return parsed;
}

async function readValidatedJsonWithEtag<T>(
  response: Response,
  parse: ResponseParser<T>,
): Promise<{ data: T; etag: string | null }> {
  const data = await readValidatedJson(response, parse);
  return {
    data,
    etag: response.headers.get("ETag"),
  };
}

type ApiFetchOptions = RequestInit & {
  timeoutMs?: number;
};

const API_BASE = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

function mergeAbortSignals(
  timeoutMs: number,
  callerSignal?: AbortSignal,
): { signal: AbortSignal; cleanup: () => void } {
  const timeoutController = new AbortController();
  const timeout = window.setTimeout(() => timeoutController.abort(), timeoutMs);
  const cleanup = () => window.clearTimeout(timeout);

  if (!callerSignal) {
    return { signal: timeoutController.signal, cleanup };
  }

  if (typeof AbortSignal !== "undefined" && "any" in AbortSignal) {
    return {
      signal: AbortSignal.any([timeoutController.signal, callerSignal]),
      cleanup,
    };
  }

  const linked = new AbortController();
  const abortLinked = () => linked.abort();
  if (callerSignal.aborted || timeoutController.signal.aborted) {
    linked.abort();
  }
  callerSignal.addEventListener("abort", abortLinked, { once: true });
  timeoutController.signal.addEventListener("abort", abortLinked, { once: true });
  return {
    signal: linked.signal,
    cleanup: () => {
      cleanup();
      callerSignal.removeEventListener("abort", abortLinked);
      timeoutController.signal.removeEventListener("abort", abortLinked);
    },
  };
}

async function apiFetch(
  input: RequestInfo | URL,
  { timeoutMs = DEFAULT_TIMEOUT_MS, signal, ...init }: ApiFetchOptions = {},
): Promise<Response> {
  const { signal: mergedSignal, cleanup } = mergeAbortSignals(
    timeoutMs,
    signal ?? undefined,
  );
  try {
    return await fetch(input, {
      ...init,
      signal: mergedSignal,
    });
  } catch (error) {
    if (mergedSignal.aborted) {
      if (signal?.aborted) {
        throw new ApiError(0, "Request cancelled.", "cancelled");
      }
      throw new ApiError(0, "Request timed out.", "timeout");
    }
    throw error;
  } finally {
    cleanup();
  }
}

function mutationHeaders(
  session: SessionInfo,
  extra: Record<string, string> = {},
): Record<string, string> {
  return {
    "X-CSRF-Token": session.csrf_token,
    Origin: session.portal_origin,
    ...extra,
  };
}

export async function fetchSession(
  options: ApiFetchOptions = {},
): Promise<SessionInfo | null> {
  const response = await apiFetch(`${API_BASE}/auth/session`, {
    credentials: "include",
    ...options,
  });
  if (response.status === 401) {
    return null;
  }
  return readValidatedJson(response, parseSessionInfo);
}

export function login(): void {
  window.location.href = `${API_BASE}/auth/login`;
}

export async function logout(options: ApiFetchOptions = {}): Promise<void> {
  const response = await apiFetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
    ...options,
  });
  if (!response.ok && response.status !== 204) {
    throw new ApiError(response.status, response.statusText);
  }
}

export async function fetchReadiness(
  options: ApiFetchOptions = {},
): Promise<ReadinessResponse> {
  const response = await apiFetch("/health/ready", options);
  const body = await response.json();
  const parsed = parseReadinessResponse(body);
  return {
    status: parsed?.status ?? (response.ok ? "ok" : "degraded"),
    checks: parsed?.checks,
    error_code: parsed?.error_code,
    detail: parsed?.detail ?? parsed?.title,
  };
}

export async function listSystems(
  options: ApiFetchOptions & { includeArchived?: boolean } = {},
): Promise<System[]> {
  const { includeArchived, ...fetchOptions } = options;
  const params = new URLSearchParams();
  if (includeArchived) {
    params.set("include_archived", "true");
  }
  const query = params.toString();
  const response = await apiFetch(
    `${API_BASE}/systems${query ? `?${query}` : ""}`,
    {
      credentials: "include",
      ...fetchOptions,
    },
  );
  return readValidatedJson(response, parseSystemList);
}

export async function archiveSystem(
  session: SessionInfo,
  systemId: string,
  options: ApiFetchOptions = {},
): Promise<System> {
  const response = await apiFetch(`${API_BASE}/systems/${systemId}/archive`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session),
      ...options.headers,
    },
    ...options,
  });
  return readValidatedJson(response, parseSystem);
}

export async function createSystem(
  session: SessionInfo,
  displayName: string,
  options: ApiFetchOptions = {},
): Promise<System> {
  const response = await apiFetch(`${API_BASE}/systems`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session),
      ...options.headers,
    },
    body: JSON.stringify({
      display_name: displayName,
      external_system_id: null,
      owner_group: "owners",
      viewer_groups: ["viewers"],
    }),
    ...options,
  });
  return readValidatedJson(response, parseSystem);
}

export async function listRevisions(
  systemId: string,
  options: ApiFetchOptions = {},
): Promise<PackageRevision[]> {
  const response = await apiFetch(
    `${API_BASE}/systems/${systemId}/package-revisions`,
    { credentials: "include", ...options },
  );
  return readValidatedJson(response, parseRevisionList);
}

export function buildCreateRevisionBody(
  input: CreateRevisionInput,
): Record<string, string | null> {
  return {
    parent_revision_id: input.parent_revision_id ?? null,
  };
}

export async function createRevision(
  session: SessionInfo,
  systemId: string,
  input: CreateRevisionInput,
  options: ApiFetchOptions = {},
): Promise<PackageRevision> {
  const response = await apiFetch(
    `${API_BASE}/systems/${systemId}/package-revisions`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      body: JSON.stringify(buildCreateRevisionBody(input)),
      ...options,
    },
  );
  return readValidatedJson(response, parsePackageRevision);
}

export async function getRevision(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<PackageRevision> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}`, {
    credentials: "include",
    ...options,
  });
  return readValidatedJson(response, parsePackageRevision);
}

export async function getIntakeReport(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<IntakeReport> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/intake-report`,
    {
      credentials: "include",
      ...options,
    },
  );
  return readValidatedJson(response, parseIntakeReport);
}

export function buildPatchRevisionMetadataBody(
  patch: PatchPackageRevisionMetadataInput,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (patch.profile_id !== undefined) {
    body.profile_id = patch.profile_id;
  }
  if (patch.certification_class !== undefined) {
    body.certification_class = patch.certification_class;
  }
  if (patch.impact_level !== undefined) {
    body.impact_level = patch.impact_level;
  }
  if (patch.data_origin !== undefined) {
    body.data_origin = patch.data_origin;
  }
  if (patch.sensitivity !== undefined) {
    body.sensitivity = patch.sensitivity;
  }
  return body;
}

export async function patchRevisionMetadata(
  session: SessionInfo,
  revisionId: string,
  etag: string,
  patch: PatchPackageRevisionMetadataInput,
  options: ApiFetchOptions = {},
): Promise<{ revision: PackageRevision; etag: string }> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}`, {
    method: "PATCH",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session, { "If-Match": etag }),
      ...options.headers,
    },
    body: JSON.stringify(buildPatchRevisionMetadataBody(patch)),
    ...options,
  });
  const { data, etag: responseEtag } = await readValidatedJsonWithEtag(
    response,
    parsePackageRevision,
  );
  return {
    revision: data,
    etag: responseEtag ?? revisionEtag(data.revision_version),
  };
}

export async function uploadPackageFile(
  session: SessionInfo,
  revisionId: string,
  file: File,
  artifactKind: ArtifactKind,
  options: ApiFetchOptions = {},
): Promise<void> {
  const uploadFile = prepareUploadFile(file);
  const form = new FormData();
  form.append("file", uploadFile);
  form.append("artifact_kind", artifactKind);
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/files`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      body: form,
      ...options,
    },
  );
  if (!response.ok) {
    const { detail, errorCode, fieldErrors } = await readProblemBody(response);
    throw new ApiError(response.status, detail, "http", errorCode, fieldErrors);
  }
}

export async function finalizeRevision(
  session: SessionInfo,
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<PackageRevision> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/finalize`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      ...options,
    },
  );
  return readValidatedJson(response, parsePackageRevision);
}

export async function getRevisionDraft(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<{ draft: PackageRevisionDraft; etag: string }> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}/draft`, {
    credentials: "include",
    ...options,
  });
  const { data, etag } = await readValidatedJsonWithEtag(
    response,
    parsePackageRevisionDraft,
  );
  return {
    draft: data,
    etag: etag ?? revisionEtag(data.revision_version),
  };
}

export async function saveRevisionDraft(
  session: SessionInfo,
  revisionId: string,
  document: PackageDraftDocument,
  etag: string,
  options: ApiFetchOptions = {},
): Promise<{ draft: PackageRevisionDraft; etag: string }> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}/draft`, {
    method: "PUT",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session, { "If-Match": etag }),
      ...options.headers,
    },
    body: JSON.stringify({ document }),
    ...options,
  });
  const { data, etag: responseEtag } = await readValidatedJsonWithEtag(
    response,
    parsePackageRevisionDraft,
  );
  return {
    draft: data,
    etag: responseEtag ?? revisionEtag(data.revision_version),
  };
}

export async function confirmRevision(
  session: SessionInfo,
  revisionId: string,
  etag: string,
  options: ApiFetchOptions = {},
): Promise<PackageRevision> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/confirm`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session, { "If-Match": etag }),
        ...options.headers,
      },
      ...options,
    },
  );
  return readValidatedJson(response, parsePackageRevision);
}

export function revisionEtag(revisionVersion: number): string {
  return `"v${revisionVersion}"`;
}

export async function getPreflight(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<PreflightResult> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}/preflight`, {
    credentials: "include",
    ...options,
  });
  return readValidatedJson(response, parsePreflight);
}

export async function getDraftExportReadiness(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<DraftExportReadiness> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/draft/export-readiness`,
    {
      credentials: "include",
      ...options,
    },
  );
  return readValidatedJson(response, parseDraftExportReadiness);
}

export async function getChangeAnalysis(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<ChangeAnalysisResult> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}/delta`, {
    credentials: "include",
    ...options,
  });
  return readValidatedJson(response, parseChangeAnalysis);
}

export async function searchPackage(
  revisionId: string,
  query: string,
  options: ApiFetchOptions & { limit?: number } = {},
): Promise<SearchResults> {
  const params = new URLSearchParams({ q: query });
  if (options.limit != null) {
    params.set("limit", String(options.limit));
  }
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/search?${params.toString()}`,
    { credentials: "include", ...options },
  );
  return readValidatedJson(response, parseSearchResults);
}

export async function chatWithPackage(
  session: SessionInfo,
  revisionId: string,
  question: string,
  options: ApiFetchOptions & { runId: string; reviewRevisionId?: string | null },
): Promise<ChatResponse> {
  const response = await apiFetch(`${API_BASE}/package-revisions/${revisionId}/chat`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...mutationHeaders(session),
      ...options.headers,
    },
    body: JSON.stringify({
      question,
      run_id: options.runId,
      review_revision_id: options.reviewRevisionId ?? null,
    }),
    ...options,
  });
  return readValidatedJson(response, parseChatResponse);
}

export async function startRun(
  session: SessionInfo,
  revisionId: string,
  options: ApiFetchOptions & {
    runType?: "deterministic_only" | "targeted" | "full";
    assessmentItemIds?: string[];
    parentRunId?: string | null;
  } = {},
): Promise<AnalysisRun> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/runs`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      body: JSON.stringify({
        run_type: options.runType ?? "deterministic_only",
        parent_run_id: options.parentRunId ?? null,
        assessment_item_ids: options.assessmentItemIds ?? [],
      }),
      ...options,
    },
  );
  return readValidatedJson(response, parseAnalysisRun);
}

/** @deprecated Use startRun with runType: "targeted". */
export async function startTargetedRun(
  session: SessionInfo,
  revisionId: string,
  assessmentItemIds: string[] = [],
  options: ApiFetchOptions = {},
): Promise<AnalysisRun> {
  return startRun(session, revisionId, {
    ...options,
    runType: "targeted",
    assessmentItemIds,
  });
}

export async function listRuns(
  revisionId: string,
  options: ApiFetchOptions = {},
): Promise<AnalysisRun[]> {
  const response = await apiFetch(
    `${API_BASE}/package-revisions/${revisionId}/runs`,
    { credentials: "include", ...options },
  );
  return readValidatedJson(response, parseRunList);
}

export async function getRun(
  runId: string,
  options: ApiFetchOptions = {},
): Promise<AnalysisRun> {
  const response = await apiFetch(`${API_BASE}/runs/${runId}`, {
    credentials: "include",
    ...options,
  });
  return readValidatedJson(response, parseAnalysisRun);
}

export async function cancelRun(
  session: SessionInfo,
  runId: string,
  options: ApiFetchOptions = {},
): Promise<AnalysisRun> {
  const response = await apiFetch(`${API_BASE}/runs/${runId}/cancel`, {
    method: "POST",
    credentials: "include",
    headers: {
      ...mutationHeaders(session),
      ...options.headers,
    },
    ...options,
  });
  return readValidatedJson(response, parseAnalysisRun);
}

export async function listMatrixRows(
  runId: string,
  options: ApiFetchOptions & {
    cursor?: string | null;
    limit?: number;
    status?: string;
  } = {},
): Promise<MatrixPage> {
  const params = new URLSearchParams();
  if (options.cursor) {
    params.set("cursor", options.cursor);
  }
  if (options.limit != null) {
    params.set("limit", String(options.limit));
  }
  if (options.status) {
    params.set("status", options.status);
  }
  const query = params.toString();
  const response = await apiFetch(
    `${API_BASE}/runs/${runId}/matrix${query ? `?${query}` : ""}`,
    { credentials: "include", ...options },
  );
  const parsed = await readValidatedJson(response, parseMatrixList);
  return parsed;
}

export async function listRunArtifacts(
  runId: string,
  options: ApiFetchOptions = {},
): Promise<{ items: ArtifactDescriptor[]; next_cursor: string | null }> {
  const response = await apiFetch(`${API_BASE}/runs/${runId}/artifacts`, {
    credentials: "include",
    ...options,
  });
  const parsed = await readValidatedJson(response, parseArtifactList);
  return parsed;
}

export async function createReviewRevision(
  session: SessionInfo,
  runId: string,
  options: ApiFetchOptions = {},
): Promise<ReviewRevision> {
  const response = await apiFetch(`${API_BASE}/runs/${runId}/review-revisions`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session),
      ...options.headers,
    },
    ...options,
  });
  return readValidatedJson(response, parseReviewRevision);
}

export async function submitReviewRevision(
  session: SessionInfo,
  reviewRevisionId: string,
  etag: string,
  options: ApiFetchOptions = {},
): Promise<ReviewRevision> {
  const response = await apiFetch(`${API_BASE}/review-revisions/${reviewRevisionId}/submit`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session, { "If-Match": etag }),
      ...options.headers,
    },
    ...options,
  });
  return readValidatedJson(response, parseReviewRevision);
}

export async function updateDisposition(
  session: SessionInfo,
  reviewRevisionId: string,
  matrixRowId: string,
  etag: string,
  body: { decision: string; edited_summary?: string | null; notes?: string | null },
  options: ApiFetchOptions = {},
): Promise<Disposition> {
  const response = await apiFetch(
    `${API_BASE}/review-revisions/${reviewRevisionId}/dispositions/${matrixRowId}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...mutationHeaders(session, { "If-Match": etag }),
        ...options.headers,
      },
      body: JSON.stringify(body),
      ...options,
    },
  );
  return readValidatedJson(response, parseDisposition);
}

export async function createExportDraft(
  session: SessionInfo,
  reviewRevisionId: string,
  options: ApiFetchOptions = {},
): Promise<ExportDraft> {
  const response = await apiFetch(
    `${API_BASE}/review-revisions/${reviewRevisionId}/export-drafts`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      ...options,
    },
  );
  return readValidatedJson(response, parseExportDraft);
}

export async function submitExportDraft(
  session: SessionInfo,
  exportDraftId: string,
  options: ApiFetchOptions = {},
): Promise<Approval> {
  const response = await apiFetch(`${API_BASE}/export-drafts/${exportDraftId}/submit`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session, { "If-Match": '"v1"' }),
      ...options.headers,
    },
    ...options,
  });
  return readValidatedJson(response, parseApproval);
}

export async function approveExport(
  session: SessionInfo,
  approvalId: string,
  reason?: string | null,
  options: ApiFetchOptions = {},
): Promise<Approval> {
  const response = await apiFetch(`${API_BASE}/approvals/${approvalId}/approve`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session),
      ...options.headers,
    },
    body: JSON.stringify({ reason: reason ?? null }),
    ...options,
  });
  return readValidatedJson(response, parseApproval);
}

export async function rejectExport(
  session: SessionInfo,
  approvalId: string,
  reason: string,
  options: ApiFetchOptions = {},
): Promise<Approval> {
  const response = await apiFetch(`${API_BASE}/approvals/${approvalId}/reject`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...mutationHeaders(session),
      ...options.headers,
    },
    body: JSON.stringify({ reason }),
    ...options,
  });
  return readValidatedJson(response, parseApproval);
}

export async function createReviewComment(
  session: SessionInfo,
  reviewRevisionId: string,
  body: { matrix_row_id?: string | null; body: string },
  options: ApiFetchOptions = {},
): Promise<ReviewComment> {
  const response = await apiFetch(
    `${API_BASE}/review-revisions/${reviewRevisionId}/comments`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...mutationHeaders(session),
        ...options.headers,
      },
      body: JSON.stringify(body),
      ...options,
    },
  );
  return readValidatedJson(response, parseReviewComment);
}

export async function listReviewComments(
  reviewRevisionId: string,
  options: ApiFetchOptions = {},
): Promise<{ items: ReviewComment[]; next_cursor: string | null }> {
  const response = await apiFetch(
    `${API_BASE}/review-revisions/${reviewRevisionId}/comments`,
    {
      credentials: "include",
      ...options,
    },
  );
  const parsed = await readValidatedJson(response, parseReviewCommentList);
  return parsed;
}

export async function downloadExport(
  _session: SessionInfo,
  exportOrApprovalId: string,
  options: ApiFetchOptions = {},
): Promise<ExportDownloadResult> {
  const response = await apiFetch(`${API_BASE}/exports/${exportOrApprovalId}/download`, {
    credentials: "include",
    headers: {
      "Idempotency-Key": crypto.randomUUID(),
      ...options.headers,
    },
    ...options,
  });
  if (!response.ok) {
    const { detail, errorCode, fieldErrors } = await readProblemBody(response);
    throw new ApiError(response.status, detail, "http", errorCode, fieldErrors);
  }
  const blob = await response.blob();
  const header = response.headers.get("Content-Disposition");
  const filename =
    parseContentDispositionFilename(header) ??
    `ato-export-${exportOrApprovalId}.zip`;
  return { blob, filename };
}

export type { MatrixRow };
