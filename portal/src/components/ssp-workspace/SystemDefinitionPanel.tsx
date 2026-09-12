import { Plus, Trash2 } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import type {
  BoundaryPlacement,
  EvidenceArtifact,
  InterconnectionDirection,
  SystemDefinition,
  SystemDefinitionChange,
  SystemDefinitionComponent,
  SystemDefinitionDiagramLink,
  SystemDefinitionInterconnection,
} from "@/sspWorkspaceTypes";

const DEFAULT_LOCATOR = '{"page":1}';

function statusBadgeVariant(
  status: SystemDefinition["status"],
): "success" | "warning" | "secondary" {
  if (status === "confirmed") return "success";
  if (status === "stale") return "warning";
  return "secondary";
}

function statusLabel(status: SystemDefinition["status"]): string {
  if (status === "confirmed") return "Confirmed";
  if (status === "stale") return "Stale";
  if (status === "unconfirmed") return "Unconfirmed";
  return "Not started";
}

function parseLocatorInput(value: string): Record<string, unknown> | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return { kind: trimmed };
  }
  return null;
}

function formatLocator(locator: Record<string, unknown>): string {
  return JSON.stringify(locator);
}

function sourceLocation(locator: Record<string, unknown>): string {
  const page = locator.page;
  if (typeof page === "number" && Number.isInteger(page) && page > 0) {
    return `page ${page}`;
  }
  return formatLocator(locator);
}

function emptyComponent(): SystemDefinitionComponent {
  return {
    componentId: "",
    name: "",
    purpose: "",
    placement: "inside",
    evidence: [],
  };
}

function emptyInterconnection(): SystemDefinitionInterconnection {
  return {
    interconnectionId: "",
    connectedOrganization: "",
    connectedSystem: "",
    direction: "inbound",
    dataTypes: [""],
    interfaceProtocol: "",
    connectionOwner: "",
    agreementType: "",
    agreementId: "",
    agreementStatus: "",
    agreementExpiration: "",
    boundaryProtections: "",
    evidence: [],
  };
}

function isComplete(change: SystemDefinitionChange): boolean {
  if (change.boundaryNarrative.trim().length < 20) return false;
  if (change.components.length === 0) return false;
  if (change.interconnections.length === 0) return false;
  if (
    !change.components.every(
      (component) =>
        component.name.trim() &&
        component.purpose.trim() &&
        (component.placement === "inside" ||
          component.placement === "outside" ||
          component.placement === "crossing"),
    )
  ) {
    return false;
  }
  return change.interconnections.every((interconnection) => {
    const required = [
      interconnection.connectedOrganization,
      interconnection.connectedSystem,
      interconnection.interfaceProtocol,
      interconnection.connectionOwner,
      interconnection.agreementType,
      interconnection.boundaryProtections,
    ];
    return (
      required.every((value) => value.trim()) &&
      interconnection.dataTypes.some((value) => value.trim())
    );
  });
}

function isDiagramEvidence(artifact: EvidenceArtifact): boolean {
  if (artifact.state !== "processed") return false;
  const name = artifact.name.toLowerCase();
  return (
    name.endsWith(".png") ||
    name.endsWith(".jpg") ||
    name.endsWith(".jpeg") ||
    name.endsWith(".webp") ||
    name.endsWith(".pdf")
  );
}

