import { afterEach, describe, expect, it, vi } from "vitest";
import {
  parseChangeAnalysis,
  parseChatResponse,
  parseDisposition,
  parseSearchResults,
} from "@/api/responseSchemas";

describe("extended response schema parsers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses change analysis payloads", () => {
    const parsed = parseChangeAnalysis({
      delta: {
        parent_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        child_revision_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        changed_artifact_ids: [],
        added_artifact_ids: ["art-1"],
        removed_artifact_ids: [],
        changed_control_ids: ["AC-1"],
        changed_evidence_keys: [],
        content_digest_changed: true,
        generated_at: "2026-07-14T12:00:00Z",
      },
      targeted_assessment_item_ids: ["AC-1"],
      requires_targeted_reanalysis: true,
    });
    expect(parsed?.targeted_assessment_item_ids).toEqual(["AC-1"]);
  });

  it("parses search results from backend shape", () => {
    const parsed = parseSearchResults({
      items: [
        {
          reference_id: "/security_controls/AC-1",
          sha256: "a".repeat(64),
          excerpt: "policy excerpt",
          score: 1.5,
        },
      ],
      query: "policy",
    });
    expect(parsed?.items).toHaveLength(1);
  });

  it("parses chat responses with refusal codes", () => {
    const parsed = parseChatResponse({
      answer: "Cannot approve packages.",
      citations: [],
      refused: true,
      refusal_code: "authorization_decision",
    });
    expect(parsed?.refused).toBe(true);
  });

  it("parses disposition routing ids", () => {
    const parsed = parseDisposition({
      matrix_row_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      decision: "evidence_requested",
      edited_summary: null,
      notes: null,
      version: 2,
      decided_by: "reviewer",
      decided_at: "2026-07-14T12:00:00Z",
      evidence_request_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    });
    expect(parsed?.evidence_request_id).toBe("dddddddd-dddd-4ddd-8ddd-dddddddddddd");
  });
});
