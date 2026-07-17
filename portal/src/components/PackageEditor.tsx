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
  formatProvenanceHint,
  isModelAssistedProvenance,
  listSecurityControlIds,
  lookupProvenance,
  profileSectionLabel,
  provenanceLabel,
} from "@/utils/draftDocument";
import {
  authorizationPathLabel,
  humanizeDraftPointer,
  impactLevelEditableForProfile,
  lookupDraftIssue,
  type DraftFieldIssue,
} from "@/utils/draftValidation";

type PackageEditorProps = {
  draft: PackageRevisionDraft;
  document: PackageDraftDocument;
  isDirty: boolean;
  saving: boolean;
  saveError: string;
  staleConflict: boolean;
  validationIssues: DraftFieldIssue[];
  exportBlocked?: boolean;
  exportBlockers?: string[];
  onDocumentChange: (document: PackageDraftDocument) => void;
  onSave: () => void;
  onReload: () => void;
  onConfirm: () => void;
};

const IMPLEMENTATION_STATUS_OPTIONS = [
  "implemented",
  "partial",
  "planned",
  "not_applicable",
  "not_implemented",
] as const;

function ProvenanceBadge({ pointer, provenance }: { pointer: string; provenance: FieldProvenanceMap }) {
  const entry = lookupProvenance(provenance, pointer);
  const label = provenanceLabel(entry);
  if (!label) {
    return null;
  }
  return (
    <span className="inline-flex items-center gap-2">
      <Badge variant={isModelAssistedProvenance(entry) ? "default" : "muted"}>
        {label}
      </Badge>
      {entry ? (
        <span className="text-xs text-muted-foreground" title={formatProvenanceDetails(entry)}>
          {formatProvenanceHint(entry)}
        </span>
      ) : null}
    </span>
  );
}

