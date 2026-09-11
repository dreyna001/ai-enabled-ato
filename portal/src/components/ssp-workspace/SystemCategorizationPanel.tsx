import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type {
  CategorizationAgentSuggestion,
  CategorizationChange,
  EvidenceArtifact,
  EvidenceLink,
  ImpactLevel,
  SystemCategorization,
} from "@/sspWorkspaceTypes";
import {
  hydrateCategorizationValues,
  validateCategorizationChange,
  type CategorizationValidationIssue,
} from "@/utils/sspFieldErrors";

const IMPACTS: Array<{
  key: "confidentiality" | "integrity" | "availability";
  rationaleKey:
    | "confidentialityRationale"
    | "integrityRationale"
    | "availabilityRationale";
  evidenceKey:
    | "confidentialityEvidence"
    | "integrityEvidence"
    | "availabilityEvidence";
  label: string;
  question: string;
}> = [
  {
    key: "confidentiality",
    rationaleKey: "confidentialityRationale",
    evidenceKey: "confidentialityEvidence",
    label: "Confidentiality",
    question: "What harm could result if information is disclosed?",
  },
  {
    key: "integrity",
    rationaleKey: "integrityRationale",
    evidenceKey: "integrityEvidence",
    label: "Integrity",
    question: "What harm could result if information is changed incorrectly?",
  },
  {
    key: "availability",
    rationaleKey: "availabilityRationale",
    evidenceKey: "availabilityEvidence",
    label: "Availability",
    question: "What harm could result if the system is unavailable?",
  },
];

function overallImpact(values: CategorizationChange): ImpactLevel | "" {
  const impacts = [
    values.confidentiality,
    values.integrity,
    values.availability,
  ];
  if (impacts.some((value) => !value)) return "";
  const rank: Record<ImpactLevel, number> = { low: 0, moderate: 1, high: 2 };
  return impacts.reduce((highest, value) =>
    rank[value as ImpactLevel] > rank[highest as ImpactLevel] ? value : highest,
  ) as ImpactLevel;
}

function statusBadgeVariant(
  status: SystemCategorization["status"],
): "success" | "warning" | "destructive" {
  if (status === "confirmed") return "success";
  if (status === "stale") return "destructive";
  return "warning";
}

function statusLabel(status: SystemCategorization["status"]): string {
  if (status === "confirmed") return "Confirmed";
  if (status === "stale") return "Re-confirm required";
  return "Unconfirmed";
}

function evidenceArtifactOptions(evidence: EvidenceArtifact[]) {
  return evidence.filter((artifact) => artifact.state === "processed");
}

function toggleEvidenceLink(
  current: EvidenceLink[],
  artifactId: string,
): EvidenceLink[] {
  const existing = current.find((link) => link.artifactId === artifactId);
  if (existing) {
    return current.filter((link) => link.artifactId !== artifactId);
  }
  return [
    ...current,
    {
      id: `${artifactId}:categorization`,
      artifactId,
      locator: JSON.stringify({ kind: "categorization_attestation" }),
    },
  ];
}

function agentSuggestedForAxis(
  agentSuggestion: CategorizationAgentSuggestion | null,
  key: "confidentiality" | "integrity" | "availability",
) {
  return Boolean(agentSuggestion?.[key]);
}

