import { z } from "zod";
import type { SessionInfo, System } from "@/types";
import type {
  AgencyDocxIssue,
  AgencyDocxMappingException,
  AgencyDocxRender,
  AgencyDocxRenderStatus,
  AgentContext,
  CategorizationChange,
  ControlResponseOptions,
  ControlStatementChange,
  QuestionAnswer,
  SspSectionChange,
  SspWorkspace,
} from "@/sspWorkspaceTypes";
import { DEFAULT_CONTROL_RESPONSE_OPTIONS } from "@/sspWorkspaceTypes";
import { ApiError } from "@/api/client";

const API_BASE = "/api/v1";

const systemSchema = z.object({
  system_id: z.string().uuid(),
  display_name: z.string().min(1),
  owner_group: z.string().min(1),
  viewer_groups: z.array(z.string()),
  archived_at: z.string().nullable().optional(),
});

const profileSchema = z.object({
  profile_version_id: z.string().uuid(),
  profile_id: z.string().min(1),
  version: z.string().min(1),
  status: z.enum(["inactive", "active", "archived"]),
  display_name: z.string().min(1),
});

const envelopeSchema = z.object({
  workspace_id: z.string().uuid(),
  system_id: z.string().uuid(),
  status: z.string(),
  system: z.object({
    display_name: z.string(),
    external_system_id: z.string().nullable().optional(),
  }),
  profile: z.object({
    profile_version_id: z.string().uuid(),
    profile_id: z.string(),
    version: z.string(),
    status: z.string(),
    impact_level: z.enum(["low", "moderate", "high"]).nullable(),
    provisional_impact_level: z.enum(["low", "moderate", "high"]),
  }),
  current_revision: z.object({
    revision_id: z.string().uuid(),
    version: z.number().int().positive(),
    status: z.string(),
    content_sha256: z.string(),
    created_at: z.string(),
    content: z.object({
      facts: z.array(z.record(z.string(), z.unknown())),
      sections: z.array(z.record(z.string(), z.unknown())),
      controls: z.array(z.record(z.string(), z.unknown())),
      questions: z.array(z.record(z.string(), z.unknown())),
    }),
  }),
  evidence: z.array(z.record(z.string(), z.unknown())),
  approvals: z.array(z.record(z.string(), z.unknown())),
  agent_patches: z.array(z.record(z.string(), z.unknown())),
  requirements: z.array(z.record(z.string(), z.unknown())),
  satisfied_requirement_ids: z.array(z.string()),
  metrics: z.record(z.string(), z.unknown()),
  control_response: z
    .object({
      implementation_statuses: z.array(z.string()),
      responsibilities: z.array(z.string()),
      question_owner_types: z.array(z.string()),
      evidence_required_for_agent_statement: z.boolean(),
    })
    .optional(),
  agency_docx_renders: z.array(z.record(z.string(), z.unknown())).optional(),
});

export type SspProfile = z.infer<typeof profileSchema>;

const AGENCY_DOCX_RENDER_STATUSES = new Set<AgencyDocxRenderStatus>([
  "awaiting_approval",
  "review_failed",
  "approved",
  "rejected",
]);

const AGENCY_DOCX_ISSUE_SEVERITIES = new Set(["blocker", "warning"] as const);

function mutationHeaders(
  session: SessionInfo,
  contentType = true,
): Record<string, string> {
  return {
    ...(contentType ? { "Content-Type": "application/json" } : {}),
    "X-CSRF-Token": session.csrf_token,
    Origin: session.portal_origin,
  };
}

async function apiRequest<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText || "Request failed";
    try {
      const body = (await response.json()) as Record<string, unknown>;
      detail =
        (typeof body.detail === "string" && body.detail) ||
        (typeof body.error === "string" && body.error) ||
        detail;
    } catch {
      // Preserve the HTTP status text when the server did not return JSON.
    }
    throw new ApiError(response.status, detail);
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) {
    throw new ApiError(502, "The SSP service returned an invalid response.", "invalid_response");
  }
  return parsed.data;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function nullableText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function evidenceLinks(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((raw, index) => {
    const link = record(raw);
    return {
      id: `${text(link.artifact_id)}:${index}`,
      artifactId: text(link.artifact_id),
      locator: JSON.stringify(record(link.locator)),
    };
  });
}

