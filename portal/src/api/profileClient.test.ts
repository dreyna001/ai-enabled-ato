import { afterEach, describe, expect, it, vi } from "vitest";
import {
  activateProfile,
  importProfile,
  listProfiles,
} from "@/api/profileClient";
import type { SessionInfo } from "@/types";

const session: SessionInfo = {
  actor_id: "platform-admin@example.gov",
  groups: ["platform-admins"],
  csrf_token: "c".repeat(32),
  portal_origin: "https://portal.example.gov",
};

const profile = {
  profile_version_id: "10000000-0000-4000-8000-000000000001",
  profile_id: "agency-fisma-nist-sp800-53-rev5",
  version: "1.4.0",
  status: "inactive",
  bundle_sha256: "a".repeat(64),
  imported_by: "platform-admin@example.gov",
  imported_at: "2026-09-10T12:00:00+00:00",
  activated_at: null,
  display_name: "Agency FISMA — NIST SP 800-53 Rev. 5",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("profileClient", () => {
  it("validates the complete list response and preserves server fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ can_manage: true, items: [profile] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listProfiles()).resolves.toEqual({
      can_manage: true,
      items: [profile],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/ssp-profiles",
      expect.objectContaining({ credentials: "include", signal: expect.any(AbortSignal) }),
    );
  });

  it("rejects list responses with unrecognized fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        can_manage: true,
        items: [{ ...profile, unexpected: "value" }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(listProfiles()).rejects.toMatchObject({
      kind: "invalid_response",
      status: 502,
    });
  });

  it("posts the backend's bundle multipart field with protected CSRF and origin headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          profile_version_id: profile.profile_version_id,
          status: "inactive",
        },
        201,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["zip bytes"], "profile.zip", { type: "application/zip" });

    await expect(
      importProfile(session, file, {
        headers: {
          "X-CSRF-Token": "caller-supplied-token",
          "X-Request-ID": "request-1",
        },
      }),
    ).resolves.toEqual({
      profile_version_id: profile.profile_version_id,
      status: "inactive",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("include");
    expect(init.headers).toEqual({
      "X-CSRF-Token": session.csrf_token,
      Origin: session.portal_origin,
      "X-Request-ID": "request-1",
    });
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("bundle")).toBe(file);
  });

  it("posts activation to the selected profile version and validates its response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        profile_version_id: profile.profile_version_id,
        status: "active",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      activateProfile(session, profile.profile_version_id),
    ).resolves.toEqual({
      profile_version_id: profile.profile_version_id,
      status: "active",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/ssp-profiles/${profile.profile_version_id}/activate`,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: {
          "X-CSRF-Token": session.csrf_token,
          Origin: session.portal_origin,
        },
      }),
    );
  });
});
