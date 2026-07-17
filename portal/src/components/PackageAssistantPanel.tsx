import { useState } from "react";
import { chatWithPackage, isCancelledRequest, searchPackage } from "@/api/client";
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
import type { ChatResponse, SearchHit, SessionInfo } from "@/types";
import { formatApiError } from "@/utils/formatApiError";
import { problemMessageForCode } from "@/utils/problemMessages";

type PackageAssistantPanelProps = {
  session: SessionInfo;
  revisionId: string;
  runId?: string | null;
  enabled: boolean;
  readinessWarning?: string | null;
  reviewRevisionId?: string | null;
};

export function PackageAssistantPanel({
  session,
  revisionId,
  runId,
  enabled,
  readinessWarning = null,
  reviewRevisionId,
}: PackageAssistantPanelProps) {
  const [query, setQuery] = useState("");
  const [question, setQuestion] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [chatResult, setChatResult] = useState<ChatResponse | null>(null);
  const [searchError, setSearchError] = useState("");
  const [chatError, setChatError] = useState("");
  const [busy, setBusy] = useState(false);

  const runSearch = async () => {
    if (!query.trim()) {
      return;
    }
    setBusy(true);
    setSearchError("");
    try {
      const result = await searchPackage(revisionId, query.trim());
      setSearchHits(result.items);
    } catch (err) {
      setSearchHits([]);
      setSearchError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const runChat = async () => {
    if (!question.trim() || !runId) {
      return;
    }
    setBusy(true);
    setChatError("");
    try {
      const result = await chatWithPackage(session, revisionId, question.trim(), {
        runId,
        reviewRevisionId,
      });
      setChatResult(result);
    } catch (err) {
      if (isCancelledRequest(err)) {
        return;
      }
      setChatResult(null);
      setChatError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  if (!enabled) {
    return (
      <Card className="opacity-80">
        <CardHeader>
          <CardTitle className="text-base">Package Assistant</CardTitle>
          <CardDescription>
            Search and chat are unavailable while the API is unreachable or the revision is not
            ready.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Package Assistant</CardTitle>
        <CardDescription>
          Revision-scoped search and bounded Q&amp;A with citation-backed answers.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {readinessWarning ? (
          <p className="rounded-sm border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-foreground">
            {readinessWarning}
          </p>
        ) : null}
        <div className="space-y-2">
          <Label htmlFor="package-search">Search package content</Label>
          <div className="flex flex-wrap gap-2">
            <Input
              id="package-search"
              value={query}
              disabled={busy}
              placeholder="Search controls, evidence, artifacts…"
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button type="button" size="sm" disabled={busy || !query.trim()} onClick={() => void runSearch()}>
              Search
            </Button>
          </div>
          {searchError ? (
            <p className="text-sm text-destructive">{searchError}</p>
          ) : null}
          {searchHits.length === 0 && query && !searchError && !busy ? (
            <p className="text-sm text-muted-foreground">No matches in this revision.</p>
          ) : null}
          {searchHits.length > 0 ? (
            <ul className="space-y-2">
              {searchHits.map((hit) => (
                <li key={`${hit.reference_id ?? hit.artifact_id}-${hit.sha256}`} className="rounded-sm border border-border p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="muted">score {hit.score.toFixed(2)}</Badge>
                    <span className="font-mono text-xs">{hit.reference_id ?? hit.artifact_id}</span>
                  </div>
                  <p className="mt-1 text-muted-foreground">{hit.excerpt}</p>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <div className="space-y-2 border-t pt-4">
          <Label htmlFor="package-question">Ask about this package</Label>
          {!runId ? (
            <p className="text-sm text-muted-foreground">
              Select a succeeded analysis run before asking citation-backed questions.
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Input
              id="package-question"
              value={question}
              disabled={busy || !runId}
              placeholder="What evidence supports AC-2?"
              onChange={(event) => setQuestion(event.target.value)}
            />
            <Button
              type="button"
              size="sm"
              disabled={busy || !runId || !question.trim()}
              onClick={() => void runChat()}
            >
              Ask
            </Button>
          </div>
          {chatError ? <p className="text-sm text-destructive">{chatError}</p> : null}
          {chatResult ? (
            <div className="rounded-sm border border-border bg-muted/20 p-3 text-sm">
              {chatResult.refused ? (
                <p className="text-amber-400">
                  {problemMessageForCode(
                    chatResult.refusal_code ?? undefined,
                    "This question cannot be answered from package content.",
                  )}
                </p>
              ) : (
                <p>{chatResult.answer}</p>
              )}
              {chatResult.citations.length > 0 ? (
                <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">
                  {chatResult.citations.map((citation, index) => (
                    <li key={`${citation.artifact_id ?? index}-${citation.sha256 ?? index}`}>
                      {(citation.excerpt as string | undefined) ?? citation.source_kind ?? "citation"}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
