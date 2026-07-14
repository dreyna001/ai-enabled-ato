import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  FieldProvenanceMap,
  PackageDraftDocument,
  PackageRevisionDraft,
  SecurityControlEntry,
} from "@/types";
import {
  createEmptySecurityControl,
  formatProvenanceDetails,
  isModelAssistedProvenance,
  listSecurityControlIds,
  lookupProvenance,
  profileSectionLabel,
  provenanceLabel,
} from "@/utils/draftDocument";

type PackageEditorProps = {
  draft: PackageRevisionDraft;
  document: PackageDraftDocument;
  isDirty: boolean;
  saving: boolean;
  saveError: string;
  staleConflict: boolean;
  onDocumentChange: (document: PackageDraftDocument) => void;
  onSave: () => void;
  onReload: () => void;
  onConfirm: () => void;
};

function ProvenanceBadge({ pointer, provenance }: { pointer: string; provenance: FieldProvenanceMap }) {
  const entry = lookupProvenance(provenance, pointer);
  const label = provenanceLabel(entry);
  if (!label) {
    return null;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge variant={isModelAssistedProvenance(entry) ? "default" : "muted"}>
        {label}
      </Badge>
      {entry ? (
        <span className="text-xs text-muted-foreground" title={formatProvenanceDetails(entry)}>
          {formatProvenanceDetails(entry)}
        </span>
      ) : null}
    </span>
  );
}

function FieldRow({
  label,
  pointer,
  provenance,
  children,
}: {
  label: string;
  pointer: string;
  provenance: FieldProvenanceMap;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label>{label}</Label>
        <ProvenanceBadge pointer={pointer} provenance={provenance} />
      </div>
      {children}
    </div>
  );
}

