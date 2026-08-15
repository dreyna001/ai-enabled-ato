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
});
