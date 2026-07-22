import {
  IntakeConflictList,
  type IntakeConflictManualEdit,
  type IntakeConflictResolution,
  type IntakeReportConflictLike,
} from "@/components/IntakeConflictList";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { sanitizeDisplayFilename } from "@/utils/downloadFilename";
import { toTitleCaseWords } from "@/utils/labelFormatting";

export type IntakeStage =
  | "no_artifacts"
  | "upload_open"
  | "malware_scan"
  | "extract"
  | "intake_map"
  | "intake_reduce"
  | "awaiting_human_review"
  | "confirmed"
  | "blocked"
  | "archived";

export type IntakeReportFileLike = {
  artifact_id: string;
  display_filename: string;
  sha256: string;
  size_bytes: number;
  artifact_kind: string;
  malware_scan_status: "pending" | "clean" | "infected" | "error";
  extraction_status: "pending" | "succeeded" | "failed" | "not_applicable";
  uploaded_at: string;
};

export type IntakeReportHumanAttestationLike = {
  data_origin: "present" | "missing";
  sensitivity: "present" | "missing";
};

export type IntakeReportSuggestedMetadataLike = {
  profile_id: string | null;
  certification_class: "B" | "C" | null;
  impact_level: "low" | "moderate" | "high" | null;
};

export type IntakeReportGapLike = {
  code: string;
  message: string;
};

export type IntakeReportOmittedChunkLike = {
  artifact_id: string;
  segment_id: string;
};

export type IntakeReportMapStepSummaryLike = {
  step_id: string;
  step_key: string;
  status:
    | "reserved"
    | "running"
    | "completed"
    | "policy_blocked"
    | "failed"
    | "reconciliation_required";
  validation_outcome: string | null;
  llm_call_count: number;
  error_code: string | null;
};

export type IntakeReportConfirmationLike = {
  allowed: boolean;
  blockers: string[];
};

export type IntakeReportLike = {
  package_revision_id: string;
  revision_version: number;
  status: string;
  intake_stage: IntakeStage;
  files: IntakeReportFileLike[];
  human_attestation: IntakeReportHumanAttestationLike;
  suggested_metadata: IntakeReportSuggestedMetadataLike;
  gaps: IntakeReportGapLike[];
  conflicts: IntakeReportConflictLike[];
  omitted_chunks: IntakeReportOmittedChunkLike[];
  context_complete: boolean;
  map_steps: IntakeReportMapStepSummaryLike[];
  confirmation: IntakeReportConfirmationLike;
  generated_at: string;
};

export type IntakeReadinessPanelProps = {
  report: IntakeReportLike | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  conflictControlsDisabled?: boolean;
  onSelectConflictCandidate?: (resolution: IntakeConflictResolution) => void;
  onManualConflictEdit?: (resolution: IntakeConflictManualEdit) => void;
};

const INTAKE_STAGE_LABELS: Record<IntakeStage, string> = {
  no_artifacts: "No artifacts uploaded",
  upload_open: "Upload open",
  malware_scan: "Malware scan",
  extract: "Extracting content",
  intake_map: "Intake map in progress",
  intake_reduce: "Intake reduce in progress",
  awaiting_human_review: "Awaiting human review",
  confirmed: "Confirmed",
  blocked: "Blocked",
  archived: "Archived",
};

const MALWARE_STATUS_VARIANT = {
  pending: "secondary",
  clean: "success",
  infected: "destructive",
  error: "warning",
} as const;

const EXTRACTION_STATUS_VARIANT = {
  pending: "secondary",
  succeeded: "success",
  failed: "destructive",
  not_applicable: "muted",
} as const;

const MAP_STATUS_VARIANT = {
  reserved: "muted",
  running: "warning",
  completed: "success",
  policy_blocked: "destructive",
  failed: "destructive",
  reconciliation_required: "warning",
} as const;

function attestationLabel(state: "present" | "missing"): string {
  return state === "present" ? "Provided by operator" : "Not yet provided";
}

function attestationVariant(state: "present" | "missing"): "success" | "warning" {
  return state === "present" ? "success" : "warning";
}

function countFileStatuses(files: IntakeReportFileLike[]) {
  const cleanCount = files.filter((file) => file.malware_scan_status === "clean").length;
  const extractedCount = files.filter((file) => file.extraction_status === "succeeded").length;
  return { cleanCount, extractedCount };
}

function isIntakeCompleted(report: IntakeReportLike): boolean {
  return report.status === "ready" || report.intake_stage === "confirmed";
}

