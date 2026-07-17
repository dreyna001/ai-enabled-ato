import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  INVALID_RESPONSE_STATUS,
  chatWithPackage,
  fetchSession,
  listSystems,
} from "@/api/client";
import { INVALID_RESPONSE_MESSAGE } from "@/api/responseSchemas";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
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