function factValue(
  facts: Array<Record<string, unknown>>,
  key: string,
): string {
  const fact = facts.find((item) => text(item.key) === key);
  if (!fact) return "";
  const value = fact.value;
  if (Array.isArray(value)) return value.map(String).join(", ");
  return typeof value === "string" ? value : "";
}

export function mapControlResponse(raw: unknown): ControlResponseOptions {
  const document = record(raw);
  const implementationStatuses = document.implementation_statuses;
  const responsibilities = document.responsibilities;
  const questionOwnerTypes = document.question_owner_types;
  const evidenceRequired = document.evidence_required_for_agent_statement;
  if (
    !Array.isArray(implementationStatuses) ||
    !Array.isArray(responsibilities) ||
    !Array.isArray(questionOwnerTypes) ||
    typeof evidenceRequired !== "boolean"
  ) {
    return DEFAULT_CONTROL_RESPONSE_OPTIONS;
  }
  return {
    implementationStatuses: implementationStatuses.map(String),
    responsibilities: responsibilities.map(String),
    questionOwnerTypes: questionOwnerTypes.map(String),
    evidenceRequiredForAgentStatement: evidenceRequired,
  };
}

function mapAgencyDocxMappingException(
  raw: unknown,
): AgencyDocxMappingException | null {
  const item = record(raw);
  const severity = text(item.severity);
  if (!AGENCY_DOCX_ISSUE_SEVERITIES.has(severity as "blocker" | "warning")) {
    return null;
  }
  const code = text(item.code);
  const message = text(item.message);
  if (!code || !message) return null;
  return {
    severity: severity as AgencyDocxMappingException["severity"],
    code,
    message,
  };
}

function mapAgencyDocxIssue(raw: unknown): AgencyDocxIssue | null {
  const item = record(raw);
  const severity = text(item.severity);
  if (!AGENCY_DOCX_ISSUE_SEVERITIES.has(severity as "blocker" | "warning")) {
    return null;
  }
  const code = text(item.code);
  const message = text(item.message);
  if (!code || !message) return null;
  const locatorRaw = item.locator;
  const locator =
    typeof locatorRaw === "string"
      ? locatorRaw
      : locatorRaw === null
        ? null
        : null;
  return {
    severity: severity as AgencyDocxIssue["severity"],
    code,
    message,
    locator,
  };
}

function mapAgencyDocxIssues(raw: unknown): AgencyDocxIssue[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry) => {
    const mapped = mapAgencyDocxIssue(entry);
    return mapped ? [mapped] : [];
  });
}

function mapAgencyDocxMappingExceptions(
  raw: unknown,
): AgencyDocxMappingException[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry) => {
    const mapped = mapAgencyDocxMappingException(entry);
    return mapped ? [mapped] : [];
  });
}

export function mapAgencyDocxRenders(raw: unknown): AgencyDocxRender[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry) => {
    const item = record(entry);
    const status = text(item.status);
    if (!AGENCY_DOCX_RENDER_STATUSES.has(status as AgencyDocxRenderStatus)) {
      return [];
    }
    const renderId = text(item.render_id);
    if (!renderId) return [];
    return [
      {
        id: renderId,
        profileVersionId: text(item.profile_version_id),
        sourceRevisionId: text(item.source_revision_id),
        sourceRevisionSha256: text(item.source_revision_sha256),
        templateSha256: text(item.template_sha256),
        templateFilename: text(item.template_filename),
        outputSha256: text(item.output_sha256),
        status: status as AgencyDocxRenderStatus,
        createdBy: text(item.created_by),
        createdAt: text(item.created_at),
        resolvedBy: nullableText(item.resolved_by),
        resolvedAt: nullableText(item.resolved_at),
        mappingSummary: text(item.mapping_summary),
        mappingExceptions: mapAgencyDocxMappingExceptions(
          item.mapping_exceptions,
        ),
        reviewSummary: text(item.review_summary),
        reviewIssues: mapAgencyDocxIssues(item.review_issues),
        canApprove: item.can_approve === true,
        canPreview: item.can_preview === true,
        canDownload: item.can_download === true,
      },
    ];
  });
}