function IntakeReadinessLoading() {
  return (
    <Card aria-busy="true" aria-label="Loading intake readiness" className="bg-muted/10">
      <CardHeader className="pb-2">
        <h2 className="text-base font-semibold leading-none tracking-tight">Intake readiness</h2>
        <CardDescription>Loading intake report…</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-20 w-full rounded-sm" />
        <Skeleton className="h-16 w-full rounded-sm" />
      </CardContent>
    </Card>
  );
}

function IntakeReadinessError({
  error,
  onRetry,
}: {
  error: string;
  onRetry?: () => void;
}) {
  return (
    <Card className="border-destructive/30">
      <CardHeader className="pb-2">
        <h2 className="text-base font-semibold leading-none tracking-tight">Intake readiness</h2>
        <CardDescription role="alert">{error}</CardDescription>
      </CardHeader>
      {onRetry ? (
        <CardContent>
          <Button size="sm" type="button" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </CardContent>
      ) : null}
    </Card>
  );
}

export function IntakeReadinessPanel({
  report,
  loading = false,
  error = null,
  onRetry,
  conflictControlsDisabled = false,
  onSelectConflictCandidate,
  onManualConflictEdit,
}: IntakeReadinessPanelProps) {
  if (loading && !report) {
    return <IntakeReadinessLoading />;
  }

  if (error) {
    return <IntakeReadinessError error={error} onRetry={onRetry} />;
  }

  if (!report) {
    return (
      <Card className="bg-muted/10">
        <CardHeader className="pb-2">
          <h2 className="text-base font-semibold leading-none tracking-tight">Intake readiness</h2>
          <CardDescription>
            Upload artifacts and finalize intake to generate a readiness report.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            description="No intake report is available for this revision yet."
            size="sm"
            title="Intake report unavailable"
          />
        </CardContent>
      </Card>
    );
  }

  const stageLabel = INTAKE_STAGE_LABELS[report.intake_stage] ?? "Processing intake";
  const { cleanCount, extractedCount } = countFileStatuses(report.files);
  const contextGapVisible = !report.context_complete;
  const confirmBlockers = report.confirmation.allowed ? [] : report.confirmation.blockers;
  const intakeCompleted = isIntakeCompleted(report);

  return (
    <Card aria-labelledby="intake-readiness-title" className="bg-muted/10" id="intake-readiness">
      <CardHeader className="pb-2">
        <h2 className="text-base font-semibold leading-none tracking-tight" id="intake-readiness-title">
          Intake readiness
        </h2>
        <CardDescription>
          {intakeCompleted
            ? "Intake is complete. Review the file inventory and any reported gaps below."
            : "Review uploaded files, human attestation, and gaps before confirming the package."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <section aria-labelledby="intake-stage-heading" className="space-y-2">
          <h3 className="text-sm font-medium" id="intake-stage-heading">
            Intake stage
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{toTitleCaseWords(report.status)}</Badge>
            <span className="text-sm text-foreground">{stageLabel}</span>
          </div>
          <p className="text-xs text-muted-foreground" role="status">
            Report generated {new Date(report.generated_at).toLocaleString()}
          </p>
        </section>

        <section aria-labelledby="intake-files-heading" className="space-y-2">
          <h3 className="text-sm font-medium" id="intake-files-heading">
            File inventory
          </h3>
          <p className="text-sm text-muted-foreground" role="status">
            {report.files.length} file(s) uploaded. {cleanCount} clean scan(s),{" "}
            {extractedCount} successful extraction(s).
          </p>
          {report.files.length > 0 ? (
            <ul className="space-y-2">
              {report.files.map((file) => (
                <li
                  className="rounded-sm border border-border bg-card px-3 py-2"
                  key={file.artifact_id}
                >
                  <p className="text-sm font-medium text-foreground">
                    {sanitizeDisplayFilename(file.display_filename)}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge variant={MALWARE_STATUS_VARIANT[file.malware_scan_status]}>
                      Scan: {toTitleCaseWords(file.malware_scan_status)}
                    </Badge>
                    <Badge variant={EXTRACTION_STATUS_VARIANT[file.extraction_status]}>
                      Extract: {toTitleCaseWords(file.extraction_status.replaceAll("_", " "))}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {file.artifact_kind} · {(file.size_bytes / 1024).toFixed(1)} KB
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              description="Upload at least one artifact to begin intake."
              size="sm"
              title="No files received"
            />
          )}
        </section>

        {!intakeCompleted ? (
          <section aria-labelledby="intake-attestation-heading" className="space-y-2">
            <h3 className="text-sm font-medium" id="intake-attestation-heading">
              Human attestation
            </h3>
            <p className="text-sm text-muted-foreground">
              Data origin and sensitivity are operator-provided only. Intake never suggests
              these values.
            </p>
            <dl className="grid gap-3 rounded-sm border border-border bg-card px-3 py-3 text-sm md:grid-cols-2">
              <div>
                <dt className="font-medium text-foreground">Data origin</dt>
                <dd className="mt-2">
                  <Badge variant={attestationVariant(report.human_attestation.data_origin)}>
                    {attestationLabel(report.human_attestation.data_origin)}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Sensitivity</dt>
                <dd className="mt-2">
                  <Badge variant={attestationVariant(report.human_attestation.sensitivity)}>
                    {attestationLabel(report.human_attestation.sensitivity)}
                  </Badge>
                </dd>
              </div>
            </dl>
          </section>
        ) : null}

        <section aria-labelledby="intake-gaps-heading" className="space-y-2">
          <h3 className="text-sm font-medium" id="intake-gaps-heading">
            Deterministic gaps
          </h3>
          {report.gaps.length > 0 ? (
            <ul className="space-y-2">
              {report.gaps.map((gap) => (
                <li
                  className="rounded-sm border border-l-4 border-l-amber-500 border-border bg-card px-3 py-2"
                  key={gap.code}
                >
                  <p className="font-medium text-foreground">{gap.message}</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">{gap.code}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground" role="status">
              No deterministic intake gaps reported.
            </p>
          )}
        </section>

        <section aria-labelledby="intake-context-heading" className="space-y-2">
          <h3 className="text-sm font-medium" id="intake-context-heading">
            Context completeness
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={report.context_complete ? "success" : "warning"}>
              {report.context_complete ? "Complete" : "Incomplete"}
            </Badge>
            {contextGapVisible ? (
              <p className="text-sm text-muted-foreground" role="status">
                Intake did not read all relevant context. This is a visible gap and may limit
                downstream analysis support.
              </p>
            ) : (
              <p className="text-sm text-muted-foreground" role="status">
                Intake reported sufficient context coverage for mapped fields.
              </p>
            )}
          </div>
        </section>

        {report.omitted_chunks.length > 0 ? (
          <section aria-labelledby="intake-omitted-heading" className="space-y-2">
            <h3 className="text-sm font-medium" id="intake-omitted-heading">
              Omitted chunks
            </h3>
            <ul className="space-y-1 text-sm text-muted-foreground">
              {report.omitted_chunks.slice(0, 8).map((chunk) => (
                <li key={`${chunk.artifact_id}-${chunk.segment_id}`}>
                  Artifact {chunk.artifact_id.slice(0, 8)}… · segment{" "}
                  {chunk.segment_id.slice(0, 64)}
                </li>
              ))}
              {report.omitted_chunks.length > 8 ? (
                <li>{report.omitted_chunks.length - 8} additional omitted chunk(s)</li>
              ) : null}
            </ul>
          </section>
        ) : null}

        {report.map_steps.length > 0 ? (
          <section aria-labelledby="intake-map-heading" className="space-y-2">
            <h3 className="text-sm font-medium" id="intake-map-heading">
              MAP status
            </h3>
            <ul className="space-y-2">
              {report.map_steps.map((step) => (
                <li
                  className="rounded-sm border border-border bg-card px-3 py-2"
                  key={step.step_id}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{step.step_key}</span>
                    <Badge variant={MAP_STATUS_VARIANT[step.status]}>
                      {toTitleCaseWords(step.status.replaceAll("_", " "))}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {step.llm_call_count} LLM call(s)
                    {step.validation_outcome
                      ? ` · validation ${step.validation_outcome}`
                      : ""}
                    {step.error_code ? ` · ${step.error_code}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {!intakeCompleted ? (
          <IntakeConflictList
            conflicts={report.conflicts}
            disabled={conflictControlsDisabled}
            onManualEdit={onManualConflictEdit}
            onSelectCandidate={onSelectConflictCandidate}
          />
        ) : null}

        {!intakeCompleted ? (
          <section aria-labelledby="intake-confirm-heading" className="space-y-2">
            <h3 className="text-sm font-medium" id="intake-confirm-heading">
              Confirm blockers
            </h3>
            {confirmBlockers.length > 0 ? (
              <ul className="space-y-2">
                {confirmBlockers.map((blocker) => (
                  <li
                    className="rounded-sm border border-l-4 border-l-destructive border-border bg-card px-3 py-2"
                    key={blocker}
                  >
                    <p className="font-medium text-foreground">
                      Resolve before confirming package
                    </p>
                    <p className="mt-1 font-mono text-xs text-muted-foreground">{blocker}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground" role="status">
                {report.confirmation.allowed
                  ? "No confirmation blockers reported."
                  : "Confirmation is blocked, but no blocker codes were returned."}
              </p>
            )}
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}
