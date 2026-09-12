import { Bot, Check, ExternalLink, RefreshCw, Send, X } from "lucide-react";
import { useRef, useState, type FormEvent, type RefObject } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SspChatMessage, SspChatSource } from "@/api/sspWorkspaceChat";
import type {
  AgentContext,
  AgentPatch,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";

export type ChatHistoryState =
  | "loading"
  | "ready"
  | "error"
  | "offline"
  | "modeldisabled";

function patchMatchesContext(patch: AgentPatch, context: AgentContext): boolean {
  if (context.targetType === "workspace") return true;
  return patch.targetLabels.some(
    (label) =>
      label.includes(context.targetId) || context.label.includes(label),
  );
}

/** Patches with no edit targets are model explanations, not applyable diffs. */
export function isEditableAgentPatch(patch: AgentPatch): boolean {
  return patch.targetLabels.length > 0;
}

function sourceTargetView(source: SspChatSource): string {
  const kind = source.kind.toLowerCase();
  if (kind.includes("control")) return "controls";
  if (kind.includes("information") || kind.includes("data_type")) {
    return "information-types";
  }
  if (kind.includes("system_definition") || kind.includes("boundary")) {
    return "system-definition";
  }
  if (kind.includes("section") || kind.includes("document")) return "ssp";
  if (kind.includes("evidence") || kind.includes("artifact")) return "evidence";
  if (kind.includes("question")) return "questions";
  if (kind.includes("review") || kind.includes("approval")) return "review";
  return "overview";
}

function sourceHref(workspaceId: string, source: SspChatSource): string {
  const params = new URLSearchParams({
    workspace_id: workspaceId,
    view: sourceTargetView(source),
    source_id: source.source_id,
  });
  if (source.target_id) params.set("target_id", source.target_id);
  return `/ssp?${params.toString()}`;
}

function isHistoricalSource(
  source: SspChatSource,
  currentRevisionId?: string,
): boolean {
  return (
    source.revision_id !== null &&
    currentRevisionId !== undefined &&
    source.revision_id !== currentRevisionId
  );
}

function formatMessageTime(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  }).format(timestamp);
}

