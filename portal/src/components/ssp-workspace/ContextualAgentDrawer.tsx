import { Bot, Check, Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  AgentContext,
  AgentPatch,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";

function patchMatchesContext(patch: AgentPatch, context: AgentContext): boolean {
  if (context.targetType === "workspace") {
    return true;
  }
  return patch.targetLabels.some(
    (label) =>
      label.includes(context.targetId) || context.label.includes(label),
  );
}

/** Patches with no edit targets are model explanations, not applyable diffs. */
export function isEditableAgentPatch(patch: AgentPatch): boolean {
  return patch.targetLabels.length > 0;
}

export function ContextualAgentDrawer({
  context,
  patches,
  onClose,
  onAskAgent,
  onApplyPatch,
  onRejectPatch,
}: {
  context: AgentContext;
  patches: AgentPatch[];
  onClose: () => void;
  onAskAgent?: SspWorkspaceActions["onAskAgent"];
  onApplyPatch?: SspWorkspaceActions["onApplyPatch"];
  onRejectPatch?: SspWorkspaceActions["onRejectPatch"];
}) {
  const [message, setMessage] = useState("");
  const [exchange, setExchange] = useState<{
    question: string;
    answer: string | null;
  } | null>(null);
  const pendingQuestionRef = useRef<string | null>(null);
  const patchIdsBeforeAskRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const pending = pendingQuestionRef.current;
    if (!pending || exchange?.answer !== null) {
      return;
    }
    const newPatch = patches.find(
      (patch) =>
        patch.state === "proposed" &&
        patchMatchesContext(patch, context) &&
        !patchIdsBeforeAskRef.current.has(patch.id),
    );
    if (newPatch) {
      setExchange({ question: pending, answer: newPatch.summary });
      pendingQuestionRef.current = null;
    }
  }, [patches, context, exchange?.answer]);

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

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="ml-auto flex h-full w-full max-w-xl flex-col border-l bg-background shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="contextual-agent-title"
      >
        <header className="flex items-start justify-between gap-3 border-b p-4">
          <div>
            <div className="flex items-center gap-2">
              <Bot className="size-4 text-link" aria-hidden="true" />
              <h2 id="contextual-agent-title" className="font-semibold">
                Ask agent
              </h2>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Context: {context.label}
            </p>
          </div>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label="Close agent"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        </header>

        <div className="portal-scrollbar flex-1 space-y-4 overflow-y-auto p-4">
          <div className="rounded-sm border bg-muted/20 p-3 text-sm">
            <p className="font-medium">Targeted editing</p>
            <p className="mt-1 text-muted-foreground">
              Request a content change to the SSP or controls. Readiness and
              export steps are on Review and Export, not in this chat.
            </p>
          </div>

          {exchange ? (
            <section aria-labelledby="agent-exchange-title" className="space-y-2">
              <h3
                id="agent-exchange-title"
                className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                This turn
              </h3>
              <div className="rounded-sm border p-3">
                <p className="text-xs font-medium text-muted-foreground">You</p>
                <p className="mt-1 text-sm">{exchange.question}</p>
              </div>
              {exchange.answer === null ? (
                <p className="text-sm text-muted-foreground">Waiting for agent…</p>
              ) : (
                <div className="rounded-sm border bg-muted/20 p-3">
                  <p className="text-xs font-medium text-muted-foreground">Agent</p>
                  <p className="mt-1 text-sm">{exchange.answer}</p>
                </div>
              )}
            </section>
          ) : null}

          {responsePatches.length > 0 ? (
            <section aria-labelledby="agent-responses-title">
              <h3
                id="agent-responses-title"
                className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Agent responses
              </h3>
              <div className="mt-2 space-y-2">
                {responsePatches.map((patch) => (
                  <div key={patch.id} className="rounded-sm border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="muted">Response</Badge>
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
                        Dismiss
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {proposedPatches.length > 0 ? (
            <section aria-labelledby="proposed-patches-title">
              <h3
                id="proposed-patches-title"
                className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Proposed changes
              </h3>
              <div className="mt-2 space-y-2">
                {proposedPatches.map((patch) => (
                  <div key={patch.id} className="rounded-sm border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="warning">Proposed</Badge>
                      <span className="font-mono text-xs text-muted-foreground">
                        {patch.id}
                      </span>
                    </div>
                    <p className="mt-2 text-sm">{patch.summary}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Targets: {patch.targetLabels.join(", ")}
                    </p>
                    <div className="mt-3 flex gap-2">
                      <Button
                        type="button"
                        size="sm"
                        disabled={!onApplyPatch}
                        onClick={() => onApplyPatch?.(patch.id)}
                      >
                        <Check aria-hidden="true" />
                        Apply
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!onRejectPatch}
                        onClick={() => onRejectPatch?.(patch.id)}
                      >
                        Reject
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : proposedPatches.length === 0 &&
            responsePatches.length === 0 &&
            !exchange ? (
            <p className="text-sm text-muted-foreground">
              No agent changes are awaiting review.
            </p>
          ) : null}
        </div>

        <form
          className="border-t p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = message.trim();
            if (!trimmed || !onAskAgent) return;
            patchIdsBeforeAskRef.current = new Set(patches.map((patch) => patch.id));
            pendingQuestionRef.current = trimmed;
            setExchange({ question: trimmed, answer: null });
            onAskAgent(context, trimmed);
            setMessage("");
          }}
        >
          <label className="sr-only" htmlFor="contextual-agent-message">
            Agent instruction
          </label>
          <textarea
            id="contextual-agent-message"
            className="min-h-24 w-full resize-y rounded-sm border bg-background p-3 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="Answer a question or request a targeted change…"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
          <div className="mt-2 flex justify-end">
            <Button
              type="submit"
              disabled={!onAskAgent || !message.trim()}
            >
              <Send aria-hidden="true" />
              Send to agent
            </Button>
          </div>
        </form>
      </aside>
    </div>
  );
}
