import { z } from "zod";

export const INVALID_RESPONSE_MESSAGE =
  "Portal API returned an unexpected response.";

const uuidSchema = z.string().uuid();
const sha256Schema = z.string().length(64);

const sessionSchema = z.object({
  actor_id: z.string().min(1),
  groups: z.array(z.string()),
  csrf_token: z.string().min(1),
  portal_origin: z.string().min(1),
});

const systemSchema = z.object({
  system_id: uuidSchema,
  display_name: z.string().min(1),
  owner_group: z.string().min(1),
  viewer_groups: z.array(z.string()),
  archived_at: z.string().nullable().optional(),
});

const profileIdSchema = z.enum([
  "fedramp_20x_program",
  "fedramp_rev5_transition",
  "fisma_agency_security",
]);

const dataOriginSchema = z.enum([
  "synthetic",
  "redacted_nonproduction",
  "customer_production",
]);

const sensitivitySchema = z.enum([
  "public",
  "internal_unclassified",
  "customer_sensitive",
  "cui",
  "classified",
  "unknown",
]);

const packageRevisionSchema = z.object({
  package_revision_id: uuidSchema,
  system_id: uuidSchema,
  parent_revision_id: uuidSchema.nullable().optional(),
  status: z.string().min(1),
  package_preparation_status: z.enum([
    "in_progress",
    "ready_for_external_review",
  ]),
  revision_version: z.number().int().nonnegative(),
  profile_id: profileIdSchema.nullable(),
  data_origin: dataOriginSchema.nullable(),
  sensitivity: sensitivitySchema.nullable(),
  impact_level: z.string().nullable().optional(),
  certification_class: z.string().nullable().optional(),
});

const intakeReportSuggestedMetadataSchema = z.object({
  profile_id: profileIdSchema.nullable(),
  certification_class: z.enum(["B", "C"]).nullable(),
  impact_level: z.enum(["low", "moderate", "high"]).nullable(),
});

const intakeReportSchema = z.object({
  schema_version: z.string().min(1),
  object_type: z.literal("intake_report"),
  package_revision_id: uuidSchema,
  revision_version: z.number().int().positive(),
  status: z.string().min(1),
  intake_stage: z.string().min(1),
  files: z.array(
    z.object({
      artifact_id: uuidSchema,
      display_filename: z.string().min(1),
      sha256: sha256Schema,
      size_bytes: z.number().int().nonnegative(),
      artifact_kind: z.string().min(1),
      malware_scan_status: z.string().min(1),
      extraction_status: z.string().min(1),
      uploaded_at: z.string().min(1),
    }),
  ),
  human_attestation: z.object({
    data_origin: z.enum(["present", "missing"]),
    sensitivity: z.enum(["present", "missing"]),
  }),
  suggested_metadata: intakeReportSuggestedMetadataSchema,
  suggestion_sources: z.array(
    z.object({
      field: z.enum(["profile_id", "certification_class", "impact_level"]),
      proposed_value: z.unknown(),
      source_artifact_id: uuidSchema,
      source_sha256: sha256Schema,
      source_locator: z.record(z.unknown()),
      model_step_id: uuidSchema.nullable().optional(),
    }),
  ),
  gaps: z.array(
    z.object({
      code: z.string().min(1),
      message: z.string().min(1),
    }),
  ),
  conflicts: z.array(
    z.object({
      field: z.string().min(1),
      values: z.array(z.record(z.unknown())).min(2),
    }),
  ),
  omitted_chunks: z.array(
    z.object({
      artifact_id: uuidSchema,
      segment_id: z.string().min(1),
    }),
  ),
  context_complete: z.boolean(),
  map_steps: z.array(z.record(z.unknown())),
  confirmation: z.object({
    allowed: z.boolean(),
    blockers: z.array(z.string()),
  }),
  generated_at: z.string().min(1),
});

const citationSchema = z
  .object({
    source_kind: z.string().optional(),
    source_sha256: sha256Schema.optional(),
    artifact_id: z.string().optional(),
    sha256: sha256Schema.optional(),
    locator: z.record(z.unknown()).optional(),
    excerpt: z.string().optional(),
  })
  .passthrough();

const matrixRowSchema = z.object({
  matrix_row_id: uuidSchema,
  assessment_item_id: z.string().min(1),
  assessment_item_type: z.string().min(1),
  model_proposed_status: z.string().min(1),
  system_status: z.string().min(1),
  finding_summary: z.string(),
  gaps: z.array(z.string()).optional(),
  assessor_questions: z.array(z.string()).optional(),
  citations: z.array(citationSchema).optional(),
  context_complete: z.boolean().optional(),
});

