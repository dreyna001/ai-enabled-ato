import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { CreateRevisionInput, PackageRevision } from "@/types";
import {
  DATA_ORIGIN_OPTIONS,
  defaultRevisionInput,
  PROFILE_OPTIONS,
  SENSITIVITY_OPTIONS,
} from "@/utils/revisionDefaults";

type RevisionCreateFormProps = {
  revisions: PackageRevision[];
  busy?: boolean;
  onCreate: (input: CreateRevisionInput) => void;
};

export function RevisionCreateForm({
  revisions,
  busy = false,
  onCreate,
}: RevisionCreateFormProps) {
  const readyParents = revisions.filter((item) => item.status === "ready");
  const [parentId, setParentId] = useState<string>("");
  const parent = readyParents.find((item) => item.package_revision_id === parentId) ?? null;
  const [input, setInput] = useState<CreateRevisionInput>(() =>
    defaultRevisionInput(null),
  );

  const applyParent = (nextParentId: string) => {
    setParentId(nextParentId);
    const selected = readyParents.find((item) => item.package_revision_id === nextParentId);
    setInput(defaultRevisionInput(selected ?? null));
  };

  return (
    <div className="space-y-4 rounded-md border bg-muted/20 p-4">
      <p className="text-sm text-muted-foreground">
        Choose profile and data origin before creating a revision. Child revisions inherit
        the parent profile when selected.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="parent-revision">Parent revision (optional)</Label>
          <select
            id="parent-revision"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={parentId}
            disabled={busy}
            onChange={(event) => applyParent(event.target.value)}
          >
            <option value="">None — new lineage</option>
            {readyParents.map((item) => (
              <option key={item.package_revision_id} value={item.package_revision_id}>
                {item.package_revision_id.slice(0, 8)}… ({item.profile_id})
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="profile-id">Profile</Label>
          <select
            id="profile-id"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={input.profile_id}
            disabled={busy || Boolean(parent)}
            onChange={(event) =>
              setInput((current) => ({
                ...current,
                profile_id: event.target.value as CreateRevisionInput["profile_id"],
              }))
            }
          >
            {PROFILE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="data-origin">Data origin</Label>
          <select
            id="data-origin"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={input.data_origin}
            disabled={busy}
            onChange={(event) =>
              setInput((current) => ({
                ...current,
                data_origin: event.target.value as CreateRevisionInput["data_origin"],
              }))
            }
          >
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
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={input.sensitivity}
            disabled={busy}
            onChange={(event) =>
              setInput((current) => ({
                ...current,
                sensitivity: event.target.value as CreateRevisionInput["sensitivity"],
              }))
            }
          >
            {SENSITIVITY_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        disabled={busy}
        onClick={() =>
          onCreate({
            ...input,
            parent_revision_id: parentId || null,
          })
        }
      >
        Create revision with selected options
      </Button>
    </div>
  );
}
