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

  it("resolves a simple question with direct text instead of an agent call", () => {
    const onAnswerQuestion = vi.fn();
    const workspace = workspaceFixture();
    workspace.sections[0] = {
      ...workspace.sections[0],
      id: "system.owner",
      title: "System Owner",
      content: "Dana Holloway, Director, Office of Grants Operations",
    };
    workspace.questions[0] = {
      ...workspace.questions[0],
      targetType: "ssp_section",
      targetId: "system.owner",
      prompt: "Who is the current system owner for FGRS?",
    };

    render(
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        initialView="questions"
        actions={{ onAnswerQuestion }}
      />,
    );

    expect(
      screen.getByLabelText(
        "Answer Who is the current system owner for FGRS?",
      ),
    ).toHaveValue("Dana Holloway, Director, Office of Grants Operations");
    expect(
      screen.queryByRole("button", { name: "Resolve with agent" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
    expect(onAnswerQuestion).toHaveBeenCalledWith({
      questionId: "question-1",
      answer: "Dana Holloway, Director, Office of Grants Operations",
    });
  });

  it("shows generation progress and disables repeated generation", () => {
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        generationPending
        actions={{ onGenerate: vi.fn() }}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Generating documents…" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("status"),
    ).toHaveTextContent(
      "Analyzing evidence and drafting supported SSP content.",
    );
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

  it("opens another system and starts a new-system flow from the workspace header", () => {
    const onOpenWorkspace = vi.fn();
    const onNewWorkspace = vi.fn();
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        availableWorkspaces={[
          { id: "workspace-1", name: "Grants Intake Management" },
          { id: "workspace-2", name: "Case Review System" },
        ]}
        actions={{ onOpenWorkspace, onNewWorkspace }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Open system"), {
      target: { value: "workspace-2" },
    });
    expect(onOpenWorkspace).toHaveBeenCalledWith("workspace-2");

    fireEvent.click(screen.getByRole("button", { name: "New system" }));
    expect(onNewWorkspace).toHaveBeenCalledOnce();
  });

  it("confirms evidence removal before analysis starts", () => {
    const onRemoveEvidence = vi.fn();
    const workspace = workspaceFixture();
    workspace.sections = workspace.sections.map((section) => ({
      ...section,
      content: "",
      state: "empty",
      satisfiedRequirementIds: [],
      evidenceLinks: [],
    }));
    workspace.controls = workspace.controls.map((control) => ({
      ...control,
      statement: "",
      state: "empty",
      evidenceLinks: [],
      unresolvedReason: null,
    }));
    workspace.questions = [];
    workspace.patches = [];

    render(
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        initialView="evidence"
        actions={{ onRemoveEvidence }}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Remove architecture.png" }),
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove evidence" }));
    expect(onRemoveEvidence).toHaveBeenCalledWith("artifact-1");
  });

  it("does not allow evidence removal after analysis has started", () => {
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        initialView="evidence"
        actions={{ onRemoveEvidence: vi.fn() }}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Remove architecture.png" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Evidence cannot be removed after analysis has started."),
    ).toBeInTheDocument();
  });
});
