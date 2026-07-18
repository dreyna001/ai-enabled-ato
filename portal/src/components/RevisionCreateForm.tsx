import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { CreateRevisionInput, PackageRevision } from "@/types";

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

  return (
    <div className="space-y-4 rounded-sm border bg-muted/20 p-4">
      <p className="text-sm text-muted-foreground">
        Create a revision first, then upload package files to populate profile and metadata.
        Optionally link a ready parent revision to continue an existing lineage.
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
      <Button
        type="button"
        size="sm"
        disabled={busy}
        onClick={() =>
          onCreate({
            parent_revision_id: parentId || null,
          })
        }
      >
        Create revision
      </Button>
    </div>
  );
}