export function mapWorkspaceEnvelope(raw: unknown): SspWorkspace {
  const envelope = envelopeSchema.parse(raw);
  const revision = envelope.current_revision;
  const content = revision.content;
  const facts = content.facts;
  const satisfied = new Set(envelope.satisfied_requirement_ids);
  const latestApproval = envelope.approvals[0];
  const categorizationConfirmed =
    factValue(facts, "system.categorization_status") === "confirmed";

  return {
    id: envelope.workspace_id,
    name: envelope.system.display_name,
    purpose: factValue(facts, "system.purpose"),
    hosting: factValue(facts, "system.hosting_model"),
    impactLevel: categorizationConfirmed
      ? envelope.profile.impact_level ?? ""
      : "",
    provisionalImpactLevel: envelope.profile.provisional_impact_level,
    categorization: {
      confidentiality: factValue(facts, "system.confidentiality_impact") as
        | "low"
        | "moderate"
        | "high"
        | "",
      integrity: factValue(facts, "system.integrity_impact") as
        | "low"
        | "moderate"
        | "high"
        | "",
      availability: factValue(facts, "system.availability_impact") as
        | "low"
        | "moderate"
        | "high"
        | "",
      confidentialityRationale: factValue(
        facts,
        "system.confidentiality_impact_rationale",
      ),
      integrityRationale: factValue(
        facts,
        "system.integrity_impact_rationale",
      ),
      availabilityRationale: factValue(
        facts,
        "system.availability_impact_rationale",
      ),
      confirmed: categorizationConfirmed,
    },
    authorizationPath: factValue(facts, "system.authorization_path"),
    profile: {
      id: envelope.profile.profile_version_id,
      name: envelope.profile.profile_id,
      version: envelope.profile.version,
      baseline: categorizationConfirmed && envelope.profile.impact_level
        ? (envelope.profile.impact_level.charAt(0).toUpperCase() +
            envelope.profile.impact_level.slice(1)) as
            | "Low"
            | "Moderate"
            | "High"
        : "Unconfirmed",
    },
    controlResponse: mapControlResponse(envelope.control_response),
    revisionId: revision.revision_id,
    revisionUpdatedAt: revision.created_at,
    lastAgentUpdateAt:
      envelope.agent_patches.find((item) => text(item.status) === "applied")
        ?.resolved_at as string | undefined,
    currentContentHash: revision.content_sha256,
    approvedContentHash: latestApproval
      ? nullableText(latestApproval.revision_sha256)
      : null,
    processingJobsTerminal: envelope.evidence.every(
      (item) => !["uploaded", "processing"].includes(text(item.status)),
    ),
    revisionSaved: true,
    internallyConsistent: true,
    requirements: envelope.requirements.map((item) => ({
      id: text(item.key),
      label: text(item.key),
      required: item.required !== false,
    })),
    evidence: envelope.evidence.map((item) => ({
      id: text(item.evidence_artifact_id),
      name: text(item.display_filename),
      mediaType: text(item.media_type),
      state: text(item.status) as "uploaded" | "processing" | "processed" | "failed",
      uploadedAt: text(item.uploaded_at),
      error: nullableText(item.failure_code),
    })),
    sections: content.sections.map((item) => {
      const key = text(item.key);
      return {
        id: key,
        title: text(item.title) || key,
        content: text(item.content),
        state: text(item.state) as "empty" | "generated" | "edited" | "reviewed",
        requirementIds: [key],
        satisfiedRequirementIds: satisfied.has(key) ? [key] : [],
        evidenceLinks: evidenceLinks(item.evidence),
      };
    }),
    controls: content.controls.map((item) => ({
      id: text(item.control_id),
      title: text(item.title),
      family: text(item.control_id).split("-")[0] ?? "",
      state: text(item.state) as "empty" | "generated" | "partial" | "reviewed",
      implementationStatus: text(item.implementation_status) || "unknown",
      responsibility: text(item.responsibility) || "unknown",
      statement: text(item.implementation_statement),
      evidenceLinks: evidenceLinks(item.evidence),
      unresolvedReason: nullableText(item.unresolved_reason),
    })),
    questions: content.questions.map((item) => ({
      id: text(item.question_id),
      targetType:
        text(item.target_type) === "control"
          ? "control"
          : text(item.target_type) === "ssp_section"
            ? "ssp_section"
            : "workspace",
      targetId: text(item.target_key),
      prompt: text(item.question),
      owner: text(item.owner_type),
      state: text(item.state) as "open" | "answered" | "dismissed",
    })),
    patches: envelope.agent_patches.map((item) => {
      const operations = Array.isArray(item.operations) ? item.operations : [];
      const operation = record(operations[0]);
      const patches = Array.isArray(operation.patches) ? operation.patches : [];
      return {
        id: text(item.patch_id),
        summary: text(item.summary),
        state: text(item.status) as "proposed" | "applied" | "rejected" | "stale",
        targetLabels: patches.map((patch) => {
          const target = record(patch);
          return `${text(target.target_type)}:${text(target.target_id)}`;
        }),
      };
    }),
    agencyDocxRenders: mapAgencyDocxRenders(envelope.agency_docx_renders),
  };
}

