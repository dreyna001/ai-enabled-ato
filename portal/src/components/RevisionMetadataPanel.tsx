import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import {
  ApiError,
  getIntakeReport,
  getRevision,
  isCancelledRequest,
  patchRevisionMetadata,
  revisionEtag,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import type {
  IntakeReport,
  PackageRevision,
  RevisionMetadataSaveState,
  SessionInfo,
} from "@/types";
import { formatProblemError } from "@/utils/formatProblemError";
import {
  CERTIFICATION_CLASS_OPTIONS,
  DATA_ORIGIN_OPTIONS,
  IMPACT_LEVEL_OPTIONS,
  PROFILE_OPTIONS,
  SENSITIVITY_OPTIONS,
} from "@/utils/revisionDefaults";
import {
  applySuggestedMetadata,
  buildMetadataPatchPayload,
  isRevisionMetadataEditable,
  metadataFormEquals,
  metadataValuesFromRevision,
  normalizeMetadataFormForProfile,
  type RevisionMetadataFormValues,
  validateMetadataForm,
} from "@/utils/revisionMetadata";

export type RevisionMetadataPanelProps = {
  session: SessionInfo;
  revision: PackageRevision;
  onRevisionUpdated: (revision: PackageRevision, etag: string) => void;
  onMetadataStateChange?: (state: RevisionMetadataSaveState) => void;
  onIntakeReportChange?: (report: IntakeReport | null) => void;
  onReportLoadStateChange?: (state: { loading: boolean; error: string }) => void;
  refreshKey?: string;
};

function suggestionHint(
  field: "profile_id" | "certification_class" | "impact_level",
  report: IntakeReport | null,
  revisionValue: string | null | undefined,
): string | null {
  if (revisionValue != null && revisionValue !== "") {
    return null;
  }

  const suggestion = report?.suggested_metadata[field];
  const valid =
    field === "profile_id"
      ? suggestion === "fedramp_20x_program" ||
        suggestion === "fedramp_rev5_transition" ||
        suggestion === "fisma_agency_security"
      : field === "certification_class"
        ? suggestion === "B" || suggestion === "C"
        : suggestion === "low" ||
          suggestion === "moderate" ||
          suggestion === "high";
  if (!valid) {
    return null;
  }
  return "Suggested by intake. Review and save to apply.";
}

export function RevisionMetadataPanel({
  session,
  revision,
  onRevisionUpdated,
  onMetadataStateChange,
  onIntakeReportChange,
  onReportLoadStateChange,
  refreshKey = "",
}: RevisionMetadataPanelProps) {
  const editable = isRevisionMetadataEditable(revision);
  const [intakeReport, setIntakeReport] = useState<IntakeReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportError, setReportError] = useState("");
  const [savedValues, setSavedValues] = useState<RevisionMetadataFormValues>(() =>
    metadataValuesFromRevision(revision),
  );
  const [formValues, setFormValues] = useState<RevisionMetadataFormValues>(() =>
    metadataValuesFromRevision(revision),
  );
  const [etag, setEtag] = useState(() => revisionEtag(revision.revision_version));
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloadError, setReloadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [staleConflict, setStaleConflict] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const syncedRevisionRef = useRef({
    revisionId: revision.package_revision_id,
    revisionVersion: revision.revision_version,
  });

  const validationIssues = useMemo(() => validateMetadataForm(formValues), [formValues]);
  const isDirty = useMemo(
    () => !metadataFormEquals(savedValues, formValues),
    [formValues, savedValues],
  );
  const isDirtyRef = useRef(isDirty);
  isDirtyRef.current = isDirty;
  const isComplete = useMemo(() => {
    if (isDirty) {
      return false;
    }
    return validateMetadataForm(savedValues).length === 0;
  }, [isDirty, savedValues]);

  useEffect(() => {
    onMetadataStateChange?.({
      isDirty,
      saving: saving || reloading,
      staleConflict,
      isComplete,
    });
  }, [isComplete, isDirty, onMetadataStateChange, reloading, saving, staleConflict]);

  const syncFromRevision = useCallback(
    (
      nextRevision: PackageRevision,
      suggestions?: IntakeReport["suggested_metadata"] | null,
      nextEtag = revisionEtag(nextRevision.revision_version),
    ) => {
      const revisionValues = metadataValuesFromRevision(nextRevision);
      syncedRevisionRef.current = {
        revisionId: nextRevision.package_revision_id,
        revisionVersion: nextRevision.revision_version,
      };
      setSavedValues(revisionValues);
      setFormValues(
        suggestions ? applySuggestedMetadata(nextRevision, suggestions) : revisionValues,
      );
      setEtag(nextEtag);
    },
    [],
  );

  useEffect(() => {
    onReportLoadStateChange?.({
      loading: reportLoading,
      error: reportError,
    });
  }, [onReportLoadStateChange, reportError, reportLoading]);

  const loadIntakeReport = useCallback(
    async (signal?: AbortSignal) => {
      setReportLoading(true);
      setReportError("");
      try {
        const report = await getIntakeReport(revision.package_revision_id, { signal });
        setIntakeReport(report);
        onIntakeReportChange?.(report);
        if (!isDirtyRef.current) {
          const revisionValues = metadataValuesFromRevision(revision);
          setSavedValues(revisionValues);
          setFormValues(applySuggestedMetadata(revision, report.suggested_metadata));
        }
      } catch (err) {
        if (isCancelledRequest(err, signal)) {
          return;
        }
        setIntakeReport(null);
        onIntakeReportChange?.(null);
        setReportError(formatProblemError(err));
      } finally {
        setReportLoading(false);
      }
    },
    [onIntakeReportChange, revision],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadIntakeReport(controller.signal);
    return () => controller.abort();
  }, [loadIntakeReport, refreshKey]);

  useEffect(() => {
    if (isDirty) {
      return;
    }
    const synced = syncedRevisionRef.current;
    if (
      revision.package_revision_id === synced.revisionId &&
      revision.revision_version < synced.revisionVersion
    ) {
      return;
    }
    syncFromRevision(revision, intakeReport?.suggested_metadata ?? null);
  }, [intakeReport?.suggested_metadata, isDirty, revision, syncFromRevision]);

  const updateField = <K extends keyof RevisionMetadataFormValues>(
    key: K,
    value: RevisionMetadataFormValues[K],
  ) => {
    setFormValues((current) =>
      normalizeMetadataFormForProfile({
        ...current,
        [key]: value,
      }),
    );
    setSaveError("");
    setSavedNotice(false);
  };

  const handleReload = async () => {
    if (saving || reloading) {
      return;
    }

    setReloading(true);
    try {
      const [latestRevision, latestReport] = await Promise.all([
        getRevision(revision.package_revision_id),
        getIntakeReport(revision.package_revision_id),
      ]);
      syncFromRevision(latestRevision, latestReport.suggested_metadata);
      setIntakeReport(latestReport);
      setSaveError("");
      setReloadError("");
      setStaleConflict(false);
      setSavedNotice(false);
      onRevisionUpdated(
        latestRevision,
        revisionEtag(latestRevision.revision_version),
      );
      onIntakeReportChange?.(latestReport);
    } catch (err) {
      setReloadError(
        `Could not reload current revision metadata. ${formatProblemError(err)}`,
      );
    } finally {
      setReloading(false);
    }
  };

  const handleSave = async () => {
    if (saving || reloading) {
      return;
    }

    const normalized = normalizeMetadataFormForProfile(formValues);
    const issues = validateMetadataForm(normalized);
    if (issues.length > 0) {
      setSaveError(issues.join("\n"));
      return;
    }
    const patch = buildMetadataPatchPayload(savedValues, normalized);
    if (!patch) {
      return;
    }
    setSaving(true);
    setSaveError("");
    setStaleConflict(false);
    setSavedNotice(false);
    try {
      const result = await patchRevisionMetadata(
        session,
        revision.package_revision_id,
        etag,
        patch,
      );
      syncFromRevision(result.revision, null, result.etag);
      setSavedNotice(true);
      onRevisionUpdated(result.revision, result.etag);
      await loadIntakeReport();
    } catch (err) {
      if (err instanceof ApiError && (err.status === 412 || err.status === 428)) {
        setStaleConflict(true);
        setSaveError(
          "Revision metadata changed on the server. Reload the latest values before saving again.",
        );
      } else if (err instanceof ApiError && err.status === 409) {
        setStaleConflict(true);
        setSaveError(
          "Metadata could not be saved because the revision state changed. Reload and retry.",
        );
      } else {
        setSaveError(formatProblemError(err));
      }
    } finally {
      setSaving(false);
    }
  };

  if (reportLoading && !intakeReport) {
    return (
      <Card className="bg-muted/10" aria-busy="true">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Revision metadata</CardTitle>
          <CardDescription>Loading intake suggestions and attestation fields…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (reportError && !intakeReport) {
    return (
      <Card className="border-destructive/30">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Revision metadata</CardTitle>
          <CardDescription>{reportError}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button type="button" size="sm" variant="outline" onClick={() => void loadIntakeReport()}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card id="revision-metadata-panel" className="bg-muted/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Revision metadata</CardTitle>
        <CardDescription>
          Set authorization path and required human attestation after upload. Model suggestions apply
          only when a field is still unset on the revision.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {staleConflict ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
            <span className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-destructive" aria-hidden="true" />
              {saveError ||
                "Revision metadata changed on the server. Reload before saving again."}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={saving || reloading}
              onClick={() => void handleReload()}
            >
              {reloading ? "Reloading…" : "Reload metadata"}
            </Button>
          </div>
        ) : null}

        {reloadError ? (
          <p className="text-sm text-destructive" role="alert">
            {reloadError}
          </p>
        ) : null}

        {saveError && !staleConflict ? (
          <p className="whitespace-pre-line text-sm text-destructive">{saveError}</p>
        ) : null}

        {savedNotice && !isDirty && !saveError ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Metadata saved.
          </p>
        ) : null}

        {isDirty ? (
          <p className="text-sm text-foreground">Unsaved metadata changes.</p>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <MetadataSelectField
            id="revision-profile-id"
            label="Profile"
            value={formValues.profile_id}
            disabled={!editable || saving || reloading}
            hint={suggestionHint("profile_id", intakeReport, revision.profile_id)}
            onChange={(value) => updateField("profile_id", value as RevisionMetadataFormValues["profile_id"])}
            options={PROFILE_OPTIONS.map((option) => ({
              value: option.id,
              label: option.label,
            }))}
          />

          {formValues.profile_id === "fedramp_20x_program" ? (
            <MetadataSelectField
              id="revision-certification-class"
              label="Certification class"
              value={formValues.certification_class}
              disabled={!editable || saving || reloading}
              hint={suggestionHint(
                "certification_class",
                intakeReport,
                revision.certification_class,
              )}
              onChange={(value) =>
                updateField(
                  "certification_class",
                  value as RevisionMetadataFormValues["certification_class"],
                )
              }
              options={CERTIFICATION_CLASS_OPTIONS.map((option) => ({
                value: option.id,
                label: option.label,
              }))}
            />
          ) : (
            <MetadataSelectField
              id="revision-impact-level"
              label="Impact level"
              value={formValues.impact_level}
              disabled={!editable || saving || reloading || !formValues.profile_id}
              hint={suggestionHint("impact_level", intakeReport, revision.impact_level)}
              onChange={(value) =>
                updateField("impact_level", value as RevisionMetadataFormValues["impact_level"])
              }
              options={IMPACT_LEVEL_OPTIONS.map((option) => ({
                value: option.id,
                label: option.label,
              }))}
            />
          )}

          <MetadataSelectField
            id="revision-data-origin"
            label="Data origin"
            value={formValues.data_origin}
            disabled={!editable || saving || reloading}
            requiredHumanAttestation
            onChange={(value) =>
              updateField("data_origin", value as RevisionMetadataFormValues["data_origin"])
            }
            options={DATA_ORIGIN_OPTIONS.map((option) => ({
              value: option.id,
              label: option.label,
            }))}
          />

          <MetadataSelectField
            id="revision-sensitivity"
            label="Sensitivity"
            value={formValues.sensitivity}
            disabled={!editable || saving || reloading}
            requiredHumanAttestation
            onChange={(value) =>
              updateField("sensitivity", value as RevisionMetadataFormValues["sensitivity"])
            }
            options={SENSITIVITY_OPTIONS.map((option) => ({
              value: option.id,
              label: option.label,
            }))}
          />
        </div>

        {editable ? (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={
                !isDirty ||
                saving ||
                reloading ||
                staleConflict ||
                validationIssues.length > 0
              }
              onClick={() => void handleSave()}
            >
              {saving ? "Saving…" : "Save metadata"}
            </Button>
            {validationIssues.length > 0 ? (
              <p className="text-sm text-muted-foreground">{validationIssues[0]}</p>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Metadata is locked while this revision is not in draft review.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function MetadataSelectField({
  id,
  label,
  value,
  disabled,
  hint,
  requiredHumanAttestation = false,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  disabled?: boolean;
  hint?: string | null;
  requiredHumanAttestation?: boolean;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id}>{label}</Label>
        {requiredHumanAttestation ? (
          <Badge variant="muted">Required human attestation</Badge>
        ) : hint ? (
          <Badge variant="default">Suggested</Badge>
        ) : null}
      </div>
      {requiredHumanAttestation ? (
        <p className="text-xs text-muted-foreground">
          You must select this value. Intake does not prefill or suggest data origin or
          sensitivity.
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
      <select
        id={id}
        className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Select {label.toLowerCase()}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
