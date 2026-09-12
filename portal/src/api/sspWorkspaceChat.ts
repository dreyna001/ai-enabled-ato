import { z } from "zod";
import {
  ApiError,
  apiFetch,
  mutationHeaders,
  readValidatedJson,
  type ApiFetchOptions,
} from "@/api/client";
import type { SessionInfo } from "@/types";

const API_BASE = "/api/v1";

const uuidSchema = z.string().uuid();
const sha256Schema = z.string().regex(/^[a-f0-9]{64}$/);

const chatSourceSchema = z.object({
  source_id: z.string().min(1).max(128),
  label: z.string().min(1).max(500),
  target_id: z.string().min(1).max(255).nullable().optional(),
  revision_id: uuidSchema.nullable(),
  sha256: sha256Schema,
  kind: z.string().min(1).max(64),
});

const chatMessageSchema = z.object({
  message_id: uuidSchema,
  sequence: z.number().int().positive(),
  role: z.enum(["user", "assistant"]),
  content: z.string().min(1).max(12000),
  created_at: z.string().datetime({ offset: true }),
  expires_at: z.string().datetime({ offset: true }),
  sources: z.array(chatSourceSchema),
  context_fingerprint: sha256Schema,
  stale: z.boolean(),
});

const chatHistorySchema = z.object({
  workspace_id: uuidSchema,
  sequence: z.number().int().nonnegative(),
  messages: z.array(chatMessageSchema),
  has_more: z.boolean(),
  next_before_sequence: z.number().int().positive().nullable(),
});

const chatRequestSchema = z.object({
  message: z.string().trim().min(1).max(8000),
  expected_revision_id: uuidSchema,
  request_id: uuidSchema,
  expected_sequence: z.number().int().nonnegative(),
  focus: z.string().max(500).optional(),
});

export type SspChatSource = z.infer<typeof chatSourceSchema>;
export type SspChatMessage = z.infer<typeof chatMessageSchema>;
export type SspChatHistory = z.infer<typeof chatHistorySchema>;

async function requestChat(
  path: string,
  workspaceId: string,
  init: ApiFetchOptions,
): Promise<SspChatHistory> {
  const parsed = await readValidatedJson(
    await apiFetch(`${API_BASE}${path}`, init),
    (value) => {
      const result = chatHistorySchema.safeParse(value);
      return result.success ? result.data : null;
    },
  );
  if (parsed.workspace_id !== workspaceId) {
    throw new ApiError(
      502,
      "The SSP chat service returned a mismatched workspace.",
      "invalid_response",
    );
  }
  return parsed;
}

export async function getSspWorkspaceChat(
  workspaceId: string,
  options: {
    beforeSequence?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<SspChatHistory> {
  uuidSchema.parse(workspaceId);
  const params = new URLSearchParams();
  if (options.beforeSequence !== undefined && options.beforeSequence >= 1) {
    params.set("before_sequence", String(Math.trunc(options.beforeSequence)));
  }
  const limit = Math.min(100, Math.max(1, Math.trunc(options.limit ?? 40)));
  params.set("limit", String(limit));
  const query = params.toString();
  return requestChat(
    `/ssp-workspaces/${encodeURIComponent(workspaceId)}/chat?${query}`,
    workspaceId,
    { method: "GET", credentials: "include", signal: options.signal },
  );
}

export async function postSspWorkspaceChat(
  session: SessionInfo,
  workspaceId: string,
  input: {
    message: string;
    expectedRevisionId: string;
    requestId: string;
    expectedSequence: number;
    focus?: string;
    signal?: AbortSignal;
  },
): Promise<SspChatHistory> {
  uuidSchema.parse(workspaceId);
  const payload = chatRequestSchema.parse({
    message: input.message,
    expected_revision_id: input.expectedRevisionId,
    request_id: input.requestId,
    expected_sequence: input.expectedSequence,
    ...(input.focus ? { focus: input.focus.slice(0, 500) } : {}),
  });
  return requestChat(
    `/ssp-workspaces/${encodeURIComponent(workspaceId)}/chat`,
    workspaceId,
    {
      method: "POST",
      credentials: "include",
      timeoutMs: 120_000,
      signal: input.signal,
      headers: {
        "Content-Type": "application/json",
        ...mutationHeaders(session),
      },
      body: JSON.stringify(payload),
    },
  );
}