const analysisRunSchema = z.object({
  run_id: uuidSchema,
  package_revision_id: uuidSchema,
  run_type: z.string().min(1),
  status: z.string().min(1),
  llm_call_count: z.number().int().nonnegative(),
  artifact_manifest_sha256: z.string().nullable(),
  requested_at: z.string().min(1),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
  error_code: z.string().nullable().optional(),
  error_retryable: z.boolean().nullable().optional(),
  parent_run_id: uuidSchema.nullable().optional(),
});

const fieldProvenanceEntrySchema = z.object({
  source_artifact_id: uuidSchema,
  source_sha256: sha256Schema,
  source_locator: z.record(z.unknown()),
  extraction_method: z.enum(["deterministic", "text", "vision", "llm_normalize"]),
  model_step_id: uuidSchema.nullable().optional(),
});

const contactPersonSchema = z.object({
  name: z.string(),
  role: z.string(),
  email: z.string(),
  organization: z.string().optional(),
  phone: z.string().optional(),
});

const packageDraftDocumentSchema = z.object({
  package: z.object({
    profile_id: z.enum([
      "fedramp_20x_program",
      "fedramp_rev5_transition",
      "fisma_agency_security",
    ]),
    title: z.string(),
    prepared_for: z.string(),
    reporting_period: z.string().nullable(),
  }),
  system: z.object({
    display_name: z.string(),
    authorization_boundary: z.string(),
    mission_summary: z.string(),
    impact_level: z.string().nullable(),
    authorization_path: z.string(),
  }),
  contacts: z.object({
    system_owner: z.array(contactPersonSchema),
    isso: z.array(contactPersonSchema),
    issm: z.array(contactPersonSchema),
    control_owners: z.array(contactPersonSchema),
    assessors: z.array(contactPersonSchema),
    approvers: z.array(contactPersonSchema),
  }),
  control_set: z.object({
    source: z.record(z.unknown()),
    tailoring: z.array(z.unknown()),
    organization_defined_parameters: z.record(z.unknown()),
    inheritance: z.array(z.unknown()),
  }),
  security_controls: z.record(
    z.object({
      implementation_status: z.string(),
      implementation_statement: z.string(),
      responsible_parties: z.array(z.string()),
      evidence_links: z.array(z.string()),
    }),
  ),
  evidence: z.record(z.record(z.unknown())),
  findings: z.record(z.record(z.unknown())),
  poam_candidates: z.record(z.record(z.unknown())),
  assessor_inputs: z.record(z.record(z.unknown())),
  privacy: z.object({
    artifacts_present: z.boolean(),
    scope_notice: z.string(),
  }),
  fedramp_20x: z.record(z.unknown()).nullable(),
  fedramp_rev5_transition: z.record(z.unknown()).nullable(),
  fisma_agency_security: z.record(z.unknown()).nullable(),
  extensions: z.record(z.unknown()),
});

const packageRevisionDraftSchema = z.object({
  schema_version: z.literal("2.0.0"),
  object_type: z.literal("package_revision_draft"),
  package_revision_id: uuidSchema,
  document_schema_version: z.string().min(1),
  document: packageDraftDocumentSchema,
  field_provenance: z.record(fieldProvenanceEntrySchema),
  updated_by: z.string().min(1),
  updated_at: z.string().min(1),
  revision_version: z.number().int().nonnegative(),
});

const preflightCheckSchema = z
  .object({
    check_id: z.string().min(1),
    severity: z.string().min(1),
    outcome: z.string().min(1),
    message: z.string().min(1),
  })
  .passthrough();

const preflightSchema = z.object({
  analysis_eligible: z.boolean(),
  export_eligible: z.boolean(),
  analysis_blockers: z.array(z.string()),
  export_blockers: z.array(z.string()),
  warnings: z.array(z.string()),
  deterministic_checks: z.array(preflightCheckSchema).optional(),
  readiness: z.object({
    numerator: z.number().int().nonnegative(),
    denominator: z.number().int().nonnegative(),
    score: z.number(),
  }),
});

const draftExportReadinessSchema = z.object({
  export_eligible: z.boolean(),
  export_blockers: z.array(z.string()),
  warnings: z.array(z.string()),
  profile_id: z.string(),
  structural_checks_passed: z.boolean(),
});

const dispositionSchema = z.object({
  matrix_row_id: uuidSchema,
  decision: z.string(),
  edited_summary: z.string().nullable(),
  notes: z.string().nullable(),
  version: z.number().int().nonnegative(),
  decided_by: z.string(),
  decided_at: z.string(),
  evidence_request_id: uuidSchema.optional(),
  poam_candidate_id: uuidSchema.optional(),
});

const reviewRevisionSchema = z.object({
  review_revision_id: uuidSchema,
  run_id: uuidSchema,
  version: z.number().int().nonnegative(),
  status: z.string(),
  dispositions: z.array(dispositionSchema),
});

const exportDraftSchema = z.object({
  export_draft_id: uuidSchema,
  review_revision_id: uuidSchema,
  payload_manifest_sha256: sha256Schema,
  status: z.string(),
});