export function SystemDefinitionPanel({
  systemDefinition,
  evidence,
  onSave,
  onAnalyzeDiagram,
  analyzeBusy = false,
}: {
  systemDefinition: SystemDefinition;
  evidence: EvidenceArtifact[];
  onSave?: (change: SystemDefinitionChange) => void;
  onAnalyzeDiagram?: (artifactId: string, pageNumber: number) => void;
  analyzeBusy?: boolean;
}) {
  const processedEvidence = evidence.filter(
    (artifact) => artifact.state === "processed",
  );
  const diagramEvidence = processedEvidence.filter(isDiagramEvidence);
  const [values, setValues] = useState<SystemDefinitionChange>({
    boundaryNarrative: systemDefinition.boundaryNarrative,
    diagramLinks: systemDefinition.diagramLinks,
    components: systemDefinition.components,
    interconnections: systemDefinition.interconnections,
  });
  const [diagramArtifactId, setDiagramArtifactId] = useState(
    diagramEvidence[0]?.id ?? processedEvidence[0]?.id ?? "",
  );
  const [diagramLocator, setDiagramLocator] = useState(DEFAULT_LOCATOR);
  const [diagramLabel, setDiagramLabel] = useState("");
  const [analyzePageNumber, setAnalyzePageNumber] = useState("1");

  useEffect(() => {
    setValues({
      boundaryNarrative: systemDefinition.boundaryNarrative,
      diagramLinks: systemDefinition.diagramLinks,
      components: systemDefinition.components,
      interconnections: systemDefinition.interconnections,
    });
  }, [
    systemDefinition.boundaryNarrative,
    systemDefinition.components,
    systemDefinition.diagramLinks,
    systemDefinition.interconnections,
  ]);
  const complete = isComplete(values);

  const updateComponent = (
    index: number,
    change: Partial<SystemDefinitionComponent>,
  ) => {
    setValues((current) => ({
      ...current,
      components: current.components.map((component, itemIndex) =>
        itemIndex === index ? { ...component, ...change } : component,
      ),
    }));
  };

  const updateInterconnection = (
    index: number,
    change: Partial<SystemDefinitionInterconnection>,
  ) => {
    setValues((current) => ({
      ...current,
      interconnections: current.interconnections.map(
        (interconnection, itemIndex) =>
          itemIndex === index ? { ...interconnection, ...change } : interconnection,
      ),
    }));
  };

  const addDiagramLink = () => {
    const locator = parseLocatorInput(diagramLocator);
    if (!diagramArtifactId || !locator) return;
    const link: SystemDefinitionDiagramLink = {
      artifactId: diagramArtifactId,
      locator,
      ...(diagramLabel.trim() ? { label: diagramLabel.trim() } : {}),
    };
    setValues((current) => ({
      ...current,
      diagramLinks: [...current.diagramLinks, link],
    }));
    setDiagramLabel("");
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="text-base">System definition</CardTitle>
              <CardDescription>
                Confirm the authorization boundary, component inventory, and
                interconnection register together.
              </CardDescription>
            </div>
            <Badge variant={statusBadgeVariant(systemDefinition.status)}>
              {statusLabel(systemDefinition.status)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {systemDefinition.status === "stale" ? (
            <p className="rounded-sm border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Structured sections changed after confirmation. Review and confirm
              again before ISSO approval.
            </p>
          ) : null}
          {!systemDefinition.status ? (
            <p className="rounded-sm border bg-muted/30 p-3 text-xs text-muted-foreground">
              Save boundary narrative, at least one component, and at least one
              interconnection to confirm the system definition.
            </p>
          ) : null}
          {systemDefinition.proposal ? (
            <div
              className={
                systemDefinition.proposal.analysisStatus === "analysis_failed" ||
                systemDefinition.proposal.analysisStatus === "analysis_failure" ||
                systemDefinition.proposal.stale ||
                systemDefinition.status === "stale"
                  ? "space-y-2 rounded-sm border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-muted-foreground"
                  : "space-y-2 rounded-sm border border-sky-500/40 bg-sky-500/10 p-3 text-xs text-muted-foreground"
              }
            >
              <p className="font-medium text-foreground">
                {systemDefinition.proposal.analysisStatus ===
                "semantic_analysis_complete"
                  ? "Semantic diagram analysis complete"
                  : systemDefinition.proposal.analysisStatus === "ingested"
                    ? "Evidence ingested; semantic diagram analysis has not run"
                    : systemDefinition.proposal.analysisStatus === "ocr"
                      ? "OCR completed; semantic diagram analysis has not run"
                      : systemDefinition.proposal.analysisStatus ===
                          "analysis_failed" ||
                        systemDefinition.proposal.analysisStatus ===
                          "analysis_failure"
                        ? "Semantic diagram analysis failed"
                        : "Diagram analysis status is unknown"}
              </p>
              <p>
                Source artifact: {" "}
                <span className="font-medium text-foreground">
                  {systemDefinition.proposal.displayFilename || "unknown filename"}
                </span>{" "}
                ({systemDefinition.proposal.artifactId || "unknown artifact"})
                {systemDefinition.proposal.artifactSha256 ? (
                  <>
                    {" "}· SHA-256{" "}
                    <span className="break-all font-mono text-foreground">
                      {systemDefinition.proposal.artifactSha256}
                    </span>
                  </>
                ) : null}
                {Object.keys(systemDefinition.proposal.locator).length > 0 ? (
                  <> · {sourceLocation(systemDefinition.proposal.locator)}</>
                ) : null}
              </p>
              {systemDefinition.proposal.sourceRevisionId ? (
                <p>
                  Source revision: {" "}
                  <span className="break-all font-mono text-foreground">
                    {systemDefinition.proposal.sourceRevisionId}
                  </span>
                </p>
              ) : null}
              {systemDefinition.proposal.analysisStatus ===
                "semantic_analysis_complete" ? (
                <p>
                  Observed coverage: {" "}
                  {systemDefinition.proposal.componentCount ?? "unknown"} component
                  {systemDefinition.proposal.componentCount === 1 ? "" : "s"}
                  {" · "}
                  {systemDefinition.proposal.interconnectionCount ?? "unknown"}{" "}
                  interconnection
                  {systemDefinition.proposal.interconnectionCount === 1 ? "" : "s"}.
                </p>
              ) : null}
              {systemDefinition.proposal.lowConfidenceComponentCount !== null &&
              systemDefinition.proposal.lowConfidenceInterconnectionCount !==
                null &&
              systemDefinition.proposal.lowConfidenceComponentCount +
                systemDefinition.proposal.lowConfidenceInterconnectionCount >
                0 ? (
                <p role="alert" className="text-amber-300">
                  Low-confidence extraction: {" "}
                  {systemDefinition.proposal.lowConfidenceComponentCount} component
                  {systemDefinition.proposal.lowConfidenceComponentCount === 1
                    ? ""
                    : "s"}
                  {" and "}
                  {systemDefinition.proposal.lowConfidenceInterconnectionCount}{" "}
                  interconnection
                  {systemDefinition.proposal.lowConfidenceInterconnectionCount === 1
                    ? ""
                    : "s"}
                  . Review and correct these fields before confirming.
                </p>
              ) : null}
              {systemDefinition.proposal.stale ||
              systemDefinition.status === "stale" ? (
                <p role="alert" className="text-amber-300">
                  This proposal is stale relative to the current system definition.
                  Review the current fields and confirm again; it is not an
                  authoritative approval.
                </p>
              ) : null}
              {systemDefinition.proposal.failureKind ? (
                <p role="alert" className="text-amber-300">
                  Failure classification: {systemDefinition.proposal.failureKind}.
                </p>
              ) : null}
              {systemDefinition.proposal.conflicts.length > 0 ? (
                <>
                  <p className="font-medium text-foreground">
                    {systemDefinition.proposal.conflicts.length} conflict
                    {systemDefinition.proposal.conflicts.length === 1 ? "" : "s"}{" "}
                    require review.
                  </p>
                  <ul className="list-disc space-y-1 pl-4">
                  {systemDefinition.proposal.conflicts.map((conflict) => (
                    <li key={`${conflict.field}-${conflict.note}`}>
                      <span className="font-medium text-foreground">
                        {conflict.field}:
                      </span>{" "}
                      diagram shows {conflict.diagramValue || "—"}; text says{" "}
                      {conflict.textValue || "—"}. {conflict.note}
                      </li>
                  ))}
                  </ul>
                </>
              ) : null}
            </div>
          ) : null}
          {!systemDefinition.proposal && diagramEvidence.length > 0 ? (
            <p className="rounded-sm border border-slate-500/40 bg-slate-500/10 p-3 text-xs text-muted-foreground">
              Processed evidence is available, but semantic diagram analysis has
              not run. Ingestion or OCR alone does not establish diagram
              components or interconnections.
            </p>
          ) : null}

          {onAnalyzeDiagram && diagramEvidence.length > 0 ? (
            <div className="flex flex-wrap items-end gap-2 rounded-sm border bg-muted/20 p-3">
              <label className="text-sm">
                <span className="mb-1 block font-medium">Analyze diagram</span>
                <select
                  aria-label="Diagram artifact"
                  className="w-full min-w-48 rounded-sm border bg-background px-2 py-1.5 text-sm"
                  value={diagramArtifactId}
                  onChange={(event) => setDiagramArtifactId(event.target.value)}
                >
                  {diagramEvidence.map((artifact) => (
                    <option key={artifact.id} value={artifact.id}>
                      {artifact.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-sm">
                <span className="mb-1 block font-medium">PDF page</span>
                <Input
                  aria-label="Diagram page number"
                  className="w-20"
                  inputMode="numeric"
                  value={analyzePageNumber}
                  onChange={(event) => setAnalyzePageNumber(event.target.value)}
                />
              </label>
              <Button
                type="button"
                variant="secondary"
                disabled={analyzeBusy || !diagramArtifactId}
                onClick={() => {
                  const pageNumber = Number.parseInt(analyzePageNumber, 10);
                  if (!diagramArtifactId || !Number.isFinite(pageNumber) || pageNumber < 1) {
                    return;
                  }
                  onAnalyzeDiagram(diagramArtifactId, pageNumber);
                }}
              >
                {analyzeBusy ? "Analyzing…" : "Analyze from diagram"}
              </Button>
            </div>
          ) : null}

          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              Authorization boundary narrative
            </span>
            <textarea
              aria-label="Authorization boundary narrative"
              className="min-h-32 w-full resize-y rounded-sm border bg-background px-3 py-2 text-sm"
              value={values.boundaryNarrative}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  boundaryNarrative: event.target.value,
                }))
              }
            />
            <span className="mt-1 block text-xs text-muted-foreground">
              Minimum 20 characters.
            </span>
          </label>

          <div className="space-y-3 rounded-sm border p-3">
            <p className="text-sm font-medium">Boundary diagram links</p>
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">
                  Evidence artifact
                </span>
                <select
                  aria-label="Diagram evidence artifact"
                  className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                  value={diagramArtifactId}
                  onChange={(event) => setDiagramArtifactId(event.target.value)}
                  disabled={processedEvidence.length === 0}
                >
                  {processedEvidence.length === 0 ? (
                    <option value="">No processed evidence</option>
                  ) : null}
                  {processedEvidence.map((artifact) => (
                    <option key={artifact.id} value={artifact.id}>
                      {artifact.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">Locator</span>
                <Input
                  aria-label="Diagram locator"
                  value={diagramLocator}
                  onChange={(event) => setDiagramLocator(event.target.value)}
                />
              </label>
              <div className="flex items-end">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!diagramArtifactId}
                  onClick={addDiagramLink}
                >
                  <Plus aria-hidden="true" />
                  Add link
                </Button>
              </div>
            </div>
            <label className="block text-xs">
              <span className="mb-1 block text-muted-foreground">
                Optional label
              </span>
              <Input
                aria-label="Diagram label"
                value={diagramLabel}
                onChange={(event) => setDiagramLabel(event.target.value)}
              />
            </label>
            {values.diagramLinks.length > 0 ? (
              <ul className="space-y-2 text-xs">
                {values.diagramLinks.map((link, index) => (
                  <li
                    key={`${link.artifactId}:${index}`}
                    className="flex items-center justify-between gap-2 rounded-sm border px-3 py-2"
                  >
                    <span>
                      {processedEvidence.find(
                        (artifact) => artifact.id === link.artifactId,
                      )?.name ?? link.artifactId}{" "}
                      · {formatLocator(link.locator)}
                      {link.label ? ` · ${link.label}` : ""}
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove diagram link ${index + 1}`}
                      onClick={() =>
                        setValues((current) => ({
                          ...current,
                          diagramLinks: current.diagramLinks.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        }))
                      }
                    >
                      <Trash2 className="size-4" aria-hidden="true" />
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                Optional links to boundary diagrams in processed evidence.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Component inventory</CardTitle>
          <CardDescription>
            Record each major component and whether it sits inside, outside, or
            crosses the boundary.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {values.components.length === 0 ? (
            <p className="rounded-sm border border-dashed p-3 text-xs text-muted-foreground">
              No components were observed or recorded. Add a component only when
              supported by the diagram or another evidence source.
            </p>
          ) : null}
          {values.components.map((component, index) => (
            <div
              key={`component-${index}`}
              className="grid gap-3 rounded-sm border p-3 md:grid-cols-2 xl:grid-cols-4"
            >
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">Name</span>
                <Input
                  aria-label={`Component ${index + 1} name`}
                  value={component.name}
                  onChange={(event) =>
                    updateComponent(index, { name: event.target.value })
                  }
                />
              </label>
              <label className="block text-xs md:col-span-2 xl:col-span-2">
                <span className="mb-1 block text-muted-foreground">Purpose</span>
                <Input
                  aria-label={`Component ${index + 1} purpose`}
                  value={component.purpose}
                  onChange={(event) =>
                    updateComponent(index, { purpose: event.target.value })
                  }
                />
              </label>
              <label className="block text-xs">
                <span className="mb-1 block text-muted-foreground">
                  Placement
                </span>
                <select
                  aria-label={`Component ${index + 1} placement`}
                  className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                  value={component.placement}
                  onChange={(event) =>
                    updateComponent(index, {
                      placement: event.target.value as BoundaryPlacement,
                    })
                  }
                >
                  <option value="inside">Inside</option>
                  <option value="outside">Outside</option>
                  <option value="crossing">Crossing</option>
                </select>
              </label>
              <div className="flex items-end justify-end md:col-span-2 xl:col-span-4">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={values.components.length === 1}
                  aria-label={`Remove component ${index + 1}`}
                  onClick={() =>
                    setValues((current) => ({
                      ...current,
                      components: current.components.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    }))
                  }
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setValues((current) => ({
                ...current,
                components: [...current.components, emptyComponent()],
              }))
            }
          >
            <Plus aria-hidden="true" />
            Add component
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Interconnection register</CardTitle>
          <CardDescription>
            Document each external connection, agreement, and boundary
            protection.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {values.interconnections.length === 0 ? (
            <p className="rounded-sm border border-dashed p-3 text-xs text-muted-foreground">
              No interconnections were observed or recorded. Add one only when a
              connected system and direction are supported by evidence.
            </p>
          ) : null}
          {values.interconnections.map((interconnection, index) => (
            <div
              key={`interconnection-${index}`}
              className="space-y-3 rounded-sm border p-3"
            >
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Connected organization
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} organization`}
                    value={interconnection.connectedOrganization}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        connectedOrganization: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Connected system
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} system`}
                    value={interconnection.connectedSystem}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        connectedSystem: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Direction
                  </span>
                  <select
                    aria-label={`Interconnection ${index + 1} direction`}
                    className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                    value={interconnection.direction}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        direction: event.target.value as InterconnectionDirection,
                      })
                    }
                  >
                    <option value="inbound">Inbound</option>
                    <option value="outbound">Outbound</option>
                    <option value="bidirectional">Bidirectional</option>
                  </select>
                </label>
                <label className="block text-xs md:col-span-2">
                  <span className="mb-1 block text-muted-foreground">
                    Data types (comma-separated)
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} data types`}
                    value={interconnection.dataTypes.join(", ")}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        dataTypes: event.target.value
                          .split(",")
                          .map((value) => value.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Interface protocol
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} protocol`}
                    value={interconnection.interfaceProtocol}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        interfaceProtocol: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Connection owner
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} owner`}
                    value={interconnection.connectionOwner}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        connectionOwner: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="block text-xs">
                  <span className="mb-1 block text-muted-foreground">
                    Agreement type
                  </span>
                  <Input
                    aria-label={`Interconnection ${index + 1} agreement type`}
                    value={interconnection.agreementType}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        agreementType: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="block text-xs md:col-span-2 xl:col-span-3">
                  <span className="mb-1 block text-muted-foreground">
                    Boundary protections
                  </span>
                  <textarea
                    aria-label={`Interconnection ${index + 1} boundary protections`}
                    className="min-h-20 w-full resize-y rounded-sm border bg-background px-3 py-2 text-sm"
                    value={interconnection.boundaryProtections}
                    onChange={(event) =>
                      updateInterconnection(index, {
                        boundaryProtections: event.target.value,
                      })
                    }
                  />
                </label>
              </div>
              <div className="flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={values.interconnections.length === 1}
                  aria-label={`Remove interconnection ${index + 1}`}
                  onClick={() =>
                    setValues((current) => ({
                      ...current,
                      interconnections: current.interconnections.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    }))
                  }
                >
                  <Trash2 className="size-4" aria-hidden="true" />
                  Remove
                </Button>
              </div>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setValues((current) => ({
                ...current,
                interconnections: [
                  ...current.interconnections,
                  emptyInterconnection(),
                ],
              }))
            }
          >
            <Plus aria-hidden="true" />
            Add interconnection
          </Button>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button
          type="button"
          disabled={!onSave || !complete}
          onClick={() => onSave?.(values)}
        >
          Confirm system definition
        </Button>
      </div>
    </div>
  );
}
