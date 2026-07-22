import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  INVALID_RESPONSE_STATUS,
  archiveSystem,
  buildCreateRevisionBody,
  buildPatchRevisionMetadataBody,
  chatWithPackage,
  createRevision,
  fetchSession,
  getIntakeReport,
  listSystems,
  patchRevisionMetadata,
} from "@/api/client";
import { INVALID_RESPONSE_MESSAGE, parseIntakeReport } from "@/api/responseSchemas";

function jsonResponse(
  body: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

describe("fetchSession response validation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns validated session payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          actor_id: "dev-portal-user",
          groups: ["owners"],
          csrf_token: "a".repeat(32),
          portal_origin: "http://localhost:5173",
        }),
      ),
    );

    await expect(fetchSession()).resolves.toMatchObject({
      actor_id: "dev-portal-user",
    });
  });

  it("returns null for unauthenticated sessions", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({}, 401)));
    await expect(fetchSession()).resolves.toBeNull();
  });

  it("rejects malformed session payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          actor_id: "dev-portal-user",
          groups: "owners",
        }),
      ),
    );

    await expect(fetchSession()).rejects.toEqual(
      new ApiError(
        INVALID_RESPONSE_STATUS,
        INVALID_RESPONSE_MESSAGE,
        "invalid_response",
      ),
    );
  });
});

describe("chatWithPackage request body", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes required run_id in the chat payload", async () => {
    const runId = "55555555-5555-4555-8555-555555555555";
    const fetchMock = vi.fn(async (_input, init?: RequestInit) => {
      expect(init?.body).toBe(
        JSON.stringify({
          question: "What controls apply?",
          run_id: runId,
          review_revision_id: null,
        }),
      );
      return jsonResponse({
        answer: "Example answer",
        citations: [],
        refused: false,
        refusal_code: null,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await chatWithPackage(
      {
        actor_id: "dev-portal-user",
        groups: ["owners"],
        csrf_token: "a".repeat(32),
        portal_origin: "http://localhost:5173",
      },
      "11111111-1111-4111-8111-111111111111",
      "What controls apply?",
      { runId },
    );

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("listSystems request cancellation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("aborts in-flight listSystems requests when the signal is cancelled", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((_input, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
      ),
    );

    const pending = listSystems({ signal: controller.signal });
    controller.abort();

    await expect(pending).rejects.toEqual(
      new ApiError(0, "Request cancelled.", "cancelled"),
    );
  });
});

