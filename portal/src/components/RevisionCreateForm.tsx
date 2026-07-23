import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { CreateRevisionInput, PackageRevision } from "@/types";
import {
  CERTIFICATION_CLASS_OPTIONS,
  DATA_ORIGIN_OPTIONS,
  IMPACT_LEVEL_OPTIONS,
  PROFILE_OPTIONS,
  SENSITIVITY_OPTIONS,
} from "@/utils/revisionDefaults";
import {
  buildCreateRevisionInput,
  normalizeMetadataFormForProfile,
  type RevisionMetadataFormValues,
  validateMetadataForm,
} from "@/utils/revisionMetadata";

type RevisionCreateFormProps = {
  revisions: PackageRevision[];
  busy?: boolean;
  onCreate: (input: CreateRevisionInput) => boolean | Promise<boolean>;
};

const emptyFormValues = (): RevisionMetadataFormValues => ({
  profile_id: "",
  certification_class: "",
  impact_level: "",
  data_origin: "",
  sensitivity: "",
});

export function RevisionCreateForm({
  revisions,
  busy = false,
  onCreate,
}: RevisionCreateFormProps) {
  const readyParents = revisions.filter((item) => item.status === "ready");
  const [expanded, setExpanded] = useState(false);
  const [parentId, setParentId] = useState<string>("");
  const [formValues, setFormValues] = useState<RevisionMetadataFormValues>(emptyFormValues);

  const validationIssues = useMemo(() => validateMetadataForm(formValues), [formValues]);
  const canCreate = validationIssues.length === 0 && !busy;

  const resetForm = () => {
    setParentId("");
    setFormValues(emptyFormValues());
  };

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
  };

  const handleCancel = () => {
    resetForm();
    setExpanded(false);
  };

  const handleCreate = async () => {
    const success = await onCreate(buildCreateRevisionInput(formValues, parentId || null));
    if (success) {
      resetForm();
      setExpanded(false);
    }
  };

  if (!expanded) {
    return (
      <div className="rounded-sm border bg-muted/20 p-4">
        <Button
          type="button"
          size="sm"
          disabled={busy}
          onClick={() => setExpanded(true)}
        >
          New revision
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-sm border bg-muted/20 p-4">
      <p className="text-sm text-muted-foreground">
        Set authorization profile and required human attestation, then create the revision
        and upload package files. Optionally link a ready parent revision to continue an
        existing lineage.
      </p>
      <div className="space-y-1.5">
        <Label htmlFor="parent-revision">Parent Revision (Optional)</Label>
        <select
          id="parent-revision"
          className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
          value={parentId}
          disabled={busy}
          onChange={(event) => setParentId(event.target.value)}
        >
          <option value="">None — new lineage</option>
          {readyParents.map((item) => (
            <option key={item.package_revision_id} value={item.package_revision_id}>
              {item.package_revision_id.slice(0, 8)}… ({item.profile_id})
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="profile-id">Profile</Label>
          <select
            id="profile-id"
            className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
            value={formValues.profile_id}
            disabled={busy}
            onChange={(event) =>
              updateField("profile_id", event.target.value as RevisionMetadataFormValues["profile_id"])
            }
          >
            <option value="">Select profile</option>
            {PROFILE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {formValues.profile_id === "fedramp_20x_program" ? (
          <div className="space-y-1.5">
            <Label htmlFor="certification-class">Certification class</Label>
            <select
              id="certification-class"
              className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
              value={formValues.certification_class}
              disabled={busy}
              onChange={(event) =>
                updateField(
                  "certification_class",
                  event.target.value as RevisionMetadataFormValues["certification_class"],
                )
              }
            >
              <option value="">Select certification class</option>
              {CERTIFICATION_CLASS_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="space-y-1.5">
            <Label htmlFor="impact-level">Impact level</Label>
            <select
              id="impact-level"
              className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
              value={formValues.impact_level}
              disabled={busy || !formValues.profile_id}
              onChange={(event) =>
                updateField(
                  "impact_level",
                  event.target.value as RevisionMetadataFormValues["impact_level"],
                )
              }
            >
              <option value="">Select impact level</option>
              {IMPACT_LEVEL_OPTIONS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="data-origin">Data origin</Label>
          <select
            id="data-origin"
            className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
            value={formValues.data_origin}
            disabled={busy}
            onChange={(event) =>
              updateField("data_origin", event.target.value as RevisionMetadataFormValues["data_origin"])
            }
          >
            <option value="">Select data origin</option>
            {DATA_ORIGIN_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="sensitivity">Sensitivity</Label>
          <select
            id="sensitivity"
            className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
            value={formValues.sensitivity}
            disabled={busy}
            onChange={(event) =>
              updateField("sensitivity", event.target.value as RevisionMetadataFormValues["sensitivity"])
            }
          >
            <option value="">Select sensitivity</option>
            {SENSITIVITY_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {validationIssues.length > 0 ? (
        <p className="text-sm text-muted-foreground">{validationIssues[0]}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          disabled={!canCreate}
          onClick={() => void handleCreate()}
        >
          Create revision
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={handleCancel}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
