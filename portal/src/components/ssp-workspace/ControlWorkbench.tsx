import { Bot, Save, Search } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type {
  AgentContext,
  ControlResponseOptions,
  ControlStatement,
  ControlStatementChange,
} from "@/sspWorkspaceTypes";

type ControlDraft = Pick<
  ControlStatementChange,
  "implementationStatus" | "responsibility" | "statement"
>;

function formatEnumLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function selectOptions(options: string[], currentValue: string): string[] {
  if (!currentValue || options.includes(currentValue)) return options;
  return [...options, currentValue];
}

function stateVariant(state: ControlStatement["state"]) {
  if (state === "reviewed") return "success" as const;
  if (state === "partial" || state === "empty") return "warning" as const;
  return "secondary" as const;
}

export function ControlWorkbench({
  controls,
  controlResponse,
  selectedControlId,
  onSelectControl,
  onSave,
  onOpenAgent,
}: {
  controls: ControlStatement[];
  controlResponse: ControlResponseOptions;
  selectedControlId: string | null;
  onSelectControl: (id: string) => void;
  onSave?: (change: ControlStatementChange) => void;
  onOpenAgent: (context: AgentContext) => void;
}) {
  const [query, setQuery] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, ControlDraft>>({});
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = controls.filter((control) => {
    const matchesQuery =
      !normalizedQuery ||
      control.id.toLowerCase().includes(normalizedQuery) ||
      control.title.toLowerCase().includes(normalizedQuery);
    const matchesAttention =
      !attentionOnly ||
      control.state === "partial" ||
      control.state === "empty" ||
      Boolean(control.unresolvedReason);
    return matchesQuery && matchesAttention;
  });
  const selected =
    controls.find((control) => control.id === selectedControlId) ??
    filtered[0] ??
    controls[0] ??
    null;

  if (!selected) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          No controls are available in the resolved profile.
        </CardContent>
      </Card>
    );
  }

  const draft = drafts[selected.id] ?? {
    implementationStatus: selected.implementationStatus,
    responsibility: selected.responsibility,
    statement: selected.statement,
  };
  const changed =
    draft.implementationStatus !== selected.implementationStatus ||
    draft.responsibility !== selected.responsibility ||
    draft.statement !== selected.statement;

  const updateDraft = (change: Partial<ControlDraft>) => {
    setDrafts((current) => ({
      ...current,
      [selected.id]: { ...draft, ...change },
    }));
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[19rem_minmax(0,1fr)]">
      <Card className="h-fit">
        <CardHeader className="p-4">
          <CardTitle className="text-sm">Selected controls</CardTitle>
          <CardDescription>
            Needs-attention controls are shown first.
          </CardDescription>
          <div className="relative pt-2">
            <Search
              className="absolute left-2.5 top-4.5 size-4 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              className="pl-8"
              aria-label="Search controls"
              placeholder="Search ID or title"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 pt-1 text-xs">
            <input
              type="checkbox"
              checked={attentionOnly}
              onChange={(event) => setAttentionOnly(event.target.checked)}
            />
            Needs attention only
          </label>
        </CardHeader>
        <CardContent className="max-h-[38rem] space-y-1 overflow-y-auto p-2 pt-0 portal-scrollbar">
          {filtered.length === 0 ? (
            <p className="p-3 text-xs text-muted-foreground">
              No controls match this filter.
            </p>
          ) : (
            filtered.map((control) => (
              <button
                key={control.id}
                type="button"
                className={cn(
                  "w-full rounded-sm px-3 py-2 text-left hover:bg-muted",
                  selected.id === control.id && "bg-muted",
                )}
                onClick={() => onSelectControl(control.id)}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-link">{control.id}</span>
                  <Badge variant={stateVariant(control.state)}>
                    {control.state}
                  </Badge>
                </span>
                <span className="mt-1 block truncate text-sm">{control.title}</span>
              </button>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <CardTitle className="font-mono text-base">
                  {selected.id}
                </CardTitle>
                <Badge variant={stateVariant(selected.state)}>
                  {selected.state}
                </Badge>
              </div>
              <CardDescription>{selected.title}</CardDescription>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onOpenAgent({
                  targetType: "control",
                  targetId: selected.id,
                  label: `Control ${selected.id} · ${selected.title}`,
                })
              }
            >
              <Bot aria-hidden="true" />
              Ask agent
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`status-${selected.id}`}>Implementation status</Label>
              <select
                id={`status-${selected.id}`}
                aria-label="Implementation status"
                className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                value={draft.implementationStatus}
                onChange={(event) =>
                  updateDraft({ implementationStatus: event.target.value })
                }
              >
                {selectOptions(
                  controlResponse.implementationStatuses,
                  draft.implementationStatus,
                ).map((value) => (
                  <option key={value} value={value}>
                    {formatEnumLabel(value)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`responsibility-${selected.id}`}>
                Responsibility / inheritance
              </Label>
              <select
                id={`responsibility-${selected.id}`}
                aria-label="Responsibility and inheritance"
                className="w-full rounded-sm border bg-background px-3 py-2 text-sm"
                value={draft.responsibility}
                onChange={(event) =>
                  updateDraft({ responsibility: event.target.value })
                }
              >
                {selectOptions(
                  controlResponse.responsibilities,
                  draft.responsibility,
                ).map((value) => (
                  <option key={value} value={value}>
                    {formatEnumLabel(value)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`statement-${selected.id}`}>
              Implementation statement
            </Label>
            <textarea
              id={`statement-${selected.id}`}
              className="min-h-56 w-full resize-y rounded-sm border bg-background p-3 text-sm leading-6 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={draft.statement}
              onChange={(event) => updateDraft({ statement: event.target.value })}
            />
          </div>
          {selected.unresolvedReason ? (
            <div className="rounded-sm border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
              <p className="font-medium text-amber-400">Unresolved information</p>
              <p className="mt-1">{selected.unresolvedReason}</p>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {selected.evidenceLinks.length} supporting evidence links
            </p>
            <Button
              type="button"
              disabled={!onSave || !changed}
              onClick={() => {
                onSave?.({ controlId: selected.id, ...draft });
              }}
            >
              <Save aria-hidden="true" />
              Save control
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
