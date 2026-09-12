import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import type { SspChatHistory } from "@/api/sspWorkspaceChat";
import {
  GlobalChatbotProvider,
  useGlobalChatbot,
} from "@/components/ssp-workspace/GlobalChatbotProvider";
import type {
  AgentContext,
  SspWorkspace,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";
import type { SessionInfo } from "@/types";

const chatMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/api/sspWorkspaceChat", () => ({
  getSspWorkspaceChat: chatMocks.get,
  postSspWorkspaceChat: chatMocks.post,
}));

const primaryId = "10000000-0000-4000-8000-000000000001";
const alternateId = "10000000-0000-4000-8000-000000000002";
const primaryRevision = "20000000-0000-4000-8000-000000000001";
const alternateRevision = "20000000-0000-4000-8000-000000000002";
const sourceRevision = "20000000-0000-4000-8000-000000000003";
const messageHash = "a".repeat(64);

const sessionFor = (actorId: string): SessionInfo => ({
  actor_id: actorId,
  groups: ["isso"],
  csrf_token: "csrf-token",
  portal_origin: "https://portal.example.gov",
});

function workspace(
  id: string,
  name: string,
  revisionId: string,
  patches: SspWorkspace["patches"] = [],
): SspWorkspace {
  return { id, name, revisionId, patches } as SspWorkspace;
}

const primary = workspace(primaryId, "Primary system", primaryRevision);
const alternate = workspace(alternateId, "Alternate system", alternateRevision);

function emptyHistory(id: string, sequence = 0): SspChatHistory {
  return {
    workspace_id: id,
    sequence,
    messages: [],
    has_more: false,
    next_before_sequence: null,
  };
}

