import { Bot } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AgentContext, WorkspaceQuestion } from "@/sspWorkspaceTypes";

export function QuestionsPanel({
  questions,
  onOpenAgent,
}: {
  questions: WorkspaceQuestion[];
  onOpenAgent: (context: AgentContext) => void;
}) {
  const openQuestions = questions.filter((question) => question.state === "open");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Open questions</CardTitle>
        <CardDescription>
          Currently identified gaps. New evidence or edits may identify more.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {openQuestions.length === 0 ? (
          <p className="rounded-sm border border-dashed p-5 text-sm text-muted-foreground">
            No recorded questions are open.
          </p>
        ) : (
          <ul className="space-y-2">
            {openQuestions.map((question) => (
              <li
                key={question.id}
                className="flex flex-wrap items-start justify-between gap-3 rounded-sm border p-4"
              >
                <div className="max-w-3xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="warning">Open</Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                      {question.targetId}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      Owner: {question.owner}
                    </span>
                  </div>
                  <p className="mt-2 text-sm">{question.prompt}</p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    onOpenAgent({
                      targetType: question.targetType,
                      targetId: question.targetId,
                      label: `${question.targetId} · unresolved question`,
                    })
                  }
                >
                  <Bot aria-hidden="true" />
                  Resolve with agent
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
