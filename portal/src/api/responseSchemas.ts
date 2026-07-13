import { z } from "zod";

export const INVALID_RESPONSE_MESSAGE =
  "Portal API returned an unexpected response.";

const sessionSchema = z.object({
  actor_id: z.string().min(1),
  groups: z.array(z.string()),
  csrf_token: z.string().min(1),
  portal_origin: z.string().min(1),
});

const systemSchema = z.object({
  system_id: z.string().uuid(),
  display_name: z.string().min(1),
  owner_group: z.string().min(1),
  viewer_groups: z.array(z.string()),
});

const packageRevisionSchema = z.object({
  package_revision_id: z.string().uuid(),
  system_id: z.string().uuid(),
  status: z.string().min(1),
  revision_version: z.number().int().nonnegative(),
  profile_id: z.string().min(1),
  data_origin: z.string().min(1),
  sensitivity: z.string().min(1),
});

const factProposalSchema = z.object({
  fact_proposal_id: z.string().uuid(),
  json_pointer: z.string().min(1),
  proposed_value: z.any(),
  review_status: z.string().min(1),
});

const analysisRunSchema = z.object({
  run_id: z.string().uuid(),
  package_revision_id: z.string().uuid(),
  run_type: z.string().min(1),
  status: z.string().min(1),
  llm_call_count: z.number().int().nonnegative(),
  artifact_manifest_sha256: z.string().nullable(),
  requested_at: z.string().min(1),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
});

const matrixRowSchema = z.object({
  matrix_row_id: z.string().uuid(),
  assessment_item_id: z.string().min(1),
  assessment_item_type: z.string().min(1),
  model_proposed_status: z.string().min(1),
  system_status: z.string().min(1),
  finding_summary: z.string(),
});

function parseWithSchema<T>(
  schema: z.ZodType<T>,
  value: unknown,
): T | null {
  const result = schema.safeParse(value);
  return result.success ? result.data : null;
}

export function parseSessionInfo(value: unknown) {
  return parseWithSchema(sessionSchema, value);
}

export function parseSystem(value: unknown) {
  return parseWithSchema(systemSchema, value);
}

export function parsePackageRevision(value: unknown) {
  return parseWithSchema(packageRevisionSchema, value);
}

export function parseFactProposal(value: unknown) {
  return parseWithSchema(factProposalSchema, value);
}

export function parseAnalysisRun(value: unknown) {
  return parseWithSchema(analysisRunSchema, value);
}

export function parseMatrixRow(value: unknown) {
  return parseWithSchema(matrixRowSchema, value);
}

export function parseSystemList(value: unknown) {
  const schema = z.object({ items: z.array(systemSchema) });
  const parsed = parseWithSchema(schema, value);
  return parsed?.items ?? null;
}

export function parseRevisionList(value: unknown) {
  const schema = z.object({ items: z.array(packageRevisionSchema) });
  const parsed = parseWithSchema(schema, value);
  return parsed?.items ?? null;
}

export function parseProposalList(value: unknown) {
  const schema = z.object({ items: z.array(factProposalSchema) });
  const parsed = parseWithSchema(schema, value);
  return parsed?.items ?? null;
}

export function parseRunList(value: unknown) {
  const schema = z.object({ items: z.array(analysisRunSchema) });
  const parsed = parseWithSchema(schema, value);
  return parsed?.items ?? null;
}

export function parseMatrixList(value: unknown) {
  const schema = z.object({
    items: z.array(matrixRowSchema),
    total: z.number().int().nonnegative().optional(),
  });
  const parsed = parseWithSchema(schema, value);
  if (!parsed) {
    return null;
  }
  return {
    items: parsed.items,
    total: parsed.total ?? parsed.items.length,
  };
}

export function parseReadinessResponse(value: unknown) {
  const schema = z.object({
    status: z.string().optional(),
    checks: z.record(z.string()).optional(),
    error_code: z.string().optional(),
    detail: z.string().optional(),
    type: z.string().optional(),
    title: z.string().optional(),
  });
  return parseWithSchema(schema, value);
}
