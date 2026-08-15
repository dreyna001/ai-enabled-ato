import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchSp80060Catalog } from "@/api/sspWorkspace";
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
  EvidenceArtifact,
  ImpactLevel,
  InformationTypeMappingEntry,
  InformationTypes,
  InformationTypesChange,
  Sp80060Catalog,
  Sp80060CatalogEntry,
} from "@/sspWorkspaceTypes";

const IMPACT_OPTIONS: Array<{ value: ImpactLevel | ""; label: string }> = [
  { value: "", label: "Catalog default" },
  { value: "low", label: "Low" },
  { value: "moderate", label: "Moderate" },
  { value: "high", label: "High" },
];

function statusBadgeVariant(
  status: InformationTypes["status"],
): "success" | "warning" | "secondary" {
  if (status === "confirmed") return "success";
  if (status === "stale") return "warning";
  return "secondary";
}

function statusLabel(status: InformationTypes["status"]): string {
  if (status === "confirmed") return "Confirmed";
  if (status === "stale") return "Stale";
  if (status === "unconfirmed") return "Unconfirmed";
  return "Not started";
}

function entryFromCatalog(catalogEntry: Sp80060CatalogEntry): InformationTypeMappingEntry {
  return {
    entryId: "",
    catalogIdentifier: catalogEntry.identifier,
    catalogTitle: catalogEntry.title,
    description: "",
    catalogConfidentiality: catalogEntry.confidentiality,
    catalogIntegrity: catalogEntry.integrity,
    catalogAvailability: catalogEntry.availability,
    adjustedConfidentiality: "",
    adjustedIntegrity: "",
    adjustedAvailability: "",
    adjustmentRationale: "",
    evidence: [],
  };
}

function hasImpactAdjustment(entry: InformationTypeMappingEntry): boolean {
  return (
    (entry.adjustedConfidentiality !== "" &&
      entry.adjustedConfidentiality !== entry.catalogConfidentiality) ||
    (entry.adjustedIntegrity !== "" &&
      entry.adjustedIntegrity !== entry.catalogIntegrity) ||
    (entry.adjustedAvailability !== "" &&
      entry.adjustedAvailability !== entry.catalogAvailability)
  );
}

function isComplete(entries: InformationTypeMappingEntry[]): boolean {
  if (entries.length === 0) return false;
  return entries.every((entry) => {
    if (!entry.catalogIdentifier.trim() || !entry.description.trim()) {
      return false;
    }
    if (hasImpactAdjustment(entry) && !entry.adjustmentRationale.trim()) {
      return false;
    }
    return true;
  });
}

