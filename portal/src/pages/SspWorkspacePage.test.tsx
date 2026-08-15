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
import { DEFAULT_CONTROL_RESPONSE_OPTIONS } from "@/sspWorkspaceTypes";

function workspaceFixture(): SspWorkspace {
  return {
    id: "workspace-1",
    name: "Grants Intake Management",
    purpose: "Manage federal grant applications.",
    hosting: "Agency-owned cloud",
    impactLevel: "Moderate",
    provisionalImpactLevel: "moderate",
    categorization: {
      confidentiality: "moderate",
      integrity: "moderate",
      availability: "low",
      confidentialityRationale: "Disclosure could cause serious mission harm.",
      integrityRationale: "Incorrect grant data could cause serious mission harm.",
      availabilityRationale: "Short outages can be handled manually.",
      confidentialityEvidence: [
        { id: "artifact-1:0", artifactId: "artifact-1", locator: "{}" },
      ],
      integrityEvidence: [
        { id: "artifact-1:1", artifactId: "artifact-1", locator: "{}" },
      ],
      availabilityEvidence: [
        { id: "artifact-1:2", artifactId: "artifact-1", locator: "{}" },
      ],
      status: "confirmed",
      confirmed: true,
    },
    authorizationPath: "Agency ATO",
    profile: {
      id: "nist-rev5",
      name: "Agency FISMA — NIST SP 800-53 Rev. 5",
      version: "2026.1",
      baseline: "Moderate",
    },
    controlResponse: DEFAULT_CONTROL_RESPONSE_OPTIONS,
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
    agencyDocxRenders: [],
    systemDefinition: {
      status: "confirmed",
      boundaryNarrative:
        "The authorization boundary includes all production application hosts.",
      diagramLinks: [],
      components: [
        {
          componentId: "web",
          name: "Web tier",
          purpose: "Public UI",
          placement: "inside",
          evidence: [],
        },
      ],
      interconnections: [
        {
          interconnectionId: "ic-1",
          connectedOrganization: "Partner agency",
          connectedSystem: "Identity broker",
          direction: "inbound",
          dataTypes: ["authentication"],
          interfaceProtocol: "HTTPS",
          connectionOwner: "System owner",
          agreementType: "ISA",
          agreementId: "",
          agreementStatus: "",
          agreementExpiration: "",
          boundaryProtections: "TLS and IP allowlisting",
          evidence: [],
        },
      ],
    },
    informationTypes: {
      status: "confirmed",
      entries: [
        {
          entryId: "entry-1",
          catalogIdentifier: "D.14.2",
          catalogTitle: "Financial Management — Grants",
          description: "The system processes grant application data.",
          catalogConfidentiality: "moderate",
          catalogIntegrity: "moderate",
          catalogAvailability: "moderate",
          adjustedConfidentiality: "",
          adjustedIntegrity: "",
          adjustedAvailability: "",
          adjustmentRationale: "",
          evidence: [],
        },
      ],
      confirmed: true,
    },
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

  it("calculates and confirms categorization after intake", () => {
    const onSaveCategorization = vi.fn();
    const workspace = workspaceFixture();
    workspace.impactLevel = "";
    workspace.profile.baseline = "Unconfirmed";
    workspace.categorization = {
      confidentiality: "",
      integrity: "",
      availability: "",
      confidentialityRationale: "",
      integrityRationale: "",
      availabilityRationale: "",
      confidentialityEvidence: [],
      integrityEvidence: [],
      availabilityEvidence: [],
      status: "unconfirmed",
      confirmed: false,
    };
    workspace.evidence = [
      {
        id: "artifact-1",
        name: "system-overview.txt",
        mediaType: "text/plain",
        state: "processed",
        uploadedAt: "2026-07-27",
      },
    ];

    render(
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        actions={{ onSaveCategorization }}
      />,
    );

    for (const [label, value] of [
      ["Confidentiality impact", "moderate"],
      ["Integrity impact", "low"],
      ["Availability impact", "high"],
    ]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.change(screen.getByLabelText("Confidentiality rationale"), {
      target: { value: "Disclosure could cause serious harm." },
    });
    fireEvent.change(screen.getByLabelText("Integrity rationale"), {
      target: { value: "Incorrect data could cause limited harm." },
    });
    fireEvent.change(screen.getByLabelText("Availability rationale"), {
      target: { value: "An outage could stop time-critical grant payments." },
    });

    expect(screen.getByText("high")).toBeInTheDocument();
    for (const checkbox of screen.getAllByRole("checkbox")) {
      fireEvent.click(checkbox);
    }
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm categorization" }),
    );
    expect(onSaveCategorization).toHaveBeenCalledWith({
      confidentiality: "moderate",
      integrity: "low",
      availability: "high",
      confidentialityRationale: "Disclosure could cause serious harm.",
      integrityRationale: "Incorrect data could cause limited harm.",
      availabilityRationale:
        "An outage could stop time-critical grant payments.",
      confidentialityEvidence: [
        {
          id: "artifact-1:categorization",
          artifactId: "artifact-1",
          locator: JSON.stringify({ kind: "categorization_attestation" }),
        },
      ],
      integrityEvidence: [
        {
          id: "artifact-1:categorization",
          artifactId: "artifact-1",
          locator: JSON.stringify({ kind: "categorization_attestation" }),
        },
      ],
      availabilityEvidence: [
        {
          id: "artifact-1:categorization",
          artifactId: "artifact-1",
          locator: JSON.stringify({ kind: "categorization_attestation" }),
        },
      ],
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

  it("opens the information types editor and forwards confirmation", async () => {
    const onSaveInformationTypes = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          source_id: "nist-sp-800-60-rev2",
          title: "NIST SP 800-60",
          version: "2.0.0",
          reference: "https://example.test/sp800-60",
          information_types: [
            {
              identifier: "D.14.2",
              title: "Financial Management — Grants",
              confidentiality: "moderate",
              integrity: "moderate",
              availability: "moderate",
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const workspace = workspaceFixture();
    workspace.informationTypes = {
      status: "unconfirmed",
      entries: [],
      confirmed: false,
    };

    render(
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        initialView="information-types"
        actions={{ onSaveInformationTypes }}
      />,
    );

    await screen.findByLabelText("Catalog information type");
    fireEvent.change(screen.getByLabelText("Catalog information type"), {
      target: { value: "D.14.2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add type" }));
    fireEvent.change(screen.getByLabelText("Information type 1 description"), {
      target: { value: "The system processes grant application data." },
    });

    const confirmButton = screen.getByRole("button", {
      name: "Confirm information types",
    });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    expect(onSaveInformationTypes).toHaveBeenCalledWith({
      entries: [
        expect.objectContaining({
          catalogIdentifier: "D.14.2",
          description: "The system processes grant application data.",
        }),
      ],
    });

    vi.unstubAllGlobals();
  });

  it("opens the system definition editor and forwards confirmation", () => {
    const onSaveSystemDefinition = vi.fn();
    render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        initialView="system-definition"
        actions={{ onSaveSystemDefinition }}
      />,
    );

    expect(
      within(screen.getByRole("main")).getByRole("button", {
        name: "Confirm system definition",
      }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole("main")).getByRole("button", {
        name: "Confirm system definition",
      }),
    );
    expect(onSaveSystemDefinition).toHaveBeenCalledWith(
      expect.objectContaining({
        boundaryNarrative:
          "The authorization boundary includes all production application hosts.",
      }),
    );
  });

  it("shows agency template upload only after ISSO approval on review view", () => {
    const onUploadAgencyTemplate = vi.fn();
    const workspace = workspaceFixture();
    workspace.approvedContentHash = workspace.currentContentHash;

    const { rerender } = render(
      <SspWorkspacePage
        state="success"
        workspace={workspaceFixture()}
        initialView="review"
        actions={{ onUploadAgencyTemplate }}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Generate agency-shaped draft" }),
    ).toBeDisabled();

    rerender(
      <SspWorkspacePage
        state="success"
        workspace={workspace}
        initialView="review"
        actions={{ onUploadAgencyTemplate }}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Generate agency-shaped draft" }),
    ).toBeEnabled();
  });
});