function history(
  id: string,
  answer: string,
  options: {
    sourceRevisionId?: string;
    stale?: boolean;
    expiresAt?: string;
  } = {},
): SspChatHistory {
  const expiresAt = options.expiresAt ?? "2033-09-11T12:00:00Z";
  return {
    workspace_id: id,
    sequence: 2,
    messages: [
      {
        message_id: "30000000-0000-4000-8000-000000000001",
        sequence: 1,
        role: "user",
        content: "Tell me about this system.",
        created_at: "2026-09-11T12:00:00Z",
        expires_at: expiresAt,
        sources: [],
        context_fingerprint: messageHash,
        stale: false,
      },
      {
        message_id: "30000000-0000-4000-8000-000000000002",
        sequence: 2,
        role: "assistant",
        content: answer,
        created_at: "2026-09-11T12:00:01Z",
        expires_at: expiresAt,
        sources: options.sourceRevisionId
          ? [
              {
                source_id: "server-source-1",
                label: "Authorization boundary",
                target_id: "AC-2",
                revision_id: options.sourceRevisionId,
                sha256: messageHash,
                kind: "control_statement",
              },
            ]
          : [],
        context_fingerprint: messageHash,
        stale: options.stale ?? false,
      },
    ],
    has_more: false,
    next_before_sequence: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function Bridge({
  current,
  alternateWorkspace = alternate,
  actions,
}: {
  current: SspWorkspace;
  alternateWorkspace?: SspWorkspace;
  actions?: SspWorkspaceActions;
}) {
  const chatbot = useGlobalChatbot();
  useEffect(() => {
    chatbot?.registerWorkspace(current, actions);
  }, [actions, chatbot, current]);

  const focusedContext: AgentContext = {
    targetType: "control",
    targetId: "AC-2",
    label: "Control AC-2",
  };
  return (
    <div>
      <button
        type="button"
        onClick={() => chatbot?.openChat({ context: focusedContext, focus: focusedContext.label })}
      >
        Open focused chat
      </button>
      <button
        type="button"
        onClick={() => chatbot?.openChat({ context: focusedContext, focus: "Different focus" })}
      >
        Open different focus
      </button>
      <button
        type="button"
        onClick={() => chatbot?.registerWorkspace(alternateWorkspace)}
      >
        Switch to alternate
      </button>
      <button type="button" onClick={() => chatbot?.registerWorkspace(current)}>
        Switch to current
      </button>
    </div>
  );
}

function TestApp({
  actorId = "user-one",
  current = primary,
  alternateWorkspace = alternate,
  actions,
}: {
  actorId?: string;
  current?: SspWorkspace;
  alternateWorkspace?: SspWorkspace;
  actions?: SspWorkspaceActions;
}) {
  return (
    <GlobalChatbotProvider key={actorId} session={sessionFor(actorId)}>
      <Bridge
        current={current}
        alternateWorkspace={alternateWorkspace}
        actions={actions}
      />
    </GlobalChatbotProvider>
  );
}

beforeEach(() => {
  chatMocks.get.mockResolvedValue(emptyHistory(primaryId));
  chatMocks.post.mockResolvedValue(emptyHistory(primaryId));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GlobalChatbotProvider", () => {
  it("keeps durable history across closing, reopening, and changing focus", async () => {
    chatMocks.get.mockResolvedValue(history(primaryId, "Persisted answer"));
    chatMocks.post.mockResolvedValue(history(primaryId, "New durable answer"));
    render(<TestApp />);

    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledWith(primaryId, expect.anything()));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("Persisted answer")).toBeInTheDocument();
    expect(screen.getByText(/Focus: Control AC-2 · whole workspace context remains active/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Chat message"), {
      target: { value: "Keep this private." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(chatMocks.post).toHaveBeenCalledOnce());
    expect(await screen.findByText("New durable answer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open SSP assistant" }));
    expect(await screen.findByText("New durable answer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(screen.getByText(/whole workspace context remains active/)).toBeInTheDocument();
  });

  it("isolates histories when switching systems", async () => {
    chatMocks.get.mockImplementation(async (id: string) =>
      id === primaryId
        ? history(primaryId, "Primary private answer")
        : history(alternateId, "Alternate private answer"),
    );
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledWith(primaryId, expect.anything()));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("Primary private answer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Switch to alternate" }));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("Alternate private answer")).toBeInTheDocument();
    expect(screen.queryByText("Primary private answer")).not.toBeInTheDocument();
  });

  it("shows history loading and empty states", async () => {
    const pending = deferred<SspChatHistory>();
    chatMocks.get.mockReturnValue(pending.promise);
    render(<TestApp />);
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(screen.getByRole("status")).toHaveTextContent("Loading private chat history");

    pending.resolve(emptyHistory(primaryId));
    expect(
      await screen.findByText(/No private messages for this workspace yet/),
    ).toBeInTheDocument();
  });

  it("does not retain expired server messages in the browser cache", async () => {
    chatMocks.get
      .mockResolvedValueOnce(
        history(primaryId, "Expired answer", { expiresAt: "2000-01-01T00:00:00Z" }),
      )
      .mockResolvedValueOnce(history(primaryId, "Current answer"));
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(
      await screen.findByText(/No private messages for this workspace yet/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh chat history" }));
    expect(await screen.findByText("Current answer")).toBeInTheDocument();
    expect(screen.queryByText("Expired answer")).not.toBeInTheDocument();
  });

  it("ignores an older same-key refresh response", async () => {
    const firstRefresh = deferred<SspChatHistory>();
    const secondRefresh = deferred<SspChatHistory>();
    chatMocks.get
      .mockResolvedValueOnce(history(primaryId, "Initial answer"))
      .mockReturnValueOnce(firstRefresh.promise)
      .mockReturnValueOnce(secondRefresh.promise);
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh chat history" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh chat history" }));
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(3));

    secondRefresh.resolve(history(primaryId, "Fresh answer"));
    expect(await screen.findByText("Fresh answer")).toBeInTheDocument();
    firstRefresh.resolve(history(primaryId, "Stale late answer"));
    await waitFor(() =>
      expect(screen.queryByText("Stale late answer")).not.toBeInTheDocument(),
    );
  });

  it("ignores a late response from a previous system scope", async () => {
    const primaryRequest = deferred<SspChatHistory>();
    const alternateRequest = deferred<SspChatHistory>();
    chatMocks.get
      .mockReturnValueOnce(primaryRequest.promise)
      .mockReturnValueOnce(alternateRequest.promise);
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Switch to alternate" }));
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(2));

    primaryRequest.resolve(history(primaryId, "Late primary answer"));
    alternateRequest.resolve(history(alternateId, "Current alternate answer"));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("Current alternate answer")).toBeInTheDocument();
    expect(screen.queryByText("Late primary answer")).not.toBeInTheDocument();
  });

  it("resends the exact pending payload on explicit retry", async () => {
    chatMocks.post
      .mockRejectedValueOnce(new ApiError(503, "Temporary service failure"))
      .mockResolvedValueOnce(history(primaryId, "Recovered answer"));
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    fireEvent.change(screen.getByLabelText("Chat message"), {
      target: { value: "Retry this exact request." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(chatMocks.post).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open different focus" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry send" }));
    await waitFor(() => expect(chatMocks.post).toHaveBeenCalledTimes(2));

    const firstInput = chatMocks.post.mock.calls[0][2];
    const retryInput = chatMocks.post.mock.calls[1][2];
    expect(retryInput).toMatchObject({
      message: firstInput.message,
      expectedRevisionId: firstInput.expectedRevisionId,
      expectedSequence: firstInput.expectedSequence,
      focus: firstInput.focus,
      requestId: firstInput.requestId,
    });
    expect(await screen.findByText("Recovered answer")).toBeInTheDocument();
  });

  it("marks stale answers and renders only safe app-generated source links", async () => {
    chatMocks.get.mockResolvedValue(
      history(primaryId, "Advisory answer", { sourceRevisionId: sourceRevision }),
    );
    render(<TestApp />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("Historical reference")).toBeInTheDocument();
    const source = screen.getByRole("link", { name: "Authorization boundary" });
    expect(source).toHaveAttribute(
      "href",
      `/ssp?workspace_id=${primaryId}&view=controls&source_id=server-source-1&target_id=AC-2`,
    );
  });

  it("keeps Propose SSP edit in the shared approval flow", async () => {
    const onAskAgent = vi.fn();
    const onApplyPatch = vi.fn();
    const patchWorkspace = workspace(primaryId, "Primary system", primaryRevision, [
      {
        id: "patch-1",
        summary: "Require quarterly access review.",
        state: "proposed",
        targetLabels: ["AC-2"],
      },
    ]);
    render(
      <TestApp
        current={patchWorkspace}
        actions={{ onAskAgent, onApplyPatch }}
      />,
    );
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    fireEvent.change(screen.getByLabelText("Chat message"), {
      target: { value: "Propose a quarterly review requirement." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Propose SSP edit" }));

    expect(onAskAgent).toHaveBeenCalledWith(
      expect.objectContaining({ targetId: "AC-2" }),
      "Propose a quarterly review requirement.",
    );
    expect(chatMocks.post).not.toHaveBeenCalled();
    expect(screen.getByText("Shared system review proposals")).toBeInTheDocument();
    expect(screen.getByText("Approval required")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve & apply" }));
    expect(onApplyPatch).toHaveBeenCalledWith("patch-1");
  });

  it("clears the private view when the authenticated actor changes", async () => {
    chatMocks.get
      .mockResolvedValueOnce(history(primaryId, "First actor answer"))
      .mockResolvedValueOnce(emptyHistory(primaryId));
    const view = render(<TestApp actorId="actor-one" />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Open focused chat" }));
    expect(await screen.findByText("First actor answer")).toBeInTheDocument();

    view.rerender(<TestApp actorId="actor-two" />);
    await waitFor(() => expect(chatMocks.get).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Open SSP assistant" }));
    expect(
      await screen.findByText(/No private messages for this workspace yet/),
    ).toBeInTheDocument();
    expect(screen.queryByText("First actor answer")).not.toBeInTheDocument();
  });
});