export async function listSspProfiles(): Promise<SspProfile[]> {
  const result = await apiRequest(
    "/ssp-profiles",
    z.object({ items: z.array(profileSchema) }),
  );
  return result.items;
}

export async function listSspSystems(): Promise<System[]> {
  const result = await apiRequest(
    "/ssp-systems",
    z.object({ items: z.array(systemSchema) }),
  );
  return result.items;
}

export async function createSspSystem(
  session: SessionInfo,
  displayName: string,
): Promise<System> {
  return apiRequest("/ssp-systems", systemSchema, {
    method: "POST",
    headers: {
      ...mutationHeaders(session),
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({ display_name: displayName }),
  });
}

export async function listSspWorkspaces(): Promise<SspWorkspace[]> {
  const result = await apiRequest(
    "/ssp-workspaces",
    z.object({ items: z.array(envelopeSchema) }),
  );
  return result.items.map(mapWorkspaceEnvelope);
}

export async function getSspWorkspace(workspaceId: string): Promise<SspWorkspace> {
  const result = await apiRequest(
    `/ssp-workspaces/${workspaceId}`,
    envelopeSchema,
  );
  return mapWorkspaceEnvelope(result);
}

export async function createSspWorkspace(
  session: SessionInfo,
  system: System,
  profileVersionId: string,
): Promise<SspWorkspace> {
  const result = await apiRequest("/ssp-workspaces", envelopeSchema, {
    method: "POST",
    headers: mutationHeaders(session),
    body: JSON.stringify({
      system_id: system.system_id,
      profile_version_id: profileVersionId,
    }),
  });
  return mapWorkspaceEnvelope(result);
}

export function saveSspCategorization(
  session: SessionInfo,
  workspace: SspWorkspace,
  change: CategorizationChange,
) {
  return workspaceMutation(
    session,
    workspace.id,
    "/categorization",
    "POST",
    {
      expected_revision_id: workspace.revisionId,
      confidentiality: change.confidentiality,
      integrity: change.integrity,
      availability: change.availability,
      confidentiality_rationale: change.confidentialityRationale,
      integrity_rationale: change.integrityRationale,
      availability_rationale: change.availabilityRationale,
    },
  );
}

async function workspaceMutation(
  session: SessionInfo,
  workspaceId: string,
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  body?: Record<string, unknown>,
): Promise<SspWorkspace> {
  const result = await apiRequest(
    `/ssp-workspaces/${workspaceId}${path}`,
    envelopeSchema,
    {
      method,
      headers: mutationHeaders(session),
      body: body ? JSON.stringify(body) : undefined,
    },
  );
  return mapWorkspaceEnvelope(result);
}

export function generateSspWorkspace(session: SessionInfo, workspace: SspWorkspace) {
  return workspaceMutation(session, workspace.id, "/generate", "POST", {
    expected_revision_id: workspace.revisionId,
  });
}

export function saveSspSection(
  session: SessionInfo,
  workspace: SspWorkspace,
  change: SspSectionChange,
) {
  return workspaceMutation(
    session,
    workspace.id,
    `/sections/${encodeURIComponent(change.sectionId)}`,
    "PATCH",
    { expected_revision_id: workspace.revisionId, content: change.content },
  );
}

export function saveSspControl(
  session: SessionInfo,
  workspace: SspWorkspace,
  change: ControlStatementChange,
) {
  return workspaceMutation(
    session,
    workspace.id,
    `/controls/${encodeURIComponent(change.controlId)}`,
    "PATCH",
    {
      expected_revision_id: workspace.revisionId,
      implementation_statement: change.statement,
      implementation_status: change.implementationStatus,
      responsibility: change.responsibility,
    },
  );
}

export function answerSspQuestion(
  session: SessionInfo,
  workspace: SspWorkspace,
  change: QuestionAnswer,
) {
  return workspaceMutation(
    session,
    workspace.id,
    `/questions/${encodeURIComponent(change.questionId)}/answer`,
    "POST",
    {
      expected_revision_id: workspace.revisionId,
      answer: change.answer,
    },
  );
}

export async function uploadSspEvidence(
  session: SessionInfo,
  workspace: SspWorkspace,
  file: File,
): Promise<SspWorkspace> {
  const form = new FormData();
  form.append("expected_revision_id", workspace.revisionId);
  form.append("file", file);
  const result = await apiRequest(
    `/ssp-workspaces/${workspace.id}/evidence`,
    envelopeSchema,
    {
      method: "POST",
      credentials: "include",
      headers: mutationHeaders(session, false),
      body: form,
    },
  );
  return mapWorkspaceEnvelope(result);
}

export function removeSspEvidence(
  session: SessionInfo,
  workspace: SspWorkspace,
  artifactId: string,
): Promise<SspWorkspace> {
  return workspaceMutation(
    session,
    workspace.id,
    `/evidence/${encodeURIComponent(artifactId)}`,
    "DELETE",
    { expected_revision_id: workspace.revisionId },
  );
}

export async function askSspAgent(
  session: SessionInfo,
  workspace: SspWorkspace,
  _context: AgentContext,
  instruction: string,
): Promise<SspWorkspace> {
  await apiRequest(
    `/ssp-workspaces/${workspace.id}/agent/patches`,
    z.object({
      patch_id: z.string().uuid(),
      status: z.string(),
      summary: z.string(),
      operations: z.array(z.unknown()),
    }),
    {
      method: "POST",
      headers: mutationHeaders(session),
      body: JSON.stringify({
        expected_revision_id: workspace.revisionId,
        instruction,
      }),
    },
  );
  return getSspWorkspace(workspace.id);
}

export function applySspPatch(
  session: SessionInfo,
  workspace: SspWorkspace,
  patchId: string,
) {
  return workspaceMutation(
    session,
    workspace.id,
    `/agent/patches/${patchId}/apply`,
    "POST",
    { expected_revision_id: workspace.revisionId },
  );
}

export function rejectSspPatch(
  session: SessionInfo,
  workspace: SspWorkspace,
  patchId: string,
) {
  return workspaceMutation(
    session,
    workspace.id,
    `/agent/patches/${patchId}/reject`,
    "POST",
  );
}

export function approveSspWorkspace(session: SessionInfo, workspace: SspWorkspace) {
  return workspaceMutation(session, workspace.id, "/approve", "POST", {
    expected_revision_id: workspace.revisionId,
  });
}

export async function downloadSspExport(
  workspace: SspWorkspace,
  format: "docx" | "json" | "oscal-json",
): Promise<void> {
  const params = new URLSearchParams({
    revision_id: workspace.revisionId,
    include_open_questions: "true",
  });
  const fallbackFilename =
    format === "oscal-json"
      ? `ssp-${workspace.revisionId}.oscal.json`
      : `ssp-${workspace.revisionId}.${format}`;
  await downloadWorkspaceBlob(
    `/ssp-workspaces/${workspace.id}/exports/${format}?${params}`,
    fallbackFilename,
  );
}

function parseAttachmentFilename(header: string | null): string | null {
  if (!header) return null;
  const match = /filename="([^"]+)"/i.exec(header.trim());
  if (!match) return null;
  const filename = match[1];
  return filename && !/[\u0000-\u001f\u007f]/.test(filename) ? filename : null;
}

