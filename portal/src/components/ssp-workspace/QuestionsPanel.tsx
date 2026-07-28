import { Save } from "lucide-react";
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
import type {
  QuestionAnswer,
  SspSection,
  WorkspaceQuestion,
} from "@/sspWorkspaceTypes";

export function QuestionsPanel({
  questions,
  sections,
  onAnswer,
}: {
  questions: WorkspaceQuestion[];
  sections: SspSection[];
  onAnswer?: (change: QuestionAnswer) => void;
}) {
  const openQuestions = questions.filter((question) => question.state === "open");
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Open questions</CardTitle>
        <CardDescription>
          Enter confirmed information directly. No agent call is required.
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
                className="space-y-3 rounded-sm border p-4"
              >
                <div>
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
                <label className="block text-sm">
                  <span className="sr-only">
                    Answer {question.prompt}
                  </span>
                  <textarea
                    aria-label={`Answer ${question.prompt}`}
                    className="min-h-20 w-full resize-y rounded-sm border bg-background px-3 py-2"
                    value={
                      drafts[question.id] ??
                      sections.find(
                        (section) =>
                          question.targetType === "ssp_section" &&
                          section.id === question.targetId,
                      )?.content ??
                      ""
                    }
                    onChange={(event) =>
                      setDrafts((current) => ({
                        ...current,
                        [question.id]: event.target.value,
                      }))
                    }
                  />
                </label>
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    disabled={
                      !onAnswer ||
                      !(
                        drafts[question.id] ??
                        sections.find(
                          (section) =>
                            question.targetType === "ssp_section" &&
                            section.id === question.targetId,
                        )?.content ??
                        ""
                      ).trim()
                    }
                    onClick={() => {
                      const answer =
                        drafts[question.id] ??
                        sections.find(
                          (section) =>
                            question.targetType === "ssp_section" &&
                            section.id === question.targetId,
                        )?.content ??
                        "";
                      onAnswer?.({ questionId: question.id, answer });
                    }}
                  >
                    <Save aria-hidden="true" />
                    Save answer
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