function ChatSources({
  message,
  workspaceId,
  currentRevisionId,
}: {
  message: SspChatMessage;
  workspaceId?: string;
  currentRevisionId?: string;
}) {
  if (message.sources.length === 0) return null;
  return (
    <div className="mt-3 border-t border-border/70 pt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Sources
      </p>
      <ul className="mt-1 space-y-1">
        {message.sources.map((source) => (
          <li key={`${message.message_id}-${source.source_id}`}>
            {workspaceId ? (
              <a
                className="inline-flex items-center gap-1 text-xs text-link hover:underline"
                href={sourceHref(workspaceId, source)}
              >
                {source.label}
                <ExternalLink className="size-3" aria-hidden="true" />
              </a>
            ) : (
              <span className="text-xs text-muted-foreground">{source.label}</span>
            )}
            {isHistoricalSource(source, currentRevisionId) ? (
              <Badge className="ml-2" variant="secondary">
                Historical reference
              </Badge>
            ) : null}
            <div className="mt-1 space-y-0.5 font-mono text-[10px] text-muted-foreground">
              <p>Source ID: {source.source_id}</p>
              <p>Target: {source.target_id ?? "not specified"}</p>
              <p>Revision: {source.revision_id ?? "not revision-specific"}</p>
              <p>SHA-256: {source.sha256}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChatMessageCard({
  message,
  workspaceId,
  currentRevisionId,
}: {
  message: SspChatMessage;
  workspaceId?: string;
  currentRevisionId?: string;
}) {
  const isStale = message.stale;
  return (
    <article
      className={
        message.role === "user"
          ? "ml-6 rounded-sm border border-link/40 bg-link/10 p-3"
          : "mr-6 rounded-sm border bg-muted/20 p-3"
      }
      data-sequence={message.sequence}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-muted-foreground">
          {message.role === "user" ? "You" : "Assistant · advisory"}
        </p>
        <time
          className="text-[10px] text-muted-foreground"
          dateTime={message.created_at}
        >
          {formatMessageTime(message.created_at)}
        </time>
      </div>
      {isStale ? (
        <Badge className="mt-2" variant="warning">
          Stale — workspace changed
        </Badge>
      ) : null}
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">
        {message.content}
      </p>
      <ChatSources
        message={message}
        workspaceId={workspaceId}
        currentRevisionId={currentRevisionId}
      />
    </article>
  );
}

type ContextualAgentDrawerProps = {
  context: AgentContext;
  patches: AgentPatch[];
  restoreFocusRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onAskAgent?: SspWorkspaceActions["onAskAgent"];
  onApplyPatch?: SspWorkspaceActions["onApplyPatch"];
  onRejectPatch?: SspWorkspaceActions["onRejectPatch"];
  messages?: SspChatMessage[];
  historyState?: ChatHistoryState;
  historyError?: string | null;
  sending?: boolean;
  onSendMessage?: (message: string) => void;
  onRefreshHistory?: () => void;
  onLoadOlder?: () => void;
  hasMore?: boolean;
  loadingOlder?: boolean;
  onRetryMessage?: () => void;
  composerDisabled?: boolean;
  selectedWorkspaceLabel?: string;
  selectedWorkspaceId?: string;
  currentRevisionId?: string;
  focus?: string | null;
  editContext?: AgentContext | null;
};

export function ContextualAgentDrawer({
  context,
  patches,
  restoreFocusRef,
  onClose,
  onAskAgent,
  onApplyPatch,
  onRejectPatch,
  messages = [],
  historyState = "ready",
  historyError = null,
  sending = false,
  onSendMessage,
  onRefreshHistory,
  onLoadOlder,
  hasMore = false,
  loadingOlder = false,
  onRetryMessage,
  composerDisabled = false,
  selectedWorkspaceLabel,
  selectedWorkspaceId,
  currentRevisionId,
  focus,
  editContext,
}: ContextualAgentDrawerProps) {
  const [message, setMessage] = useState("");
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const proposedPatches = patches.filter(
    (patch) =>
      patch.state === "proposed" &&
      patchMatchesContext(patch, context) &&
      isEditableAgentPatch(patch),
  );
  const responsePatches = patches.filter(
    (patch) =>
      patch.state === "proposed" &&
      patchMatchesContext(patch, context) &&
      !isEditableAgentPatch(patch),
  );
  const canSend = Boolean(message.trim()) && !sending && !composerDisabled;
  const hasPrivateChat = Boolean(onSendMessage);
  const hasStaleMessages = messages.some((item) => {
    return item.stale;
  });

  const submitPrivateMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || sending) return;
    if (onSendMessage) {
      onSendMessage(trimmed);
    } else if (onAskAgent) {
      onAskAgent(editContext ?? context, trimmed);
    } else {
      return;
    }
    setMessage("");
  };

  const proposeEdit = () => {
    const trimmed = message.trim();
    if (!trimmed || !onAskAgent || sending) return;
    onAskAgent(editContext ?? context, trimmed);
    setMessage("");
  };

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content
          aria-describedby="contextual-agent-description"
          aria-labelledby="contextual-agent-title"
          className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-xl flex-col border-l bg-background shadow-2xl"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            messageInputRef.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            restoreFocusRef.current?.focus();
          }}
        >
          <header className="flex items-start justify-between gap-3 border-b p-4">
            <div>
              <div className="flex items-center gap-2">
                <Bot className="size-4 text-link" aria-hidden="true" />
                <Dialog.Title id="contextual-agent-title" className="font-semibold">
                  SSP assistant
                </Dialog.Title>
              </div>
              <Dialog.Description
                className="mt-1 text-xs text-muted-foreground"
                id="contextual-agent-description"
              >
                {selectedWorkspaceLabel
                  ? `Workspace: ${selectedWorkspaceLabel}`
                  : `Context: ${context.label}`}
              </Dialog.Description>
              {focus ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Focus: {focus} · whole workspace context remains active
                </p>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              {onRefreshHistory ? (
                <Button
                  aria-label="Refresh chat history"
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={onRefreshHistory}
                  disabled={sending}
                >
                  <RefreshCw aria-hidden="true" />
                </Button>
              ) : null}
              <Dialog.Close asChild>
                <Button type="button" size="icon" variant="ghost" aria-label="Close assistant">
                  <X aria-hidden="true" />
                </Button>
              </Dialog.Close>
            </div>
          </header>

          <div className="portal-scrollbar flex-1 space-y-4 overflow-y-auto p-4">
            <div className="rounded-sm border bg-muted/20 p-3 text-xs leading-5">
              <p className="font-semibold">Private advisory chat</p>
              <p className="mt-1 text-muted-foreground">
                This conversation is private to you and is stored against the selected
                workspace for seven years. It references the shared canonical SSP
                records, but model answers are advisory—verify the linked sources.
              </p>
              {editContext && onAskAgent ? (
                <p className="mt-2 border-t border-border/70 pt-2 text-amber-300">
                  Propose SSP edit is different: it creates a shared system review
                  proposal. It never changes the SSP until an authorized user approves it.
                </p>
              ) : null}
            </div>

            {hasStaleMessages ? (
              <div className="flex items-center justify-between gap-3 rounded-sm border border-amber-400/40 bg-amber-400/10 p-3 text-xs">
                <span>Some answers or sources use an older workspace revision.</span>
                {onRefreshHistory ? (
                  <Button type="button" size="sm" variant="outline" onClick={onRefreshHistory}>
                    Refresh
                  </Button>
                ) : null}
              </div>
            ) : null}

            {hasMore ? (
              <div className="flex justify-center">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={loadingOlder}
                  onClick={onLoadOlder}
                >
                  {loadingOlder ? "Loading older turns…" : "Load older turns"}
                </Button>
              </div>
            ) : null}

            {historyState === "loading" && messages.length === 0 ? (
              <div className="rounded-sm border p-4 text-sm text-muted-foreground" role="status">
                Loading private chat history…
              </div>
            ) : null}
            {historyState === "offline" ? (
              <div className="rounded-sm border border-amber-400/40 bg-amber-400/10 p-4 text-sm" role="alert">
                Chat history is unavailable offline. Reconnect and refresh to continue.
              </div>
            ) : null}
            {historyState === "modeldisabled" ? (
              <div className="rounded-sm border border-amber-400/40 bg-amber-400/10 p-4 text-sm" role="alert">
                The approved SSP model is disabled. Private history remains available,
                but new advisory answers are unavailable.
              </div>
            ) : null}
            {historyState === "error" ? (
              historyError ? null : (
              <div className="rounded-sm border border-destructive/40 p-4 text-sm text-destructive" role="alert">
                Chat history could not be loaded.
              </div>
              )
            ) : null}
            {historyError && historyState !== "loading" ? (
              <div className="rounded-sm border border-destructive/40 p-4 text-sm text-destructive" role="alert">
                <p>{historyError}</p>
                {onRetryMessage ? (
                  <Button className="mt-3" type="button" size="sm" variant="outline" onClick={onRetryMessage}>
                    Retry send
                  </Button>
                ) : null}
              </div>
            ) : null}
            {historyState === "ready" && messages.length === 0 ? (
              <div className="rounded-sm border border-dashed p-6 text-center text-sm text-muted-foreground">
                {selectedWorkspaceId
                  ? "No private messages for this workspace yet. Ask about the canonical SSP records to begin."
                  : "Select an SSP workspace before starting a private chat."}
              </div>
            ) : null}

            {messages.map((item) => (
              <ChatMessageCard
                key={item.message_id}
                message={item}
                workspaceId={selectedWorkspaceId}
                currentRevisionId={currentRevisionId}
              />
            ))}

            {sending ? (
              <div className="mr-6 rounded-sm border border-dashed p-3 text-sm text-muted-foreground" role="status">
                Sending to the private assistant…
              </div>
            ) : null}

            {proposedPatches.length > 0 ? (
              <section aria-labelledby="proposed-patches-title">
                <div className="flex items-center gap-2">
                  <h3
                    id="proposed-patches-title"
                    className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    Shared system review proposals
                  </h3>
                  <Badge variant="warning">Approval required</Badge>
                </div>
                <div className="mt-2 space-y-2">
                  {proposedPatches.map((patch) => (
                    <div key={patch.id} className="rounded-sm border border-amber-400/40 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <Badge variant="warning">Shared proposal</Badge>
                        <span className="font-mono text-xs text-muted-foreground">
                          {patch.id}
                        </span>
                      </div>
                      <p className="mt-2 text-sm">{patch.summary}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Targets: {patch.targetLabels.join(", ")}
                      </p>
                      <p className="mt-2 text-xs text-amber-300">
                        This proposal is not applied. Review and approve it to change the
                        shared SSP record.
                      </p>
                      <div className="mt-3 flex gap-2">
                        <Button
                          type="button"
                          size="sm"
                          disabled={!onApplyPatch}
                          onClick={() => onApplyPatch?.(patch.id)}
                        >
                          <Check aria-hidden="true" />
                          Approve &amp; apply
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={!onRejectPatch}
                          onClick={() => onRejectPatch?.(patch.id)}
                        >
                          Reject proposal
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {responsePatches.length > 0 ? (
              <section aria-labelledby="agent-responses-title">
                <h3
                  id="agent-responses-title"
                  className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  Agent review notes
                </h3>
                <div className="mt-2 space-y-2">
                  {responsePatches.map((patch) => (
                    <div key={patch.id} className="rounded-sm border p-3">
                      <div className="flex items-center justify-between gap-2">
                        <Badge variant="muted">Advisory</Badge>
                        <span className="font-mono text-xs text-muted-foreground">
                          {patch.id}
                        </span>
                      </div>
                      <p className="mt-2 text-sm">{patch.summary}</p>
                      <div className="mt-3">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={!onRejectPatch}
                          onClick={() => onRejectPatch?.(patch.id)}
                        >
                          Dismiss note
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </div>

          <form className="border-t p-4" onSubmit={submitPrivateMessage}>
            <label className="sr-only" htmlFor="contextual-agent-message">
              {hasPrivateChat ? "Chat message" : "Agent instruction"}
            </label>
            <textarea
              id="contextual-agent-message"
              ref={messageInputRef}
              className="min-h-24 w-full resize-y rounded-sm border bg-background p-3 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              maxLength={8000}
              placeholder={
                hasPrivateChat
                  ? "Ask about this workspace or its evidence…"
                  : "Describe a targeted SSP change to propose…"
              }
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              disabled={composerDisabled || sending}
            />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] text-muted-foreground">
                {message.length}/8,000 · 4,000-character default
              </span>
              <div className="flex gap-2">
                {hasPrivateChat ? (
                  <Button type="submit" disabled={!canSend}>
                    <Send aria-hidden="true" />
                    Send message
                  </Button>
                ) : (
                  <Button type="submit" disabled={!canSend || !onAskAgent}>
                    <Send aria-hidden="true" />
                    Send to agent
                  </Button>
                )}
                {hasPrivateChat && editContext && onAskAgent ? (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!canSend}
                    onClick={proposeEdit}
                  >
                    Propose SSP edit
                  </Button>
                ) : null}
              </div>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
