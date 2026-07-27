import { z } from "zod";
import type { SessionInfo, System } from "@/types";
import type {
  AgentContext,
  ControlStatementChange,
  SspSectionChange,
  SspWorkspace,
} from "@/sspWorkspaceTypes";
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
    impact_level: z.enum(["low", "moderate", "high"]),
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
});

export type SspProfile = z.infer<typeof profileSchema>;

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

export function mapWorkspaceEnvelope(raw: unknown): SspWorkspace {
  const envelope = envelopeSchema.parse(raw);
  const revision = envelope.current_revision;
  const content = revision.content;
  const facts = content.facts;
  const satisfied = new Set(envelope.satisfied_requirement_ids);
  const latestApproval = envelope.approvals[0];

  return {
    id: envelope.workspace_id,
    name: envelope.system.display_name,
    purpose: factValue(facts, "system.purpose"),
    hosting: factValue(facts, "system.hosting_model"),
    impactLevel: envelope.profile.impact_level,
    authorizationPath: factValue(facts, "system.authorization_path"),
    profile: {
      id: envelope.profile.profile_version_id,
      name: envelope.profile.profile_id,
      version: envelope.profile.version,
      baseline: (
        envelope.profile.impact_level.charAt(0).toUpperCase() +
        envelope.profile.impact_level.slice(1)
      ) as "Low" | "Moderate" | "High",
    },
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
  impactLevel: "low" | "moderate" | "high",
): Promise<SspWorkspace> {
  const result = await apiRequest("/ssp-workspaces", envelopeSchema, {
    method: "POST",
    headers: mutationHeaders(session),
    body: JSON.stringify({
      system_id: system.system_id,
      profile_version_id: profileVersionId,
      impact_level: impactLevel,
    }),
  });
  return mapWorkspaceEnvelope(result);
}

async function workspaceMutation(
  session: SessionInfo,
  workspaceId: string,
  path: string,
  method: "POST" | "PATCH",
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
  format: "docx" | "json",
): Promise<void> {
  const params = new URLSearchParams({
    revision_id: workspace.revisionId,
    include_open_questions: "true",
  });
  const response = await fetch(
    `${API_BASE}/ssp-workspaces/${workspace.id}/exports/${format}?${params}`,
    { credentials: "include" },
  );
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `ssp-${workspace.revisionId}.${format}`;
  anchor.click();
  URL.revokeObjectURL(url);
}