export function InformationTypesPanel({
  informationTypes,
  evidence: _evidence,
  onSave,
}: {
  informationTypes: InformationTypes;
  evidence: EvidenceArtifact[];
  onSave?: (change: InformationTypesChange) => void;
}) {
  const [catalog, setCatalog] = useState<Sp80060Catalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [selectedCatalogId, setSelectedCatalogId] = useState("");
  const [values, setValues] = useState<InformationTypesChange>({
    entries: informationTypes.entries,
  });
  const complete = isComplete(values.entries);
  const usedCatalogIds = new Set(
    values.entries.map((entry) => entry.catalogIdentifier),
  );
  const availableCatalogEntries =
    catalog?.informationTypes.filter(
      (entry) => !usedCatalogIds.has(entry.identifier),
    ) ?? [];

  useEffect(() => {
    let cancelled = false;
    void fetchSp80060Catalog()
      .then((loaded) => {
        if (!cancelled) {
          setCatalog(loaded);
          setCatalogError("");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCatalogError("Unable to load the SP 800-60 catalog.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const updateEntry = (
    index: number,
    change: Partial<InformationTypeMappingEntry>,
  ) => {
    setValues((current) => ({
      entries: current.entries.map((entry, itemIndex) =>
        itemIndex === index ? { ...entry, ...change } : entry,
      ),
    }));
  };

  const addCatalogEntry = () => {
    const catalogEntry = catalog?.informationTypes.find(
      (entry) => entry.identifier === selectedCatalogId,
    );
    if (!catalogEntry) return;
    setValues((current) => ({
      entries: [...current.entries, entryFromCatalog(catalogEntry)],
    }));
    setSelectedCatalogId("");
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="text-base">Information type mapping</CardTitle>
              <CardDescription>
                Map NIST SP 800-60 information types processed by the system,
                including any adjusted impacts and supporting rationale.
              </CardDescription>
            </div>
            <Badge variant={statusBadgeVariant(informationTypes.status)}>
              {statusLabel(informationTypes.status)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {informationTypes.status === "stale" ? (
            <p className="rounded-sm border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Information type mappings changed after confirmation. Review and
              confirm again before ISSO approval.
            </p>
          ) : null}
          {!informationTypes.status ? (
            <p className="rounded-sm border bg-muted/30 p-3 text-xs text-muted-foreground">
              Select at least one SP 800-60 information type and describe how
              the system processes it before confirming.
            </p>
          ) : null}
          {catalog ? (
            <p className="text-xs text-muted-foreground">
              {catalog.title} · version {catalog.version}
            </p>
          ) : null}
          {catalogError ? (
            <p className="text-xs text-destructive">{catalogError}</p>
          ) : null}

          <div className="flex flex-wrap items-end gap-3 rounded-sm border p-3">
            <label className="block min-w-64 flex-1 text-xs">
              <span className="mb-1 block text-muted-foreground">
                Add catalog information type
              </span>
              <select
                aria-label="Catalog information type"
                className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                value={selectedCatalogId}
                onChange={(event) => setSelectedCatalogId(event.target.value)}
                disabled={availableCatalogEntries.length === 0}
              >
                <option value="">
                  {availableCatalogEntries.length === 0
                    ? "All catalog types added"
                    : "Select information type"}
                </option>
                {availableCatalogEntries.map((entry) => (
                  <option key={entry.identifier} value={entry.identifier}>
                    {entry.identifier} · {entry.title}
                  </option>
                ))}
              </select>
            </label>
            <Button
              type="button"
              variant="outline"
              disabled={!selectedCatalogId}
              onClick={addCatalogEntry}
            >
              <Plus aria-hidden="true" />
              Add type
            </Button>
          </div>

          {values.entries.length === 0 ? (
            <p className="rounded-sm border border-dashed p-3 text-xs text-muted-foreground">
              No information types mapped yet.
            </p>
          ) : (
            <div className="space-y-3">
              {values.entries.map((entry, index) => (
                <fieldset
                  key={`${entry.catalogIdentifier}:${index}`}
                  className="space-y-3 rounded-sm border p-3"
                >
                  <legend className="px-1 text-sm font-medium">
                    {entry.catalogIdentifier}
                    {entry.catalogTitle ? ` · ${entry.catalogTitle}` : ""}
                  </legend>
                  <p className="text-xs text-muted-foreground">
                    Catalog impacts: C {entry.catalogConfidentiality} · I{" "}
                    {entry.catalogIntegrity} · A {entry.catalogAvailability}
                  </p>
                  <label className="block text-xs">
                    <span className="mb-1 block text-muted-foreground">
                      System-specific description
                    </span>
                    <textarea
                      aria-label={`Information type ${index + 1} description`}
                      className="min-h-20 w-full resize-y rounded-sm border bg-background px-3 py-2 text-sm"
                      value={entry.description}
                      onChange={(event) =>
                        updateEntry(index, { description: event.target.value })
                      }
                    />
                  </label>
                  <div className="grid gap-3 md:grid-cols-3">
                    {(
                      [
                        ["adjustedConfidentiality", "Adjusted confidentiality"],
                        ["adjustedIntegrity", "Adjusted integrity"],
                        ["adjustedAvailability", "Adjusted availability"],
                      ] as const
                    ).map(([field, label]) => (
                      <label key={field} className="block text-xs">
                        <span className="mb-1 block text-muted-foreground">
                          {label}
                        </span>
                        <select
                          aria-label={`Information type ${index + 1} ${label}`}
                          className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                          value={entry[field]}
                          onChange={(event) =>
                            updateEntry(index, {
                              [field]: event.target.value as ImpactLevel | "",
                            })
                          }
                        >
                          {IMPACT_OPTIONS.map((option) => (
                            <option key={option.label} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>
                  {hasImpactAdjustment(entry) ? (
                    <label className="block text-xs">
                      <span className="mb-1 block text-muted-foreground">
                        Adjustment rationale
                      </span>
                      <textarea
                        aria-label={`Information type ${index + 1} adjustment rationale`}
                        className="min-h-20 w-full resize-y rounded-sm border bg-background px-3 py-2 text-sm"
                        value={entry.adjustmentRationale}
                        onChange={(event) =>
                          updateEntry(index, {
                            adjustmentRationale: event.target.value,
                          })
                        }
                      />
                    </label>
                  ) : null}
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove information type ${index + 1}`}
                      onClick={() =>
                        setValues((current) => ({
                          entries: current.entries.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                      Remove
                    </Button>
                  </div>
                </fieldset>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button
          type="button"
          disabled={!onSave || !complete}
          onClick={() => onSave?.(values)}
        >
          Confirm information types
        </Button>
      </div>
    </div>
  );
}