export function SystemCategorizationPanel({
  categorization,
  provisionalImpactLevel,
  evidence,
  revisionId,
  analyzePending = false,
  onAnalyze,
  onSave,
}: {
  categorization: SystemCategorization;
  provisionalImpactLevel: ImpactLevel;
  evidence: EvidenceArtifact[];
  revisionId: string;
  analyzePending?: boolean;
  onAnalyze?: () => void;
  onSave?: (change: CategorizationChange) => void;
}) {
  const processedEvidence = evidenceArtifactOptions(evidence);
  const [values, setValues] = useState<CategorizationChange>(() =>
    hydrateCategorizationValues(categorization, processedEvidence),
  );
  const [validationIssues, setValidationIssues] = useState<
    CategorizationValidationIssue[]
  >([]);

  useEffect(() => {
    const processed = evidenceArtifactOptions(evidence);
    setValues(hydrateCategorizationValues(categorization, processed));
    setValidationIssues([]);
  }, [revisionId]);

  const overall = overallImpact(values);
  const complete =
    Boolean(overall) &&
    IMPACTS.every(
      ({ rationaleKey, evidenceKey }) =>
        values[rationaleKey].trim() && values[evidenceKey].length > 0,
    );
  const showAnalyze =
    categorization.status !== "confirmed" &&
    Boolean(onAnalyze) &&
    processedEvidence.length > 0;

  function issueForField(field: string) {
    return validationIssues.find((issue) => issue.field === field)?.message;
  }

  function handleConfirm() {
    const issues = validateCategorizationChange(values, processedEvidence);
    if (issues.length > 0) {
      setValidationIssues(issues);
      return;
    }
    setValidationIssues([]);
    onSave?.(values);
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">System categorization</CardTitle>
            <CardDescription>
              FIPS 199 impacts require rationale and at least one supporting
              evidence artifact per security objective. Agent suggestions stay
              editable until you confirm.
            </CardDescription>
          </div>
          <Badge variant={statusBadgeVariant(categorization.status)}>
            {statusLabel(categorization.status)}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {categorization.status !== "confirmed" ? (
          <p className="rounded-sm border bg-muted/30 p-3 text-xs text-muted-foreground">
            {categorization.status === "stale" ? (
              <>
                Mission, boundary, or information types changed after the last
                confirmation. Re-confirm categorization before ISSO approval.
              </>
            ) : categorization.agentSuggestion ? (
              <>
                Agent suggested values are shown below. Review and edit each
                impact, rationale, and evidence link, then confirm to lock the
                baseline.
              </>
            ) : (
              <>
                A provisional {provisionalImpactLevel} baseline keeps the
                workspace usable. Analyze from evidence or run full document
                generation, then confirm with supporting artifacts.
              </>
            )}
          </p>
        ) : null}
        {validationIssues.length > 0 ? (
          <div
            className="rounded-sm border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
            role="alert"
          >
            <p className="font-medium">Fix these items before confirming:</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {validationIssues.map((issue) => (
                <li key={`${issue.field}:${issue.message}`}>{issue.message}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="grid gap-4 xl:grid-cols-3">
          {IMPACTS.map(({ key, rationaleKey, evidenceKey, label, question }) => (
            <fieldset key={key} className="space-y-2 rounded-sm border p-3">
              <legend className="flex flex-wrap items-center gap-2 px-1 text-sm font-medium">
                <span>{label}</span>
                {agentSuggestedForAxis(categorization.agentSuggestion, key) ? (
                  <Badge variant="outline" className="text-[10px] font-normal">
                    Agent suggested
                  </Badge>
                ) : null}
              </legend>
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">Impact</span>
                <select
                  aria-label={`${label} impact`}
                  className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                  value={values[key]}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      [key]: event.target.value as ImpactLevel | "",
                    }))
                  }
                >
                  <option value="">Select</option>
                  <option value="low">Low</option>
                  <option value="moderate">Moderate</option>
                  <option value="high">High</option>
                </select>
                {issueForField(key) ? (
                  <p className="mt-1 text-destructive">{issueForField(key)}</p>
                ) : null}
              </label>
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">
                  {question}
                </span>
                <textarea
                  aria-label={`${label} rationale`}
                  className="min-h-24 w-full resize-y rounded-sm border bg-background px-3 py-2 text-sm"
                  value={values[rationaleKey]}
                  onChange={(event) =>
                    setValues((current) => ({
                      ...current,
                      [rationaleKey]: event.target.value,
                    }))
                  }
                />
                {issueForField(rationaleKey) ? (
                  <p className="mt-1 text-destructive">
                    {issueForField(rationaleKey)}
                  </p>
                ) : null}
              </label>
              <div className="space-y-2 text-xs">
                <p className="text-muted-foreground">
                  Supporting evidence (select at least one processed artifact)
                </p>
                {issueForField(evidenceKey) ? (
                  <p className="text-destructive">{issueForField(evidenceKey)}</p>
                ) : null}
                {processedEvidence.length === 0 ? (
                  <p className="rounded-sm border border-dashed p-2 text-muted-foreground">
                    Upload and process evidence in Intake before confirming
                    categorization.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {processedEvidence.map((artifact) => {
                      const selected = values[evidenceKey].some(
                        (link) => link.artifactId === artifact.id,
                      );
                      return (
                        <li key={artifact.id}>
                          <label className="flex items-start gap-2 rounded-sm border p-2">
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() =>
                                setValues((current) => ({
                                  ...current,
                                  [evidenceKey]: toggleEvidenceLink(
                                    current[evidenceKey],
                                    artifact.id,
                                  ),
                                }))
                              }
                            />
                            <span>{artifact.name}</span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </fieldset>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm">
            Overall impact:{" "}
            <strong className="capitalize">{overall || "Not calculated"}</strong>
          </p>
          <div className="flex flex-wrap gap-2">
            {showAnalyze ? (
              <Button
                variant="outline"
                disabled={analyzePending}
                onClick={() => onAnalyze?.()}
              >
                {analyzePending ? "Analyzing…" : "Analyze from evidence"}
              </Button>
            ) : null}
            <Button disabled={!onSave || !complete} onClick={handleConfirm}>
              Confirm categorization
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