function FieldHelp({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

function FieldError({ message }: { message?: string }) {
  if (!message) {
    return null;
  }
  return <p className="text-xs text-destructive">{message}</p>;
}

function FieldRow({
  label,
  pointer,
  provenance,
  helpText,
  fieldError,
  children,
}: {
  label: string;
  pointer: string;
  provenance: FieldProvenanceMap;
  helpText?: string;
  fieldError?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label className={fieldError ? "text-destructive" : undefined}>{label}</Label>
        <ProvenanceBadge pointer={pointer} provenance={provenance} />
      </div>
      {helpText ? <FieldHelp>{helpText}</FieldHelp> : null}
      {children}
      <FieldError message={fieldError} />
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
  validationIssues,
  exportBlocked = false,
  exportBlockers = [],
  onDocumentChange,
  onSave,
  onReload,
  onConfirm,
}: PackageEditorProps) {
  const [activeTab, setActiveTab] = useState("package");
  const provenance = draft.field_provenance;
  const profileLabel = profileSectionLabel(document.package.profile_id);
  const hasValidationIssues = validationIssues.length > 0;

  const issueFor = (pointer: string) => lookupDraftIssue(validationIssues, pointer)?.message;

  const focusTabForIssue = (issue: DraftFieldIssue) => {
    setActiveTab(issue.tab);
  };

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
        <div className="rounded-sm border border-border border-l-4 border-l-amber-500 bg-card px-4 py-3 text-sm text-foreground">
          Unsaved changes. Save draft before confirming the package.
        </div>
      ) : null}

      {staleConflict ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border border-l-4 border-l-destructive bg-card px-4 py-3 text-sm text-foreground">
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
        <div className="rounded-sm border border-border border-l-4 border-l-destructive bg-card px-4 py-3 text-sm text-destructive whitespace-pre-line">
          {saveError}
        </div>
      ) : null}

      {hasValidationIssues ? (
        <div className="rounded-sm border border-border border-l-4 border-l-destructive bg-card px-4 py-3 text-sm">
          <p className="font-medium text-destructive">
            Fix these fields before saving or confirming:
          </p>
          <ul className="mt-2 space-y-1 text-destructive">
            {validationIssues.map((issue) => (
              <li key={issue.pointer}>
                <button
                  type="button"
                  className="text-left underline-offset-2 hover:underline"
                  onClick={() => focusTabForIssue(issue)}
                >
                  {humanizeDraftPointer(issue.pointer)}: {issue.message}
                </button>
              </li>
            ))}
          </ul>
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
          <TabsTrigger value="assessor">Assessor Inputs</TabsTrigger>
        </TabsList>

        <TabsContent value="package" className="space-y-4">
          <FieldRow
            label="Title"
            pointer="/package/title"
            provenance={provenance}
            helpText="Short package name shown in workflow and exports (max 255 characters)."
            fieldError={issueFor("/package/title")}
          >
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
            label="Prepared For"
            pointer="/package/prepared_for"
            provenance={provenance}
            helpText="Customer or program audience for this package (for example, agency security review)."
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
          <FieldRow
            label="Profile"
            pointer="/package/profile_id"
            provenance={provenance}
            helpText="Set when the revision was created. It cannot be changed in the editor."
          >
            <Input value={document.package.profile_id} readOnly className="bg-muted/40" />
          </FieldRow>
        </TabsContent>

        <TabsContent value="system" className="space-y-4">
          <FieldRow
            label="Display Name"
            pointer="/system/display_name"
            provenance={provenance}
            helpText="Official system name used in sealed package and system-context records."
            fieldError={issueFor("/system/display_name")}
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
            label="Authorization Boundary"
            pointer="/system/authorization_boundary"
            provenance={provenance}
            helpText="Describe what is in scope for authorization (networks, services, data stores)."
            fieldError={issueFor("/system/authorization_boundary")}
          >
            <textarea
              className="min-h-24 w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
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
            label="Mission Summary"
            pointer="/system/mission_summary"
            provenance={provenance}
            helpText="One or two sentences describing what the system does."
            fieldError={issueFor("/system/mission_summary")}
          >
            <textarea
              className="min-h-24 w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
              value={document.system.mission_summary}
              onChange={(event) =>
                updateDocument({
                  ...document,
                  system: { ...document.system, mission_summary: event.target.value },
                })
              }
            />
          </FieldRow>
          {impactLevelEditableForProfile(document.package.profile_id) ? (
            <FieldRow
              label="Impact Level"
              pointer="/system/impact_level"
              provenance={provenance}
              helpText="Required before confirm. Choose the FIPS 199 impact level (low, moderate, or high)."
              fieldError={issueFor("/system/impact_level")}
            >
              <select
                className="w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
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
              >
                <option value="">Select impact level</option>
                <option value="low">Low</option>
                <option value="moderate">Moderate</option>
                <option value="high">High</option>
              </select>
            </FieldRow>
          ) : null}
          <FieldRow
            label="Authorization Path"
            pointer="/system/authorization_path"
            provenance={provenance}
            helpText="Set automatically from the package profile. It cannot be changed in the editor."
            fieldError={issueFor("/system/authorization_path")}
          >
            <p className="text-sm text-muted-foreground">
              {authorizationPathLabel(document.package.profile_id)}
            </p>
          </FieldRow>
        </TabsContent>

        <TabsContent value="contacts" className="space-y-4">
          <Card className="bg-muted/20">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">System Owner</CardTitle>
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
              Add Control
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
                    label="Implementation Status"
                    pointer={`${pointerBase}/implementation_status`}
                    provenance={provenance}
                    helpText="Use implemented, partial, planned, not applicable, or not implemented."
                    fieldError={issueFor(`${pointerBase}/implementation_status`)}
                  >
                    <select
                      className="w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
                      value={control.implementation_status}
                      onChange={(event) =>
                        updateControl(controlId, {
                          implementation_status: event.target.value,
                        })
                      }
                    >
                      {IMPLEMENTATION_STATUS_OPTIONS.map((status) => (
                        <option key={status} value={status}>
                          {status.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  </FieldRow>
                  <FieldRow
                    label="Implementation Statement"
                    pointer={`${pointerBase}/implementation_statement`}
                    provenance={provenance}
                    helpText="Describe how this control is implemented in this system."
                    fieldError={issueFor(`${pointerBase}/implementation_statement`)}
                  >
                    <textarea
                      className="min-h-20 w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
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
                  <pre className="overflow-auto rounded-sm border border-border bg-background p-3 text-xs">
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
            label="Privacy Scope Notice"
            pointer="/privacy/scope_notice"
            provenance={provenance}
          >
            <textarea
              className="min-h-24 w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
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
            Assessor-owned inputs are populated by upload and intake only. Upload an assessor
            attestation or independent assessment artifact before confirming; fields here are
            read-only.
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
                  <pre className="overflow-auto rounded-sm border border-border bg-background p-3 text-xs">
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
        <Button
          type="button"
          disabled={!isDirty || saving || staleConflict || hasValidationIssues}
          onClick={onSave}
        >
          {saving ? "Saving…" : "Save Draft"}
        </Button>
        <Button
          type="button"
          variant="default"
          disabled={
            isDirty || saving || staleConflict || hasValidationIssues || exportBlocked
          }
          onClick={onConfirm}
          title={
            exportBlocked
              ? "Resolve export readiness blockers before confirming."
              : hasValidationIssues
                ? "Resolve the highlighted validation issues before confirming."
                : isDirty
                  ? "Save draft before confirming."
                  : undefined
          }
        >
          Confirm Package
        </Button>
        {exportBlocked ? (
          <p className="w-full text-sm text-destructive">
            Confirm is blocked until required uploads and profile content are present
            {exportBlockers.length > 0 ? ` (${exportBlockers.length} item${exportBlockers.length === 1 ? "" : "s"})` : ""}.
            See Confirm readiness above.
          </p>
        ) : null}
      </div>
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
                className="min-h-20 w-full rounded-sm border border-border bg-background px-3 py-2 font-mono text-xs"
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
