import { useState } from "react";
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
  CategorizationChange,
  ImpactLevel,
  SystemCategorization,
} from "@/sspWorkspaceTypes";

const IMPACTS: Array<{
  key: "confidentiality" | "integrity" | "availability";
  rationaleKey:
    | "confidentialityRationale"
    | "integrityRationale"
    | "availabilityRationale";
  label: string;
  question: string;
}> = [
  {
    key: "confidentiality",
    rationaleKey: "confidentialityRationale",
    label: "Confidentiality",
    question: "What harm could result if information is disclosed?",
  },
  {
    key: "integrity",
    rationaleKey: "integrityRationale",
    label: "Integrity",
    question: "What harm could result if information is changed incorrectly?",
  },
  {
    key: "availability",
    rationaleKey: "availabilityRationale",
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

export function SystemCategorizationPanel({
  categorization,
  provisionalImpactLevel,
  onSave,
}: {
  categorization: SystemCategorization;
  provisionalImpactLevel: ImpactLevel;
  onSave?: (change: CategorizationChange) => void;
}) {
  const [values, setValues] = useState<CategorizationChange>({
    confidentiality: categorization.confidentiality,
    integrity: categorization.integrity,
    availability: categorization.availability,
    confidentialityRationale: categorization.confidentialityRationale,
    integrityRationale: categorization.integrityRationale,
    availabilityRationale: categorization.availabilityRationale,
  });
  const overall = overallImpact(values);
  const complete =
    Boolean(overall) &&
    IMPACTS.every(({ rationaleKey }) => values[rationaleKey].trim());

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">System categorization</CardTitle>
            <CardDescription>
              Review after evidence analysis. Overall impact is the highest C, I,
              or A value.
            </CardDescription>
          </div>
          <Badge variant={categorization.confirmed ? "success" : "warning"}>
            {categorization.confirmed ? "Confirmed" : "Unconfirmed"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {!categorization.confirmed ? (
          <p className="rounded-sm border bg-muted/30 p-3 text-xs text-muted-foreground">
            A provisional {provisionalImpactLevel} baseline keeps the workspace
            usable. It is replaced when this categorization is confirmed.
          </p>
        ) : null}
        <div className="grid gap-4 xl:grid-cols-3">
          {IMPACTS.map(({ key, rationaleKey, label, question }) => (
            <fieldset key={key} className="space-y-2 rounded-sm border p-3">
              <legend className="px-1 text-sm font-medium">{label}</legend>
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
              </label>
            </fieldset>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm">
            Overall impact:{" "}
            <strong className="capitalize">{overall || "Not calculated"}</strong>
          </p>
          <Button
            disabled={!onSave || !complete}
            onClick={() => onSave?.(values)}
          >
            Confirm categorization
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
