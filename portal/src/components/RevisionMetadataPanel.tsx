import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AlertTriangle } from "lucide-react";

import {
  ApiError,
  getRevision,
  patchRevisionMetadata,
  revisionEtag,
} from "@/api/client";

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

  refreshKey?: string;
};

function optionLabel(
  options: Array<{ value: string; label: string }>,

  value: string,
): string {
  if (!value) {
    return "Not set";
  }

  return options.find((option) => option.value === value)?.label ?? value;
}

export function RevisionMetadataPanel({
  session,

  revision,

  onRevisionUpdated,

  onMetadataStateChange,

  refreshKey = "",
}: RevisionMetadataPanelProps) {
  const editable = isRevisionMetadataEditable(revision);

  const [savedValues, setSavedValues] = useState<RevisionMetadataFormValues>(
    () => metadataValuesFromRevision(revision),
  );

  const [formValues, setFormValues] = useState<RevisionMetadataFormValues>(() =>
    metadataValuesFromRevision(revision),
  );

  const [etag, setEtag] = useState(() =>
    revisionEtag(revision.revision_version),
  );

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

  const validationIssues = useMemo(
    () => validateMetadataForm(formValues),
    [formValues],
  );

  const isDirty = useMemo(
    () => !metadataFormEquals(savedValues, formValues),
    [formValues, savedValues],
  );
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
  }, [
    isComplete,
    isDirty,
    onMetadataStateChange,
    reloading,
    saving,
    staleConflict,
  ]);

  const syncFromRevision = useCallback(
    (
      nextRevision: PackageRevision,

      nextEtag = revisionEtag(nextRevision.revision_version),
    ) => {
      const revisionValues = metadataValuesFromRevision(nextRevision);

      syncedRevisionRef.current = {
        revisionId: nextRevision.package_revision_id,

        revisionVersion: nextRevision.revision_version,
      };

      setSavedValues(revisionValues);

      setFormValues(revisionValues);

      setEtag(nextEtag);
    },

    [],
  );

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

    syncFromRevision(revision);
  }, [isDirty, refreshKey, revision, syncFromRevision]);

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
      const latestRevision = await getRevision(revision.package_revision_id);

      syncFromRevision(latestRevision);

      setSaveError("");

      setReloadError("");

      setStaleConflict(false);

      setSavedNotice(false);

      onRevisionUpdated(
        latestRevision,

        revisionEtag(latestRevision.revision_version),
      );
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

      syncFromRevision(result.revision, result.etag);

      setSavedNotice(true);

      onRevisionUpdated(result.revision, result.etag);
    } catch (err) {
      if (
        err instanceof ApiError &&
        (err.status === 412 || err.status === 428)
      ) {
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

  const profileOptions = PROFILE_OPTIONS.map((option) => ({
    value: option.id,

    label: option.label,
  }));

  const certificationClassOptions = CERTIFICATION_CLASS_OPTIONS.map(
    (option) => ({
      value: option.id,

      label: option.label,
    }),
  );

  const impactLevelOptions = IMPACT_LEVEL_OPTIONS.map((option) => ({
    value: option.id,

    label: option.label,
  }));

  return (
    <Card id="revision-metadata-panel" className="bg-muted/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Revision metadata</CardTitle>

        <CardDescription>
          {editable
            ? "Authorization path and human attestation were set at creation. Update values here if corrections are needed before confirming the package."
            : "Sealed revision metadata is immutable. Values below were saved when intake was confirmed."}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {staleConflict ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
            <span className="flex items-center gap-2">
              <AlertTriangle
                className="size-4 text-destructive"
                aria-hidden="true"
              />

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
          <p className="whitespace-pre-line text-sm text-destructive">
            {saveError}
          </p>
        ) : null}

        {savedNotice && !isDirty && !saveError ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Metadata saved.
          </p>
        ) : null}

        {editable && isDirty ? (
          <p className="text-sm text-foreground">Unsaved metadata changes.</p>
        ) : null}

        {editable ? (
          <div className="grid gap-4 md:grid-cols-2">
            <MetadataSelectField
              id="revision-profile-id"

              label="Profile"

              value={formValues.profile_id}

              disabled={saving || reloading}

              onChange={(value) =>
                updateField(
                  "profile_id",
                  value as RevisionMetadataFormValues["profile_id"],
                )
              }

              options={profileOptions}
            />

            {formValues.profile_id === "fedramp_20x_program" ? (
              <MetadataSelectField
                id="revision-certification-class"

                label="Certification class"

                value={formValues.certification_class}

                disabled={saving || reloading}

                onChange={(value) =>
                  updateField(
                    "certification_class",

                    value as RevisionMetadataFormValues["certification_class"],
                  )
                }

                options={certificationClassOptions}
              />
            ) : (
              <MetadataSelectField
                id="revision-impact-level"

                label="Impact level"

                value={formValues.impact_level}

                disabled={saving || reloading || !formValues.profile_id}

                onChange={(value) =>
                  updateField(
                    "impact_level",
                    value as RevisionMetadataFormValues["impact_level"],
                  )
                }

                options={impactLevelOptions}
              />
            )}

            <MetadataSelectField
              id="revision-data-origin"

              label="Data origin"

              value={formValues.data_origin}

              disabled={saving || reloading}

              onChange={(value) =>
                updateField(
                  "data_origin",
                  value as RevisionMetadataFormValues["data_origin"],
                )
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

              disabled={saving || reloading}

              onChange={(value) =>
                updateField(
                  "sensitivity",
                  value as RevisionMetadataFormValues["sensitivity"],
                )
              }

              options={SENSITIVITY_OPTIONS.map((option) => ({
                value: option.id,

                label: option.label,
              }))}
            />
          </div>
        ) : (
          <MetadataReadOnlySummary
            values={savedValues}

            profileOptions={profileOptions}

            certificationClassOptions={certificationClassOptions}

            impactLevelOptions={impactLevelOptions}

            dataOriginOptions={DATA_ORIGIN_OPTIONS.map((option) => ({
              value: option.id,

              label: option.label,
            }))}

            sensitivityOptions={SENSITIVITY_OPTIONS.map((option) => ({
              value: option.id,

              label: option.label,
            }))}
          />
        )}

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
              <p className="text-sm text-muted-foreground">
                {validationIssues[0]}
              </p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function MetadataReadOnlySummary({
  values,

  profileOptions,

  certificationClassOptions,

  impactLevelOptions,

  dataOriginOptions,

  sensitivityOptions,
}: {
  values: RevisionMetadataFormValues;

  profileOptions: Array<{ value: string; label: string }>;

  certificationClassOptions: Array<{ value: string; label: string }>;

  impactLevelOptions: Array<{ value: string; label: string }>;

  dataOriginOptions: Array<{ value: string; label: string }>;

  sensitivityOptions: Array<{ value: string; label: string }>;
}) {
  return (
    <dl className="grid gap-3 rounded-sm border border-border bg-card px-3 py-3 text-sm md:grid-cols-2">
      <div>
        <dt className="font-medium text-foreground">Profile</dt>

        <dd className="mt-1 text-muted-foreground">
          {optionLabel(profileOptions, values.profile_id)}
        </dd>
      </div>

      {values.profile_id === "fedramp_20x_program" ? (
        <div>
          <dt className="font-medium text-foreground">Certification class</dt>

          <dd className="mt-1 text-muted-foreground">
            {optionLabel(certificationClassOptions, values.certification_class)}
          </dd>
        </div>
      ) : (
        <div>
          <dt className="font-medium text-foreground">Impact level</dt>

          <dd className="mt-1 text-muted-foreground">
            {optionLabel(impactLevelOptions, values.impact_level)}
          </dd>
        </div>
      )}

      <div>
        <dt className="font-medium text-foreground">Data origin</dt>

        <dd className="mt-1 text-muted-foreground">
          {optionLabel(dataOriginOptions, values.data_origin)}
        </dd>
      </div>

      <div>
        <dt className="font-medium text-foreground">Sensitivity</dt>

        <dd className="mt-1 text-muted-foreground">
          {optionLabel(sensitivityOptions, values.sensitivity)}
        </dd>
      </div>
    </dl>
  );
}

function MetadataSelectField({
  id,

  label,

  value,

  disabled,

  options,

  onChange,
}: {
  id: string;

  label: string;

  value: string;

  disabled?: boolean;

  options: Array<{ value: string; label: string }>;

  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>

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