const approvalSchema = z.object({
  approval_id: uuidSchema,
  export_draft_id: uuidSchema,
  payload_manifest_sha256: sha256Schema,
  submitted_by: z.string(),
  decided_by: z.string().nullable(),
  decision: z.string(),
  submitted_at: z.string().optional(),
  decided_at: z.string().nullable().optional(),
  expires_at: z.string(),
  reason: z.string().nullable().optional(),
});

const reviewCommentSchema = z.object({
  comment_id: uuidSchema,
  review_revision_id: uuidSchema,
  matrix_row_id: uuidSchema.nullable(),
  body: z.string().min(1),
  created_by: z.string(),
  created_at: z.string(),
});

const revisionDeltaSchema = z.object({
  parent_revision_id: uuidSchema,
  child_revision_id: uuidSchema,
  changed_artifact_ids: z.array(z.string()),
  added_artifact_ids: z.array(z.string()),
  removed_artifact_ids: z.array(z.string()),
  changed_control_ids: z.array(z.string()),
  changed_evidence_keys: z.array(z.string()),
  content_digest_changed: z.boolean(),
  generated_at: z.string(),
});

const changeAnalysisSchema = z.object({
  delta: revisionDeltaSchema,
  targeted_assessment_item_ids: z.array(z.string()),
  requires_targeted_reanalysis: z.boolean(),
});

const searchHitSchema = z
  .object({
    reference_id: z.string().optional(),
    chunk_id: sha256Schema.optional(),
    artifact_id: uuidSchema.optional(),
    sha256: sha256Schema,
    excerpt: z.string(),
    score: z.number(),
    citation: citationSchema.optional(),
  })
  .passthrough();

const searchResultsSchema = z.object({
  items: z.array(searchHitSchema),
  next_cursor: z.string().nullable().optional(),
  query: z.string().optional(),
});

const chatResponseSchema = z.object({
  answer: z.string(),
  citations: z.array(citationSchema),
  refused: z.boolean(),
  refusal_code: z.string().nullable(),
});

const artifactDescriptorSchema = z.object({
  artifact_id: z.string().min(1),
  path: z.string().min(1),
  media_type: z.string().min(1),
  sha256: sha256Schema,
  size_bytes: z.number().int().positive(),
  official_schema_id: z.string().nullable(),
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

export function parseIntakeReport(value: unknown) {
  return parseWithSchema(intakeReportSchema, value);
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

export function parseRunList(value: unknown) {
  const schema = z.object({ items: z.array(analysisRunSchema) });
  const parsed = parseWithSchema(schema, value);
  return parsed?.items ?? null;
}

export function parseMatrixList(value: unknown) {
  const schema = z.object({
    items: z.array(matrixRowSchema),
    total: z.number().int().nonnegative().optional(),
    next_cursor: z.string().nullable().optional(),
  });
  const parsed = parseWithSchema(schema, value);
  if (!parsed) {
    return null;
  }
  return {
    items: parsed.items,
    total: parsed.total ?? parsed.items.length,
    next_cursor: parsed.next_cursor ?? null,
  };
}

export function parseArtifactList(value: unknown) {
  const schema = z.object({
    items: z.array(artifactDescriptorSchema),
    next_cursor: z.string().nullable().optional(),
  });
  const parsed = parseWithSchema(schema, value);
  if (!parsed) {
    return null;
  }
  return {
    items: parsed.items,
    next_cursor: parsed.next_cursor ?? null,
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

export function parsePackageRevisionDraft(value: unknown) {
  return parseWithSchema(packageRevisionDraftSchema, value);
}

export function parsePreflight(value: unknown) {
  return parseWithSchema(preflightSchema, value);
}

export function parseDraftExportReadiness(value: unknown) {
  return parseWithSchema(draftExportReadinessSchema, value);
}

export function parseDisposition(value: unknown) {
  return parseWithSchema(dispositionSchema, value);
}

export function parseReviewRevision(value: unknown) {
  return parseWithSchema(reviewRevisionSchema, value);
}

export function parseExportDraft(value: unknown) {
  return parseWithSchema(exportDraftSchema, value);
}

export function parseApproval(value: unknown) {
  return parseWithSchema(approvalSchema, value);
}

export function parseReviewComment(value: unknown) {
  return parseWithSchema(reviewCommentSchema, value);
}

export function parseReviewCommentList(value: unknown) {
  const schema = z.object({
    items: z.array(reviewCommentSchema),
    next_cursor: z.string().nullable(),
  });
  return parseWithSchema(schema, value);
}

export function parseChangeAnalysis(value: unknown) {
  return parseWithSchema(changeAnalysisSchema, value);
}

export function parseSearchResults(value: unknown) {
  return parseWithSchema(searchResultsSchema, value);
}

export function parseChatResponse(value: unknown) {
  return parseWithSchema(chatResponseSchema, value);
}
