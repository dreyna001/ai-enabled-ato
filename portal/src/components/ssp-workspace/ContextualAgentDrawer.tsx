import { Bot, Check, Send, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  AgentContext,
  AgentPatch,
  SspWorkspaceActions,
} from "@/sspWorkspaceTypes";

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
  const proposedPatches = patches.filter(
    (patch) =>
      patch.state === "proposed" &&
      (context.targetType === "workspace" ||
        patch.targetLabels.some(
          (label) =>
            label.includes(context.targetId) ||
            context.label.includes(label),
        )),
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
              Answer a question, provide a fact, or request a change. The agent
              proposes a bounded patch to the current target for review.
            </p>
          </div>

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
          ) : (
            <p className="text-sm text-muted-foreground">
              No agent changes are awaiting review.
            </p>
          )}
        </div>

        <form
          className="border-t p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = message.trim();
            if (!trimmed || !onAskAgent) return;
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
