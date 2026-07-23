import { afterEach, describe, expect, it, vi } from "vitest";
import {
  parseChangeAnalysis,
  parseChatResponse,
  parseDisposition,
  parsePackageRevision,
  parseSearchResults,
  parseSessionInfo,
  parseSystem,
} from "@/api/responseSchemas";

describe("extended response schema parsers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses explicit single-user mode and defaults it off when absent", () => {
    const session = {
      actor_id: "operator@example.test",
      groups: ["owners"],
      csrf_token: "c".repeat(32),
      portal_origin: "http://localhost:5173",
    };

    expect(
      parseSessionInfo({
        ...session,
        single_user_mode_enabled: true,
      })?.single_user_mode_enabled,
    ).toBe(true);
    expect(parseSessionInfo(session)?.single_user_mode_enabled).toBe(false);
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

  it("requires a valid package preparation status", () => {
    const revision = {
      package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      package_preparation_status: "ready_for_external_review",
      revision_version: 1,
      profile_id: "fisma_agency_security",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    };

    expect(parsePackageRevision(revision)?.package_preparation_status).toBe(
      "ready_for_external_review",
    );
    expect(
      parsePackageRevision({
        package_revision_id: revision.package_revision_id,
        system_id: revision.system_id,
        status: revision.status,
        revision_version: revision.revision_version,
        profile_id: null,
        data_origin: null,
        sensitivity: null,
        package_preparation_status: "in_progress",
      })?.profile_id,
    ).toBeNull();
    expect(
      parsePackageRevision({
        package_revision_id: revision.package_revision_id,
        system_id: revision.system_id,
        status: revision.status,
        revision_version: revision.revision_version,
        profile_id: revision.profile_id,
        data_origin: revision.data_origin,
        sensitivity: revision.sensitivity,
      }),
    ).toBeNull();
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

  it("parses archived_at on system payloads", () => {
    const active = parseSystem({
      system_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      display_name: "Active System",
      owner_group: "owners",
      viewer_groups: ["viewers"],
      archived_at: null,
    });
    const archived = parseSystem({
      system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      display_name: "Archived System",
      owner_group: "owners",
      viewer_groups: ["viewers"],
      archived_at: "2026-07-17T12:00:00Z",
    });

    expect(active?.archived_at).toBeNull();
    expect(archived?.archived_at).toBe("2026-07-17T12:00:00Z");
  });
});
