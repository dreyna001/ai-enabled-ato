import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Bot } from "lucide-react";
import {
  ApiError,
  isCancelledRequest,
} from "@/api/client";
import {
  getSspWorkspaceChat,
  postSspWorkspaceChat,
  type SspChatHistory,
  type SspChatMessage,
} from "@/api/sspWorkspaceChat";
import { Button } from "@/components/ui/button";
import {
  ContextualAgentDrawer,
  type ChatHistoryState,
} from "@/components/ssp-workspace/ContextualAgentDrawer";
import type {
  AgentContext,
  AgentPatch,
  SspWorkspace,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";
import type { SessionInfo } from "@/types";
import { formatApiError } from "@/utils/formatApiError";

type ChatbotContextValue = {
  identityKey: string;
  registerWorkspace: (
    workspace: SspWorkspace,
    actions?: SspWorkspaceActions,
  ) => void;
  openChat: (options?: { context?: AgentContext; focus?: string }) => void;
};

// A nullable context keeps SSP page unit tests independent of the authenticated shell.
const ChatbotContext = createContext<ChatbotContextValue | null>(null);

type WorkspaceScope = {
  id: string;
  name: string;
  revisionId: string;
  patches: AgentPatch[];
  signature: string;
};

type PendingSend = {
  message: string;
  requestId: string;
  key: string;
  revisionId: string;
  expectedSequence: number;
  focus: string | undefined;
};

function scopeKey(actorId: string, workspaceId: string): string {
  return `${actorId}:${workspaceId}`;
}

function patchSignature(patches: AgentPatch[]): string {
  return patches
    .map((patch) => `${patch.id}:${patch.state}:${patch.summary}:${patch.targetLabels.join(",")}`)
    .join("|");
}

function mergeMessages(
  current: SspChatMessage[],
  incoming: SspChatMessage[],
): SspChatMessage[] {
  const isActive = (message: SspChatMessage) =>
    new Date(message.expires_at).getTime() > Date.now();
  const byId = new Map(
    current
      .filter(isActive)
      .map((message) => [message.message_id, message]),
  );
  for (const message of incoming) {
    if (isActive(message)) byId.set(message.message_id, message);
  }
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

function isOfflineError(error: unknown): boolean {
  return (
    (typeof navigator !== "undefined" && navigator.onLine === false) ||
    (error instanceof TypeError && error.message.toLowerCase().includes("fetch"))
  );
}

function isModelDisabledError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return new Set([
    "model_not_configured",
    "model_policy_not_approved",
    "model_routing_denied",
    "prohibited_model_action",
  ]).has(error.errorCode ?? "");
}

function historyStateForError(error: unknown): ChatHistoryState {
  if (isOfflineError(error)) return "offline";
  if (isModelDisabledError(error)) return "modeldisabled";
  return "error";
}

function isPermissionDeniedError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 401) return true;
  if (error.status !== 403) return false;
  return !isModelDisabledError(error);
}

function historyResponseMessages(response: SspChatHistory): SspChatMessage[] {
  return response.messages.filter(
    (message) => new Date(message.expires_at).getTime() > Date.now(),
  );
}

export function useGlobalChatbot(): ChatbotContextValue | null {
  return useContext(ChatbotContext);
}