async function readDownloadErrorDetail(response: Response): Promise<string> {
  let detail = response.statusText || "Request failed";
  try {
    const body = (await response.json()) as Record<string, unknown>;
    detail =
      (typeof body.detail === "string" && body.detail) ||
      (typeof body.error === "string" && body.error) ||
      detail;
  } catch {
    // Preserve the HTTP status text when the server did not return JSON.
  }
  return detail;
}

async function downloadWorkspaceBlob(
  path: string,
  fallbackFilename: string,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!response.ok) {
    throw new ApiError(response.status, await readDownloadErrorDetail(response));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    parseAttachmentFilename(response.headers.get("Content-Disposition")) ??
    fallbackFilename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function createAgencyDocxRender(
  session: SessionInfo,
  workspace: SspWorkspace,
  file: File,
): Promise<SspWorkspace> {
  const form = new FormData();
  form.append("revision_id", workspace.revisionId);
  form.append("file", file);
  const result = await apiRequest(
    `/ssp-workspaces/${workspace.id}/agency-docx-renders`,
    envelopeSchema,
    {
      method: "POST",
      credentials: "include",
      headers: mutationHeaders(session, false),
      body: form,
    },
  );
  return mapWorkspaceEnvelope(result);
}

async function agencyDocxRenderMutation(
  session: SessionInfo,
  workspaceId: string,
  renderId: string,
  action: "approve" | "reject",
): Promise<SspWorkspace> {
  const result = await apiRequest(
    `/ssp-workspaces/${workspaceId}/agency-docx-renders/${encodeURIComponent(renderId)}/${action}`,
    envelopeSchema,
    {
      method: "POST",
      headers: mutationHeaders(session),
    },
  );
  return mapWorkspaceEnvelope(result);
}

export function approveAgencyDocxRender(
  session: SessionInfo,
  workspace: SspWorkspace,
  renderId: string,
): Promise<SspWorkspace> {
  return agencyDocxRenderMutation(session, workspace.id, renderId, "approve");
}

export function rejectAgencyDocxRender(
  session: SessionInfo,
  workspace: SspWorkspace,
  renderId: string,
): Promise<SspWorkspace> {
  return agencyDocxRenderMutation(session, workspace.id, renderId, "reject");
}

export function previewAgencyDocxRender(
  workspace: SspWorkspace,
  renderId: string,
): Promise<void> {
  return downloadWorkspaceBlob(
    `/ssp-workspaces/${workspace.id}/agency-docx-renders/${encodeURIComponent(renderId)}/preview`,
    `draft-${renderId}.docx`,
  );
}

export function downloadAgencyDocxRender(
  workspace: SspWorkspace,
  renderId: string,
): Promise<void> {
  return downloadWorkspaceBlob(
    `/ssp-workspaces/${workspace.id}/agency-docx-renders/${encodeURIComponent(renderId)}/download`,
    `agency-shaped-draft-${renderId}.docx`,
  );
}