export function PackageEditor({
  draft,
  document,
  isDirty,
  saving,
  saveError,
  staleConflict,
  onDocumentChange,
  onSave,
  onReload,
  onConfirm,
}: PackageEditorProps) {
  const [activeTab, setActiveTab] = useState("package");
  const provenance = draft.field_provenance;
  const profileLabel = profileSectionLabel(document.package.profile_id);

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (isDirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const controlIds = useMemo(() => listSecurityControlIds(document), [document]);

  const updateDocument = (next: PackageDraftDocument) => {
    onDocumentChange(next);
  };

  const updateControl = (controlId: string, patch: Partial<SecurityControlEntry>) => {
    const existing = document.security_controls[controlId] ?? createEmptySecurityControl();
    updateDocument({
      ...document,
      security_controls: {
        ...document.security_controls,
        [controlId]: { ...existing, ...patch },
      },
    });
  };

  const addControl = () => {
    const baseId = "AC-1";
    let candidate = baseId;
    let suffix = 1;
    while (document.security_controls[candidate]) {
      candidate = `${baseId}-${suffix}`;
      suffix += 1;
    }
    updateDocument({
      ...document,
      security_controls: {
        ...document.security_controls,
        [candidate]: createEmptySecurityControl(),
      },
    });
  };

  const removeControl = (controlId: string) => {
    const nextControls = { ...document.security_controls };
    delete nextControls[controlId];
    updateDocument({
      ...document,
      security_controls: nextControls,
    });
  };

  return (
    <div className="space-y-4">
      {isDirty ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm">
          Unsaved changes. Save draft before confirming the package.
        </div>
      ) : null}

      {staleConflict ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm">
          <span className="flex items-center gap-2">
            <AlertTriangle className="size-4 text-destructive" />
            This draft changed on the server. Reload to continue editing.
          </span>
          <Button type="button" size="sm" variant="outline" onClick={onReload}>
            Reload draft
          </Button>
        </div>
      ) : null}

      {saveError && !staleConflict ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {saveError}
        </div>
      ) : null}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="package">Package</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
          <TabsTrigger value="contacts">Contacts</TabsTrigger>
          <TabsTrigger value="controls">Controls</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
          {profileLabel ? <TabsTrigger value="profile">{profileLabel}</TabsTrigger> : null}
          <TabsTrigger value="privacy">Privacy</TabsTrigger>
          <TabsTrigger value="assessor">Assessor inputs</TabsTrigger>
        </TabsList>

        <TabsContent value="package" className="space-y-4">
          <FieldRow label="Title" pointer="/package/title" provenance={provenance}>
            <Input
              value={document.package.title}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  package: { ...document.package, title: event.target.value },
                })
              }
            />
          </FieldRow>
          <FieldRow
            label="Prepared for"
            pointer="/package/prepared_for"
            provenance={provenance}
          >
            <Input
              value={document.package.prepared_for}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  package: { ...document.package, prepared_for: event.target.value },
                })
              }
            />
          </FieldRow>
          <FieldRow label="Profile" pointer="/package/profile_id" provenance={provenance}>
            <Input value={document.package.profile_id} readOnly className="bg-muted/40" />
          </FieldRow>
        </TabsContent>

        <TabsContent value="system" className="space-y-4">
          <FieldRow
            label="Display name"
            pointer="/system/display_name"
            provenance={provenance}
          >
            <Input
              value={document.system.display_name}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  system: { ...document.system, display_name: event.target.value },
                })
              }
            />
          </FieldRow>
          <FieldRow
            label="Authorization boundary"
            pointer="/system/authorization_boundary"
            provenance={provenance}
          >
            <textarea
              className="min-h-24 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={document.system.authorization_boundary}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  system: {
                    ...document.system,
                    authorization_boundary: event.target.value,
                  },
                })
              }
            />
          </FieldRow>
          <FieldRow
            label="Mission summary"
            pointer="/system/mission_summary"
            provenance={provenance}
          >
            <textarea
              className="min-h-24 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={document.system.mission_summary}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  system: { ...document.system, mission_summary: event.target.value },
                })
              }
            />
          </FieldRow>
          <FieldRow label="Impact level" pointer="/system/impact_level" provenance={provenance}>
            <Input
              value={document.system.impact_level ?? ""}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  system: {
                    ...document.system,
                    impact_level: event.target.value || null,
                  },
                })
              }
            />
          </FieldRow>
        </TabsContent>

        <TabsContent value="contacts" className="space-y-4">
          <Card className="bg-muted/20">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">System owner</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <ContactEditor
                contacts={document.contacts.system_owner}
                onChange={(system_owner) =>
                  updateDocument({
                    ...document,
                    contacts: { ...document.contacts, system_owner },
                  })
                }
              />
            </CardContent>
          </Card>
          <Card className="bg-muted/20">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">ISSO</CardTitle>
            </CardHeader>
            <CardContent>
              <ContactEditor
                contacts={document.contacts.isso}
                onChange={(isso) =>
                  updateDocument({
                    ...document,
                    contacts: { ...document.contacts, isso },
                  })
                }
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="controls" className="space-y-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              {controlIds.length} security control{controlIds.length === 1 ? "" : "s"}
            </p>
            <Button type="button" size="sm" variant="outline" onClick={addControl}>
              <Plus className="size-4" />
              Add control
            </Button>
          </div>
          {controlIds.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No controls pre-filled yet. Add rows manually or upload OSCAL/scanner exports.
            </p>
          ) : null}
          {controlIds.map((controlId) => {
            const control = document.security_controls[controlId];
            const pointerBase = `/security_controls/${controlId.replace(/\//g, "~1")}`;
            return (
              <Card key={controlId} className="bg-muted/20">
                <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 pb-2">
                  <CardTitle className="font-mono text-sm">{controlId}</CardTitle>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => removeControl(controlId)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </CardHeader>
                <CardContent className="space-y-3">
                  <FieldRow
                    label="Implementation status"
                    pointer={`${pointerBase}/implementation_status`}
                    provenance={provenance}
                  >
                    <Input
                      value={control.implementation_status}
                      onChange={(event) =>
                        updateControl(controlId, {
                          implementation_status: event.target.value,
                        })
                      }
                    />
                  </FieldRow>
                  <FieldRow
                    label="Implementation statement"
                    pointer={`${pointerBase}/implementation_statement`}
                    provenance={provenance}
                  >
                    <textarea
                      className="min-h-20 w-full rounded-md border bg-background px-3 py-2 text-sm"
                      value={control.implementation_statement}
                      onChange={(event) =>
                        updateControl(controlId, {
                          implementation_statement: event.target.value,
                        })
                      }
                    />
                  </FieldRow>
                </CardContent>
              </Card>
            );
          })}
        </TabsContent>

        <TabsContent value="evidence" className="space-y-4">
          {Object.keys(document.evidence).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No evidence entries linked yet. Upload artifacts to populate evidence links.
            </p>
          ) : (
            Object.entries(document.evidence).map(([key, value]) => (
              <Card key={key} className="bg-muted/20">
                <CardHeader className="pb-2">
                  <CardTitle className="font-mono text-sm">{key}</CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="overflow-auto rounded-md border bg-background p-3 text-xs">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            ))
          )}
        </TabsContent>

        {profileLabel ? (
          <TabsContent value="profile" className="space-y-4">
            <ProfileSection document={document} onDocumentChange={updateDocument} />
          </TabsContent>
        ) : null}

        <TabsContent value="privacy" className="space-y-4">
          <FieldRow
            label="Privacy scope notice"
            pointer="/privacy/scope_notice"
            provenance={provenance}
          >
            <textarea
              className="min-h-24 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={document.privacy.scope_notice}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  privacy: { ...document.privacy, scope_notice: event.target.value },
                })
              }
            />
          </FieldRow>
          <p className="text-sm text-muted-foreground">
            Artifacts present (assessor/privacy ownership — read-only):{" "}
            {document.privacy.artifacts_present ? "yes" : "no"}
          </p>
        </TabsContent>

        <TabsContent value="assessor" className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Assessor-owned inputs are read-only in the portal. Contact an assessor to update SAR
            or independent assessment fields.
          </p>
          {Object.keys(document.assessor_inputs).length === 0 ? (
            <p className="text-sm text-muted-foreground">No assessor inputs populated yet.</p>
          ) : (
            Object.entries(document.assessor_inputs).map(([key, value]) => (
              <Card key={key} className="bg-muted/20">
                <CardHeader className="pb-2">
                  <CardTitle className="font-mono text-sm">{key}</CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="overflow-auto rounded-md border bg-background p-3 text-xs">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            ))
          )}
        </TabsContent>
      </Tabs>

      <Separator />

      <div className="flex flex-wrap gap-2">
        <Button type="button" disabled={!isDirty || saving || staleConflict} onClick={onSave}>
          {saving ? "Saving…" : "Save draft"}
        </Button>
        <Button
          type="button"
          variant="default"
          disabled={isDirty || saving || staleConflict}
          onClick={onConfirm}
        >
          Confirm package
        </Button>
      </div>

      <Card className="bg-muted/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Draft metadata</CardTitle>
          <CardDescription>
            Updated by {draft.updated_by} at {draft.updated_at}
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