export function GlobalChatbotProvider({
  session,
  children,
}: {
  session: SessionInfo;
  children: ReactNode;
}) {
  const actorId = session.actor_id;
  const [scope, setScope] = useState<WorkspaceScope | null>(null);
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [context, setContext] = useState<AgentContext | null>(null);
  const [messages, setMessages] = useState<SspChatMessage[]>([]);
  const [historyState, setHistoryState] = useState<ChatHistoryState>("ready");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextBeforeSequence, setNextBeforeSequence] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [sending, setSending] = useState(false);
  const [, setBridgeVersion] = useState(0);
  const [canRetryMessage, setCanRetryMessage] = useState(false);
  const cacheRef = useRef<Map<string, SspChatMessage[]>>(new Map());
  const cacheRevisionRef = useRef<Map<string, string>>(new Map());
  const latestSequenceRef = useRef<Map<string, number>>(new Map());
  const scopeRef = useRef<WorkspaceScope | null>(null);
  const currentKeyRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const historyControllerRef = useRef<AbortController | null>(null);
  const sendControllerRef = useRef<AbortController | null>(null);
  const actionsRef = useRef<SspWorkspaceActions>({});
  const pendingSendRef = useRef<PendingSend | null>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);

  scopeRef.current = scope;

  const abortInFlight = useCallback(() => {
    historyControllerRef.current?.abort();
    sendControllerRef.current?.abort();
    historyControllerRef.current = null;
    sendControllerRef.current = null;
  }, []);

  const isCurrent = useCallback((key: string, generation: number) => {
    return currentKeyRef.current === key && generationRef.current === generation;
  }, []);

  const applyHistoryResponse = useCallback(
    (key: string, response: SspChatHistory, replace = false) => {
      const updated = replace
        ? historyResponseMessages(response)
        : mergeMessages(cacheRef.current.get(key) ?? [], historyResponseMessages(response));
      cacheRef.current.set(key, updated);
      latestSequenceRef.current.set(key, response.sequence);
      setMessages(updated);
      setHasMore(response.has_more);
      setNextBeforeSequence(response.next_before_sequence);
    },
    [],
  );

  const loadLatest = useCallback(
    async (
      scopeAtRequest: WorkspaceScope,
      key: string,
      generation: number,
      preserveError = false,
      replace = false,
    ) => {
      historyControllerRef.current?.abort();
      setLoadingOlder(false);
      const controller = new AbortController();
      historyControllerRef.current = controller;
      setHistoryState("loading");
      if (!preserveError) setHistoryError(null);
      try {
        const response = await getSspWorkspaceChat(scopeAtRequest.id, {
          limit: 40,
          signal: controller.signal,
        });
        if (
          !isCurrent(key, generation) ||
          controller.signal.aborted ||
          historyControllerRef.current !== controller
        ) {
          return;
        }
        applyHistoryResponse(key, response, replace);
        setHistoryState("ready");
      } catch (error) {
        if (
          !isCurrent(key, generation) ||
          controller.signal.aborted ||
          historyControllerRef.current !== controller ||
          isCancelledRequest(error, controller.signal)
        ) {
          return;
        }
        if (isPermissionDeniedError(error)) {
          cacheRef.current.delete(key);
          latestSequenceRef.current.delete(key);
          setMessages([]);
          setHasMore(false);
          setNextBeforeSequence(null);
          pendingSendRef.current = null;
          setCanRetryMessage(false);
        }
        setHistoryState(historyStateForError(error));
        setHistoryError(formatApiError(error, "Chat history could not be loaded."));
      } finally {
        if (historyControllerRef.current === controller) historyControllerRef.current = null;
      }
    },
    [applyHistoryResponse, isCurrent],
  );

  useEffect(() => {
    cacheRef.current.clear();
    cacheRevisionRef.current.clear();
    latestSequenceRef.current.clear();
    pendingSendRef.current = null;
    setCanRetryMessage(false);
    setMessages([]);
    setHasMore(false);
    setNextBeforeSequence(null);
    setHistoryError(null);
    setOpen(false);
    abortInFlight();
    generationRef.current += 1;
  }, [abortInFlight, actorId]);

  useEffect(() => {
    const nextGeneration = generationRef.current + 1;
    generationRef.current = nextGeneration;
    abortInFlight();
    const currentScope = scopeRef.current;
    const key = currentScope ? scopeKey(actorId, currentScope.id) : null;
    const previousKey = currentKeyRef.current;
    currentKeyRef.current = key;
    if (previousKey !== key) {
      setContext(null);
      setFocus(null);
    }
    setSending(false);
    setLoadingOlder(false);
    setCanRetryMessage(false);
    pendingSendRef.current = null;
    setHistoryError(null);
    const previousRevision = key
      ? cacheRevisionRef.current.get(key)
      : undefined;
    const revisionChanged =
      currentScope !== null &&
      previousRevision !== undefined &&
      previousRevision !== currentScope.revisionId;
    if (key && currentScope) {
      cacheRevisionRef.current.set(key, currentScope.revisionId);
      if (revisionChanged) {
        cacheRef.current.delete(key);
        latestSequenceRef.current.delete(key);
      }
    }
    const cached = key && !revisionChanged
      ? cacheRef.current.get(key) ?? []
      : [];
    if (key) cacheRef.current.set(key, cached);
    setMessages(cached);
    setHasMore(false);
    setNextBeforeSequence(null);
    if (!currentScope || !key) {
      setHistoryState("ready");
      return;
    }
    void loadLatest(currentScope, key, nextGeneration, false, revisionChanged);
  }, [abortInFlight, actorId, loadLatest, scope?.id, scope?.revisionId]);

  useEffect(() => () => abortInFlight(), [abortInFlight]);

  const registerWorkspace = useCallback(
    (workspace: SspWorkspace, actions: SspWorkspaceActions = {}) => {
      actionsRef.current = actions;
      setBridgeVersion((version) => version + 1);
      const signature = `${workspace.id}|${workspace.name}|${workspace.revisionId}|${patchSignature(workspace.patches)}`;
      setScope((current) => {
        if (current?.signature === signature) return current;
        return {
          id: workspace.id,
          name: workspace.name,
          revisionId: workspace.revisionId,
          patches: workspace.patches,
          signature,
        };
      });
    },
    [],
  );

  const openChat = useCallback(
    (options: { context?: AgentContext; focus?: string } = {}) => {
      setContext(options.context ?? null);
      setFocus(options.focus?.slice(0, 500) ?? null);
      setOpen(true);
    },
    [],
  );

  const refreshHistory = useCallback(() => {
    const currentScope = scopeRef.current;
    const key = currentKeyRef.current;
    if (!currentScope || !key) return;
    void loadLatest(currentScope, key, generationRef.current);
  }, [loadLatest]);

  const loadOlder = useCallback(async () => {
    const currentScope = scopeRef.current;
    const key = currentKeyRef.current;
    const beforeSequence = nextBeforeSequence;
    if (!currentScope || !key || beforeSequence === null || loadingOlder) return;
    const generation = generationRef.current;
    const controller = new AbortController();
    historyControllerRef.current?.abort();
    historyControllerRef.current = controller;
    setLoadingOlder(true);
    try {
      const response = await getSspWorkspaceChat(currentScope.id, {
        beforeSequence,
        limit: 40,
        signal: controller.signal,
      });
      if (
        !isCurrent(key, generation) ||
        controller.signal.aborted ||
        historyControllerRef.current !== controller
      ) {
        return;
      }
      applyHistoryResponse(key, response, false);
      latestSequenceRef.current.set(
        key,
        Math.max(latestSequenceRef.current.get(key) ?? 0, response.sequence),
      );
      setHistoryState("ready");
    } catch (error) {
      if (
        !isCurrent(key, generation) ||
        controller.signal.aborted ||
        historyControllerRef.current !== controller ||
        isCancelledRequest(error, controller.signal)
      ) {
        return;
      }
      if (isPermissionDeniedError(error)) {
        cacheRef.current.delete(key);
        latestSequenceRef.current.delete(key);
        setMessages([]);
        setHasMore(false);
        setNextBeforeSequence(null);
      }
      setHistoryState(historyStateForError(error));
      setHistoryError(formatApiError(error, "Older chat history could not be loaded."));
    } finally {
      if (historyControllerRef.current === controller) {
        setLoadingOlder(false);
        historyControllerRef.current = null;
      }
    }
  }, [applyHistoryResponse, isCurrent, loadingOlder, nextBeforeSequence]);

  const submitChat = useCallback(
    async (message: string, retryPayload?: PendingSend) => {
      const currentScope = scopeRef.current;
      const key = currentKeyRef.current;
      if (!currentScope || !key || sending) return;
      const generation = generationRef.current;
      const expectedSequence =
        retryPayload?.expectedSequence ?? latestSequenceRef.current.get(key) ?? 0;
      const requestId = retryPayload?.requestId ?? crypto.randomUUID();
      const pending: PendingSend = retryPayload ?? {
        message,
        requestId,
        key,
        revisionId: currentScope.revisionId,
        expectedSequence,
        focus: focus ?? undefined,
      };
      pendingSendRef.current = pending;
      setSending(true);
      setHistoryError(null);
      setCanRetryMessage(false);
      sendControllerRef.current?.abort();
      const controller = new AbortController();
      sendControllerRef.current = controller;
      try {
        const response = await postSspWorkspaceChat(session, currentScope.id, {
          message: pending.message,
          expectedRevisionId: pending.revisionId,
          requestId,
          expectedSequence: pending.expectedSequence,
          focus: pending.focus,
          signal: controller.signal,
        });
        if (
          !isCurrent(key, generation) ||
          controller.signal.aborted ||
          sendControllerRef.current !== controller
        ) {
          return;
        }
        applyHistoryResponse(key, response, false);
        setHistoryState("ready");
        setHistoryError(null);
        pendingSendRef.current = null;
        setCanRetryMessage(false);
      } catch (error) {
        if (
          !isCurrent(key, generation) ||
          controller.signal.aborted ||
          sendControllerRef.current !== controller ||
          isCancelledRequest(error, controller.signal)
        ) {
          return;
        }
        if (isPermissionDeniedError(error)) {
          cacheRef.current.delete(key);
          latestSequenceRef.current.delete(key);
          setMessages([]);
          setHasMore(false);
          setNextBeforeSequence(null);
        }
        if (error instanceof ApiError && error.status === 409) {
          setHistoryError(
            "This chat changed while you were sending. History was refreshed; review the current sequence before retrying.",
          );
          void loadLatest(currentScope, key, generation, true);
        } else {
          setHistoryState(historyStateForError(error));
          setHistoryError(formatApiError(error, "The private assistant could not respond."));
          setCanRetryMessage(
            !isModelDisabledError(error) && !isPermissionDeniedError(error),
          );
        }
      } finally {
        if (sendControllerRef.current === controller) sendControllerRef.current = null;
        if (isCurrent(key, generation)) setSending(false);
      }
    },
    [applyHistoryResponse, focus, isCurrent, loadLatest, sending, session],
  );

  const retryMessage = useCallback(() => {
    const pending = pendingSendRef.current;
    if (!pending || pending.key !== currentKeyRef.current) return;
    void submitChat(pending.message, pending);
  }, [submitChat]);

  const contextValue = useMemo<ChatbotContextValue>(
    () => ({ identityKey: actorId, registerWorkspace, openChat }),
    [actorId, openChat, registerWorkspace],
  );
  const drawerContext: AgentContext =
    context ??
    (scope
      ? { targetType: "workspace", targetId: scope.id, label: scope.name }
      : { targetType: "workspace", targetId: "none", label: "No workspace selected" });

  return (
    <ChatbotContext.Provider value={contextValue}>
      {children}
      <Button
        ref={launcherRef}
        type="button"
        className="fixed bottom-6 right-6 z-40 shadow-lg"
        onClick={() => openChat()}
        aria-label="Open SSP assistant"
      >
        <Bot aria-hidden="true" />
        SSP assistant
      </Button>
      {open ? (
        <ContextualAgentDrawer
          context={drawerContext}
          editContext={context}
          patches={scope?.patches ?? []}
          restoreFocusRef={launcherRef}
          onClose={() => setOpen(false)}
          onAskAgent={actionsRef.current.onAskAgent}
          onApplyPatch={actionsRef.current.onApplyPatch}
          onRejectPatch={actionsRef.current.onRejectPatch}
          key={scope?.id ?? "no-workspace"}
          messages={messages}
          historyState={historyState}
          historyError={historyError}
          sending={sending}
          onSendMessage={(message) => void submitChat(message)}
          onRetryMessage={canRetryMessage ? retryMessage : undefined}
          onRefreshHistory={refreshHistory}
          onLoadOlder={() => void loadOlder()}
          hasMore={hasMore}
          loadingOlder={loadingOlder}
          selectedWorkspaceLabel={scope?.name}
          selectedWorkspaceId={scope?.id}
          currentRevisionId={scope?.revisionId}
          focus={focus}
          composerDisabled={
            !scope ||
            historyState === "loading" ||
            historyState === "modeldisabled"
          }
        />
      ) : null}
    </ChatbotContext.Provider>
  );
}
