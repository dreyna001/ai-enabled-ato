import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import {
  getSspWorkspaceChat,
  postSspWorkspaceChat,
} from "@/api/sspWorkspaceChat";
import type { SessionInfo } from "@/types";

const workspaceId = "10000000-0000-4000-8000-000000000001";
const revisionId = "10000000-0000-4000-8000-000000000002";
const requestId = "10000000-0000-4000-8000-000000000003";
const hash = "a".repeat(64);
const session: SessionInfo = {
  actor_id: "isso@example.gov",
  groups: ["isso"],
  csrf_token: "csrf-token",
  portal_origin: "https://portal.example.gov",
};

function history(overrides: Record<string, unknown> = {}) {
  return {
    workspace_id: workspaceId,
    sequence: 2,
    messages: [
      {
        message_id: requestId,
        sequence: 1,
        role: "user",
        content: "What is the authorization boundary?",
        created_at: "2026-09-11T12:00:00Z",
        expires_at: "2033-09-11T12:00:00Z",
        sources: [],
        context_fingerprint: hash,
        stale: false,
      },
      {
        message_id: revisionId,
        sequence: 2,
        role: "assistant",
        content: "The boundary is recorded in the SSP.",
        created_at: "2026-09-11T12:00:01Z",
        expires_at: "2033-09-11T12:00:01Z",
        sources: [
          {
            source_id: "ssp-section-1",
            label: "Authorization boundary",
            target_id: "system.authorization_boundary",
            revision_id: revisionId,
            sha256: hash,
            kind: "ssp_section",
          },
        ],
        context_fingerprint: hash,
        stale: false,
      },
    ],
    has_more: false,
    next_before_sequence: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SSP workspace chat API", () => {
  it("loads a validated latest history page with the opaque turn cursor", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(
        `/api/v1/ssp-workspaces/${workspaceId}/chat?limit=40`,
      );
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return new Response(JSON.stringify(history()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getSspWorkspaceChat(workspaceId)).resolves.toMatchObject({
      workspace_id: workspaceId,
      sequence: 2,
      messages: expect.arrayContaining([
        expect.objectContaining({ role: "assistant", context_fingerprint: hash }),
      ]),
    });
  });

  it("passes an older turn cursor and caps the requested page size", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        `/api/v1/ssp-workspaces/${workspaceId}/chat?before_sequence=9&limit=100`,
      );
      return new Response(
        JSON.stringify(
          history({
            sequence: 9,
            has_more: true,
            next_before_sequence: 1,
          }),
        ),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getSspWorkspaceChat(workspaceId, { beforeSequence: 9, limit: 500 }),
    ).resolves.toMatchObject({ has_more: true, next_before_sequence: 1 });
  });

  it("posts the exact server contract with CSRF and the long model timeout", async () => {
    const focus = "Control AC-2";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/ssp-workspaces/${workspaceId}/chat`);
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      expect(init?.headers).toMatchObject({
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf-token",
        Origin: "https://portal.example.gov",
      });
      expect(init?.body).toBe(
        JSON.stringify({
          message: "Explain AC-2.",
          expected_revision_id: revisionId,
          request_id: requestId,
          expected_sequence: 2,
          focus,
        }),
      );
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      return new Response(JSON.stringify(history()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await postSspWorkspaceChat(session, workspaceId, {
      message: "Explain AC-2.",
      expectedRevisionId: revisionId,
      requestId,
      expectedSequence: 2,
      focus,
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("rejects malformed UUID and hash response fields at the client boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify(
            history({
              messages: [
                {
                  ...history().messages[1],
                  context_fingerprint: "not-a-sha256",
                },
              ],
            }),
          ),
          { status: 200 },
        ),
      ),
    );

    await expect(getSspWorkspaceChat(workspaceId)).rejects.toEqual(
      new ApiError(
        502,
        "Portal API returned an unexpected response.",
        "invalid_response",
      ),
    );
    await expect(getSspWorkspaceChat("workspace-not-a-uuid")).rejects.toThrow();
  });
});
