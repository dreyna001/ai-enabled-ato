import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemDefinitionPanel } from "@/components/ssp-workspace/SystemDefinitionPanel";
import type { SystemDefinition } from "@/sspWorkspaceTypes";

const systemDefinition: SystemDefinition = {
  status: "unconfirmed",
  boundaryNarrative: "",
  diagramLinks: [],
  components: [],
  interconnections: [],
  proposal: null,
};

afterEach(cleanup);

describe("SystemDefinitionPanel", () => {
  it("requires complete structured fields before confirming", () => {
    const onSave = vi.fn();
    render(
      <SystemDefinitionPanel
        systemDefinition={systemDefinition}
        evidence={[
          {
            id: "artifact-1",
            name: "architecture.png",
            mediaType: "image/png",
            state: "processed",
            uploadedAt: "2026-07-27",
          },
        ]}
        onSave={onSave}
      />,
    );

    const confirmButton = screen.getByRole("button", {
      name: "Confirm system definition",
    });
    expect(confirmButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Add component" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Add interconnection" }),
    );

    fireEvent.change(
      screen.getByLabelText("Authorization boundary narrative"),
      {
        target: {
          value:
            "The authorization boundary includes all production application hosts.",
        },
      },
    );
    fireEvent.change(screen.getByLabelText("Component 1 name"), {
      target: { value: "Web tier" },
    });
    fireEvent.change(screen.getByLabelText("Component 1 purpose"), {
      target: { value: "Public UI" },
    });
    fireEvent.change(
      screen.getByLabelText("Interconnection 1 organization"),
      {
        target: { value: "Partner agency" },
      },
    );
    fireEvent.change(screen.getByLabelText("Interconnection 1 system"), {
      target: { value: "Identity broker" },
    });
    fireEvent.change(screen.getByLabelText("Interconnection 1 data types"), {
      target: { value: "authentication" },
    });
    fireEvent.change(screen.getByLabelText("Interconnection 1 protocol"), {
      target: { value: "HTTPS" },
    });
    fireEvent.change(screen.getByLabelText("Interconnection 1 owner"), {
      target: { value: "System owner" },
    });
    fireEvent.change(
      screen.getByLabelText("Interconnection 1 agreement type"),
      {
        target: { value: "ISA" },
      },
    );
    fireEvent.change(
      screen.getByLabelText("Interconnection 1 boundary protections"),
      {
        target: { value: "TLS and IP allowlisting" },
      },
    );

    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        boundaryNarrative:
          "The authorization boundary includes all production application hosts.",
        components: [
          expect.objectContaining({ name: "Web tier", purpose: "Public UI" }),
        ],
        interconnections: [
          expect.objectContaining({
            connectedOrganization: "Partner agency",
            connectedSystem: "Identity broker",
          }),
        ],
      }),
    );
  });

  it("calls analyze handler for diagram evidence", () => {
    const onAnalyzeDiagram = vi.fn();
    render(
      <SystemDefinitionPanel
        systemDefinition={systemDefinition}
        evidence={[
          {
            id: "artifact-1",
            name: "architecture.png",
            mediaType: "image/png",
            state: "processed",
            uploadedAt: "2026-07-27",
          },
        ]}
        onAnalyzeDiagram={onAnalyzeDiagram}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Analyze from diagram" }));
    expect(onAnalyzeDiagram).toHaveBeenCalledWith("artifact-1", 1);
  });

  it("keeps empty analysis results empty while preserving manual add controls", () => {
    render(
      <SystemDefinitionPanel
        systemDefinition={systemDefinition}
        evidence={[]}
      />,
    );

    expect(
      screen.getByText(/No components were observed or recorded/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No interconnections were observed or recorded/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Component 1 name")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Interconnection 1 organization"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Confirm system definition" }),
    ).toBeDisabled();
  });

  it("shows source coverage and low-confidence warnings while allowing correction", () => {
    const onSave = vi.fn();
    render(
      <SystemDefinitionPanel
        systemDefinition={{
          status: "unconfirmed",
          boundaryNarrative:
            "The authorization boundary includes the production application hosts.",
          diagramLinks: [],
          components: [
            {
              componentId: "web",
              name: "Unclear web label",
              purpose: "Serves the user interface",
              placement: "inside",
              evidence: [],
            },
          ],
          interconnections: [
            {
              interconnectionId: "logging-link",
              connectedOrganization: "Agency operations",
              connectedSystem: "Shared logging service",
              direction: "outbound",
              dataTypes: ["audit events"],
              interfaceProtocol: "TLS",
              connectionOwner: "Operations",
              agreementType: "ISA",
              agreementId: "",
              agreementStatus: "",
              agreementExpiration: "",
              boundaryProtections: "Encrypted transport",
              evidence: [],
            },
          ],
          proposal: {
            source: "diagram_analysis",
            analysisStatus: "semantic_analysis_complete",
            artifactId: "artifact-1",
            artifactSha256: "a".repeat(64),
            displayFilename: "architecture.pdf",
            locator: { kind: "rendered_page", page: 3 },
            sourceRevisionId: "revision-1",
            componentCount: 1,
            interconnectionCount: 1,
            lowConfidenceComponentCount: 1,
            lowConfidenceInterconnectionCount: 1,
            conflictCount: 1,
            stale: false,
            conflicts: [
              {
                field: "logging placement",
                diagramValue: "outside",
                textValue: "inside",
                note: "Confirm the boundary before confirmation.",
              },
            ],
          },
        }}
        evidence={[]}
        onSave={onSave}
      />,
    );

    expect(screen.getByText("Semantic diagram analysis complete")).toBeInTheDocument();
    expect(screen.getByText(/SHA-256/)).toBeInTheDocument();
    expect(screen.getByText(/page 3/)).toBeInTheDocument();
    expect(screen.getByText(/Observed coverage: 1 component/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Low-confidence extraction: 1 component and 1 interconnection/,
    );

    fireEvent.change(screen.getByLabelText("Component 1 name"), {
      target: { value: "Confirmed web tier" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm system definition" }),
    );

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        components: [expect.objectContaining({ name: "Confirmed web tier" })],
      }),
    );
  });

  it("identifies a stale diagram proposal without treating it as approval", () => {
    render(
      <SystemDefinitionPanel
        systemDefinition={{
          ...systemDefinition,
          status: "stale",
          proposal: {
            source: "diagram_analysis",
            analysisStatus: "semantic_analysis_complete",
            artifactId: "artifact-1",
            artifactSha256: "b".repeat(64),
            displayFilename: "architecture.png",
            locator: { kind: "image", page: 1 },
            componentCount: 0,
            interconnectionCount: 0,
            lowConfidenceComponentCount: 0,
            lowConfidenceInterconnectionCount: 0,
            conflictCount: 0,
            stale: false,
            conflicts: [],
          },
        }}
        evidence={[]}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      /This proposal is stale relative to the current system definition/,
    );
    expect(
      screen.getByRole("button", { name: "Confirm system definition" }),
    ).toBeDisabled();
  });

  it("shows an explicit analysis failure without creating observed structure", () => {
    render(
      <SystemDefinitionPanel
        systemDefinition={{
          ...systemDefinition,
          proposal: {
            source: "diagram_analysis",
            analysisStatus: "analysis_failed",
            artifactId: "artifact-1",
            artifactSha256: "c".repeat(64),
            displayFilename: "architecture.pdf",
            locator: { kind: "rendered_page", page: 2 },
            componentCount: null,
            interconnectionCount: null,
            lowConfidenceComponentCount: null,
            lowConfidenceInterconnectionCount: null,
            conflictCount: 0,
            stale: false,
            failureKind: "model_call",
            conflicts: [],
          },
        }}
        evidence={[]}
      />,
    );

    expect(screen.getByText("Semantic diagram analysis failed")).toBeInTheDocument();
    expect(screen.getByText(/Failure classification: model_call/)).toBeInTheDocument();
    expect(
      screen.getByText(/No components were observed or recorded/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No interconnections were observed or recorded/),
    ).toBeInTheDocument();
  });
});
