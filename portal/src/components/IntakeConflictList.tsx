import { Button } from "@/components/ui/button";
import { sanitizeDisplayFilename } from "@/utils/downloadFilename";
import { toTitleCaseWords } from "@/utils/labelFormatting";

const MAX_PLAIN_TEXT_LENGTH = 240;

const BLOCKED_CANDIDATE_KEYS = new Set([
  "prompt",
  "prompt_text",
  "prompt_sha256",
  "response",
  "response_text",
  "response_sha256",
  "fact_bundle_sha256",
  "storage_key",
  "blob_key",
  "object_key",
  "secret",
  "api_key",
  "password",
  "token",
  "dsn",
  "credential",
  "credentials",
  "raw_html",
  "html",
  "innerhtml",
]);

const VALUE_KEYS = ["value", "proposed_value", "display_value"] as const;

export type IntakeReportConflictLike = {
  field: string;
  values: unknown[];
};

export type IntakeConflictResolution = {
  field: string;
  candidateIndex: number;
};

export type IntakeConflictManualEdit = {
  field: string;
};

export type SanitizedConflictCandidate = {
  valueLabel: string;
  sourceLabel: string;
};

export type IntakeConflictListProps = {
  conflicts: IntakeReportConflictLike[];
  disabled?: boolean;
  onSelectCandidate?: (resolution: IntakeConflictResolution) => void;
  onManualEdit?: (resolution: IntakeConflictManualEdit) => void;
};

function sanitizePlainText(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, MAX_PLAIN_TEXT_LENGTH);
}

function formatScalarCandidateValue(raw: unknown): string {
  if (raw === null || raw === undefined) {
    return "No value";
  }
  if (typeof raw === "string") {
    return sanitizePlainText(raw);
  }
  if (typeof raw === "number" || typeof raw === "boolean") {
    return String(raw);
  }
  return "Unsupported value shape";
}

function readSafeString(
  record: Record<string, unknown>,
  key: string,
): string | null {
  if (BLOCKED_CANDIDATE_KEYS.has(key)) {
    return null;
  }
  const raw = record[key];
  if (typeof raw !== "string" || raw.length === 0) {
    return null;
  }
  return sanitizePlainText(raw);
}

function summarizeSourceLocator(locator: unknown): string | null {
  if (!locator || typeof locator !== "object" || Array.isArray(locator)) {
    return null;
  }
  const record = locator as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof record.page === "number") {
    parts.push(`page ${record.page}`);
  }
  if (typeof record.segment_index === "number") {
    parts.push(`segment ${record.segment_index}`);
  }
  if (typeof record.segment_id === "string") {
    parts.push(`segment ${sanitizePlainText(record.segment_id)}`);
  }
  return parts.length > 0 ? parts.join(", ") : null;
}

/** Sanitize one conflict candidate object into bounded plain-text labels. */
export function sanitizeConflictCandidate(candidate: unknown): SanitizedConflictCandidate {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return {
      valueLabel: "Unsupported value shape",
      sourceLabel: "Source unavailable",
    };
  }

  const record = candidate as Record<string, unknown>;
  let valueLabel = "Unknown value";
  for (const key of VALUE_KEYS) {
    if (key in record) {
      valueLabel = formatScalarCandidateValue(record[key]);
      break;
    }
  }

  const explicitDescriptor = readSafeString(record, "source_descriptor");
  if (explicitDescriptor) {
    return { valueLabel, sourceLabel: explicitDescriptor };
  }

  const sourceParts: string[] = [];
  const filename =
    readSafeString(record, "display_filename") ??
    readSafeString(record, "source_filename");
  if (filename) {
    sourceParts.push(sanitizeDisplayFilename(filename));
  }

  const extractionMethod = readSafeString(record, "extraction_method");
  if (extractionMethod) {
    sourceParts.push(extractionMethod);
  }

  const artifactId = readSafeString(record, "source_artifact_id");
  if (artifactId) {
    sourceParts.push(`Artifact ${artifactId.slice(0, 8)}…`);
  }

  const locatorSummary = summarizeSourceLocator(record.source_locator);
  if (locatorSummary) {
    sourceParts.push(locatorSummary);
  }

  return {
    valueLabel,
    sourceLabel: sourceParts.length > 0 ? sourceParts.join(" · ") : "Source unavailable",
  };
}

function conflictFieldLabel(field: string): string {
  const trimmed = field.trim();
  if (!trimmed) {
    return "Unknown field";
  }
  if (trimmed.startsWith("/")) {
    return trimmed;
  }
  return toTitleCaseWords(trimmed.replaceAll(".", " "));
}

function conflictStableKey(field: string, index: number): string {
  return `conflict-${index}-${field}`;
}

export function IntakeConflictList({
  conflicts,
  disabled = false,
  onSelectCandidate,
  onManualEdit,
}: IntakeConflictListProps) {
  if (conflicts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        No unresolved intake conflicts.
      </p>
    );
  }

  return (
    <section aria-labelledby="intake-conflicts-heading" className="space-y-4">
      <div>
        <h3 className="text-sm font-medium" id="intake-conflicts-heading">
          Resolve conflicts
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Choose one extracted value or edit the field manually. Suggestions never
          replace human attestation fields.
        </p>
      </div>
      <ul className="space-y-4">
        {conflicts.map((conflict, conflictIndex) => {
          const conflictKey = conflictStableKey(conflict.field, conflictIndex);
          const fieldLabel = conflictFieldLabel(conflict.field);

          return (
            <li
              className="rounded-sm border border-border bg-card px-3 py-3"
              key={conflictKey}
            >
              <p className="font-medium text-foreground">{fieldLabel}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{conflict.field}</p>
              <div
                aria-label={`Candidate values for ${fieldLabel}`}
                className="mt-3 grid gap-3 md:grid-cols-2"
                role="group"
              >
                {conflict.values.map((candidate, candidateIndex) => {
                  const sanitized = sanitizeConflictCandidate(candidate);
                  const candidateKey = `${conflictKey}-candidate-${candidateIndex}`;

                  return (
                    <div
                      className="space-y-2 rounded-sm border border-border/80 bg-muted/10 px-3 py-3"
                      key={candidateKey}
                    >
                      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Candidate {candidateIndex + 1}
                      </p>
                      <dl className="space-y-2 text-sm">
                        <div>
                          <dt className="font-medium text-foreground">Value</dt>
                          <dd className="mt-1 break-words text-foreground">{sanitized.valueLabel}</dd>
                        </div>
                        <div>
                          <dt className="font-medium text-foreground">Source</dt>
                          <dd className="mt-1 break-words text-muted-foreground">
                            {sanitized.sourceLabel}
                          </dd>
                        </div>
                      </dl>
                      <Button
                        aria-label={`Use candidate ${candidateIndex + 1} for ${fieldLabel}`}
                        disabled={disabled || !onSelectCandidate}
                        size="sm"
                        type="button"
                        variant="outline"
                        onClick={() =>
                          onSelectCandidate?.({
                            field: conflict.field,
                            candidateIndex,
                          })
                        }
                      >
                        Use this value
                      </Button>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3">
                <Button
                  aria-label={`Edit ${fieldLabel} manually`}
                  disabled={disabled || !onManualEdit}
                  size="sm"
                  type="button"
                  variant="secondary"
                  onClick={() => onManualEdit?.({ field: conflict.field })}
                >
                  Edit manually
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
