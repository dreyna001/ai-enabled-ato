import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SspWorkspacePage } from "@/pages/SspWorkspacePage";
import type { SspWorkspace } from "@/sspWorkspaceTypes";

function workspaceFixture(): SspWorkspace {
  return {
    id: "workspace-1",
    name: "Grants Intake Management",
    purpose: "Manage federal grant applications.",
    hosting: "Agency-owned cloud",
    impactLevel: "Moderate",
    authorizationPath: "Agency ATO",
    profile: {
      id: "nist-rev5",
      name: "Agency FISMA — NIST SP 800-53 Rev. 5",
      version: "2026.1",
      baseline: "Moderate",
    },
    revisionId: "rev-4",
    revisionUpdatedAt: "2026-07-27T12:00:00Z",
    lastAgentUpdateAt: "2026-07-27T11:30:00Z",
    currentContentHash: "hash-4",
    approvedContentHash: null,
    processingJobsTerminal: true,
    revisionSaved: true,
    internallyConsistent: true,
    requirements: [
      { id: "purpose", label: "System purpose", required: true },
    ],
    evidence: [
      {
        id: "artifact-1",
        name: "architecture.png",
        mediaType: "image/png",
        state: "processed",
        uploadedAt: "Jul 27, 2026",
      },
    ],
    sections: [
      {
        id: "section-1",
        title: "System Description",
        content: "The system manages grants.",
        state: "generated",
        requirementIds: ["purpose"],
        satisfiedRequirementIds: ["purpose"],
        evidenceLinks: [
          { id: "link-1", artifactId: "artifact-1", locator: "image:1" },
        ],
      },
    ],
    controls: [
      {
        id: "AC-2",
        title: "Account Management",
        family: "Access Control",
        state: "partial",
        implementationStatus: "Implemented",
        responsibility: "Hybrid",
        statement: "The application uses agency identity services.",
        evidenceLinks: [
          { id: "link-2", artifactId: "artifact-1", locator: "image:1" },
        ],
        unresolvedReason: "Account review frequency is unknown.",
      },
    ],
    questions: [
      {
        id: "question-1",
        targetType: "control",
        targetId: "AC-2",
        prompt: "How often are privileged roles reviewed?",
        owner: "System owner",
        state: "open",
      },
    ],
    patches: [
      {
        id: "patch-1",
        summary: "Add the confirmed quarterly account review frequency.",
        state: "proposed",
        targetLabels: ["AC-2", "SSP section 6.3"],
      },
    ],
  };
}

afterEach(cleanup);

describe("SspWorkspacePage", () => {
  it("renders explicit loading, error, and empty states", () => {
    const { rerender } = render(<SspWorkspacePage state="loading" />);
    expect(
      screen.getByLabelText("Loading SSP workspace"),
    ).toBeInTheDocument();

    rerender(
      <SspWorkspacePage state="error" message="The service is unavailable." />,
    );
    expect(screen.getByText("SSP workspace unavailable")).toBeInTheDocument();
    expect(screen.getByText("The service is unavailable.")).toBeInTheDocument();

    rerender(<SspWorkspacePage state="empty" />);
    expect(screen.getByText("No system workspace")).toBeInTheDocument();
  });

  it("renders deterministic workspace metrics and navigates to recorded questions", () => {
    render(
      <SspWorkspacePage state="success" workspace={workspaceFixture()} />,
    );

    expect(
      screen.getByRole("heading", { name: "Grants Intake Management" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("100%")).toHaveLength(2);
    expect(
      screen.getByText("1/1 required items satisfied"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Resolve 1 open questions/i }),
    );
    expect(screen.getByText("How often are privileged roles reviewed?")).toBeInTheDocument();
  });

  it("opens the agent in the selected control context and forwards the instruction", () => {
    const onAskAgent = vi.fn();
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        initialView="controls"
        actions={{ onAskAgent }}
      />,
    );

    fireEvent.click(
      within(screen.getByRole("main")).getByRole("button", {
        name: "Ask agent",
      }),
    );
    expect(screen.getByText("Context: Control AC-2 · Account Management")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Agent instruction"), {
      target: { value: "Use quarterly review frequency." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send to agent" }));

    expect(onAskAgent).toHaveBeenCalledWith(
      {
        targetType: "control",
        targetId: "AC-2",
        label: "Control AC-2 · Account Management",
      },
      "Use quarterly review frequency.",
    );
  });

  it("forwards direct control edits without approving them", () => {
    const onSaveControl = vi.fn();
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        initialView="controls"
        actions={{ onSaveControl }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Implementation statement"), {
      target: { value: "Updated implementation statement." },
    });
    fireEvent.click(
      within(screen.getByRole("main")).getByRole("button", {
        name: "Save control",
      }),
    );

    expect(onSaveControl).toHaveBeenCalledWith({
      controlId: "AC-2",
      implementationStatus: "Implemented",
      responsibility: "Hybrid",
      statement: "Updated implementation statement.",
    });
    expect(screen.queryByText("ISSO approved")).not.toBeInTheDocument();
  });
});
