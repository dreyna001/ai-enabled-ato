import { Bot, Save } from "lucide-react";
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
import { cn } from "@/lib/utils";
import type {
  AgentContext,
  SspSection,
  SspSectionChange,
} from "@/sspWorkspaceTypes";

function sectionVariant(state: SspSection["state"]) {
  if (state === "reviewed") return "success" as const;
  if (state === "empty") return "warning" as const;
  if (state === "edited") return "outline" as const;
  return "secondary" as const;
}

export function SspDocumentPanel({
  sections,
  selectedSectionId,
  onSelectSection,
  onSave,
  onOpenAgent,
}: {
  sections: SspSection[];
  selectedSectionId: string | null;
  onSelectSection: (id: string) => void;
  onSave?: (change: SspSectionChange) => void;
  onOpenAgent: (context: AgentContext) => void;
}) {
  const selected =
    sections.find((section) => section.id === selectedSectionId) ??
    sections[0] ??
    null;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const content = selected ? drafts[selected.id] ?? selected.content : "";

  if (!selected) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          No SSP sections are available in the pinned profile.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <Card className="h-fit">
        <CardHeader className="p-4">
          <CardTitle className="text-sm">SSP sections</CardTitle>
          <CardDescription>One editable working document</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 p-2 pt-0">
          {sections.map((section) => (
            <button
              key={section.id}
              type="button"
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-sm px-3 py-2 text-left text-sm hover:bg-muted",
                section.id === selected.id && "bg-muted text-foreground",
              )}
              onClick={() => onSelectSection(section.id)}
            >
              <span className="truncate">{section.title}</span>
              <span className="text-xs text-muted-foreground">
                {section.satisfiedRequirementIds.length}/
                {section.requirementIds.length}
              </span>
            </button>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <CardTitle className="text-base">{selected.title}</CardTitle>
                <Badge variant={sectionVariant(selected.state)}>
                  {selected.state}
                </Badge>
              </div>
              <CardDescription>
                {selected.evidenceLinks.length} linked evidence citations
              </CardDescription>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onOpenAgent({
                  targetType: "ssp_section",
                  targetId: selected.id,
                  label: `SSP · ${selected.title}`,
                })
              }
            >
              <Bot aria-hidden="true" />
              Ask agent to edit
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="sr-only" htmlFor={`ssp-section-${selected.id}`}>
            {selected.title} content
          </label>
          <textarea
            id={`ssp-section-${selected.id}`}
            className="min-h-80 w-full resize-y rounded-sm border bg-background p-4 text-sm leading-6 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={content}
            onChange={(event) =>
              setDrafts((current) => ({
                ...current,
                [selected.id]: event.target.value,
              }))
            }
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              Saving updates the working revision. Approval happens once at review.
            </p>
            <Button
              type="button"
              disabled={!onSave || content === selected.content}
              onClick={() => {
                onSave?.({ sectionId: selected.id, content });
              }}
            >
              <Save aria-hidden="true" />
              Save section
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