describe("listSystems archived query", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests archived systems only when includeArchived is true", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/systems?include_archived=true");
      return jsonResponse({ items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    await listSystems({ includeArchived: true });

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("preserves the default systems list URL for existing callers", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/systems");
      return jsonResponse({ items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    await listSystems();

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("createRevision request body", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds a create payload with required metadata fields", () => {
    expect(
      buildCreateRevisionBody({
        profile_id: "fisma_agency_security",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
        impact_level: "moderate",
        certification_class: null,
      }),
    ).toEqual({
      parent_revision_id: null,
      profile_id: "fisma_agency_security",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
      impact_level: "moderate",
      certification_class: null,
    });
    const parentId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    expect(
      buildCreateRevisionBody({
        parent_revision_id: parentId,
        profile_id: "fedramp_20x_program",
        data_origin: "customer_production",
        sensitivity: "cui",
        certification_class: "B",
        impact_level: null,
      }),
    ).toEqual({
      parent_revision_id: parentId,
      profile_id: "fedramp_20x_program",
      data_origin: "customer_production",
      sensitivity: "cui",
      certification_class: "B",
      impact_level: null,
    });
  });

  it("posts create payload with metadata fields", async () => {
    const systemId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.body).toBe(
        JSON.stringify({
          parent_revision_id: null,
          profile_id: "fisma_agency_security",
          data_origin: "synthetic",
          sensitivity: "internal_unclassified",
          impact_level: "moderate",
          certification_class: null,
        }),
      );
      return jsonResponse({
        package_revision_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        system_id: systemId,
        status: "uploading",
        package_preparation_status: "in_progress",
        revision_version: 1,
        profile_id: "fisma_agency_security",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
        impact_level: "moderate",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await createRevision(
      {
        actor_id: "dev-portal-user",
        groups: ["owners"],
        csrf_token: "c".repeat(32),
        portal_origin: "http://localhost:5173",
      },
      systemId,
      {
        profile_id: "fisma_agency_security",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
        impact_level: "moderate",
        certification_class: null,
      },
    );

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("archiveSystem mutation headers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts to the archive route with CSRF, origin, and idempotency headers", async () => {
    const systemId = "11111111-1111-4111-8111-111111111111";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/systems/${systemId}/archive`);
      expect(init?.method).toBe("POST");
      expect(init?.headers).toMatchObject({
        "X-CSRF-Token": "b".repeat(32),
        Origin: "http://localhost:5173",
      });
      expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
      return jsonResponse({
        system_id: systemId,
        display_name: "Example System",
        owner_group: "owners",
        viewer_groups: ["viewers"],
        archived_at: "2026-07-17T12:00:00Z",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await archiveSystem(
      {
        actor_id: "dev-portal-user",
        groups: ["owners"],
        csrf_token: "b".repeat(32),
        portal_origin: "http://localhost:5173",
      },
      systemId,
    );

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("patchRevisionMetadata request body", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds a minimal changed-field payload", () => {
    expect(
      buildPatchRevisionMetadataBody({
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
      }),
    ).toEqual({
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });
  });

  it("patches metadata with If-Match, CSRF, and idempotency headers", async () => {
    const revisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(_input)).toBe(`/api/v1/package-revisions/${revisionId}`);
      expect(init?.method).toBe("PATCH");
      expect(init?.body).toBe(
        JSON.stringify({
          data_origin: "synthetic",
          sensitivity: "internal_unclassified",
        }),
      );
      expect(init?.headers).toMatchObject({
        "Content-Type": "application/json",
        "If-Match": '"v2"',
        "X-CSRF-Token": "d".repeat(32),
        Origin: "http://localhost:5173",
      });
      expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
      return jsonResponse(
        {
          package_revision_id: revisionId,
          system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          status: "awaiting_confirmation",
          package_preparation_status: "in_progress",
          revision_version: 3,
          profile_id: "fisma_agency_security",
          data_origin: "synthetic",
          sensitivity: "internal_unclassified",
          impact_level: "moderate",
        },
        200,
        { ETag: '"v3"' },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await patchRevisionMetadata(
      {
        actor_id: "dev-portal-user",
        groups: ["owners"],
        csrf_token: "d".repeat(32),
        portal_origin: "http://localhost:5173",
      },
      revisionId,
      '"v2"',
      {
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
      },
    );

    expect(result.etag).toBe('"v3"');
    expect(result.revision.revision_version).toBe(3);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("getIntakeReport response validation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses intake report payloads", async () => {
    const revisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          schema_version: "2.0.0",
          object_type: "intake_report",
          package_revision_id: revisionId,
          revision_version: 2,
          status: "scanning",
          intake_stage: "extract",
          files: [],
          human_attestation: {
            data_origin: "present",
            sensitivity: "present",
          },
          suggested_metadata: {
            profile_id: null,
            certification_class: null,
            impact_level: null,
          },
          suggestion_sources: [],
          gaps: [],
          conflicts: [],
          omitted_chunks: [],
          context_complete: false,
          map_steps: [],
          confirmation: {
            allowed: false,
            blockers: ["revision_not_awaiting_confirmation"],
          },
          generated_at: "2026-07-17T12:00:00Z",
        }),
      ),
    );

    await expect(getIntakeReport(revisionId)).resolves.toMatchObject({
      object_type: "intake_report",
      confirmation: { allowed: false },
    });
    expect(
      parseIntakeReport({
        object_type: "wrong",
      }),
    ).toBeNull();
  });
});