function ContactEditor({
  contacts,
  onChange,
}: {
  contacts: Array<{ name: string; role: string; email: string }>;
  onChange: (contacts: Array<{ name: string; role: string; email: string }>) => void;
}) {
  const primary = contacts[0] ?? { name: "", role: "", email: "" };
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <Input
        placeholder="Name"
        value={primary.name}
        onChange={(event) =>
          onChange([{ ...primary, name: event.target.value }, ...contacts.slice(1)])
        }
      />
      <Input
        placeholder="Role"
        value={primary.role}
        onChange={(event) =>
          onChange([{ ...primary, role: event.target.value }, ...contacts.slice(1)])
        }
      />
      <Input
        placeholder="Email"
        value={primary.email}
        onChange={(event) =>
          onChange([{ ...primary, email: event.target.value }, ...contacts.slice(1)])
        }
      />
    </div>
  );
}

function ProfileSection({
  document,
  onDocumentChange,
}: {
  document: PackageDraftDocument;
  onDocumentChange: (document: PackageDraftDocument) => void;
}) {
  const profileId = document.package.profile_id;
  const sectionKey =
    profileId === "fedramp_20x_program"
      ? "fedramp_20x"
      : profileId === "fedramp_rev5_transition"
        ? "fedramp_rev5_transition"
        : "fisma_agency_security";
  const section = document[sectionKey as keyof PackageDraftDocument] as Record<
    string,
    unknown
  > | null;

  if (!section || Object.keys(section).length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No profile-specific section populated yet for {profileId}.
      </p>
    );
  }

  return (
    <GroupedRecordEditor
      label={profileSectionLabel(profileId) ?? profileId}
      value={section}
      onChange={(next) =>
        onDocumentChange({
          ...document,
          [sectionKey]: next,
        } as PackageDraftDocument)
      }
    />
  );
}

function GroupedRecordEditor({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  return (
    <Card className="bg-muted/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{label}</CardTitle>
        <CardDescription>Edit grouped profile fields (JSON values).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {Object.entries(value).map(([key, fieldValue]) => (
          <div key={key} className="space-y-1">
            <Label>{key}</Label>
            {typeof fieldValue === "string" ? (
              <Input
                value={fieldValue}
                onChange={(event) =>
                  onChange({
                    ...value,
                    [key]: event.target.value,
                  })
                }
              />
            ) : (
              <textarea
                className="min-h-20 w-full rounded-md border bg-background px-3 py-2 font-mono text-xs"
                value={JSON.stringify(fieldValue, null, 2)}
                onChange={(event) => {
                  try {
                    onChange({
                      ...value,
                      [key]: JSON.parse(event.target.value),
                    });
                  } catch {
                    // keep editing until valid JSON
                  }
                }}
              />
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
