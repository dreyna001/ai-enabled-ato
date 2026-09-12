import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import { ProfilesPage } from "@/pages/ProfilesPage";
import type { Profile, ProfileList } from "@/api/profileClient";
import type { SessionInfo } from "@/types";

const apiMocks = vi.hoisted(() => ({
  activateProfile: vi.fn(),
  importProfile: vi.fn(),
  listProfiles: vi.fn(),
}));

vi.mock("@/api/profileClient", () => apiMocks);

const adminSession: SessionInfo = {
  actor_id: "platform-admin@example.gov",
  groups: ["platform-admins"],
  csrf_token: "a".repeat(32),
  portal_origin: "http://127.0.0.1:5174",
};

const readerSession: SessionInfo = {
  actor_id: "isso@example.gov",
  groups: ["isso"],
  csrf_token: "b".repeat(32),
  portal_origin: "http://127.0.0.1:5174",
};

const inactiveProfile: Profile = {
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

const activeProfile: Profile = {
  profile_version_id: "10000000-0000-4000-8000-000000000002",
  profile_id: "agency-fisma-nist-sp800-53-rev5",
  version: "1.3.0",
  status: "active",
  bundle_sha256: "b".repeat(64),
  imported_by: "system:built-in-profile",
  imported_at: "2026-09-01T12:00:00+00:00",
  activated_at: "2026-09-01T12:01:00+00:00",
  display_name: "Agency FISMA — NIST SP 800-53 Rev. 5",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  apiMocks.listProfiles.mockResolvedValue({
    can_manage: true,
    items: [inactiveProfile, activeProfile],
  });
  apiMocks.importProfile.mockResolvedValue({
    profile_version_id: inactiveProfile.profile_version_id,
    status: "inactive",
  });
  apiMocks.activateProfile.mockResolvedValue({
    profile_version_id: inactiveProfile.profile_version_id,
    status: "active",
  });
});

describe("ProfilesPage", () => {
  it("keeps non-platform administrators read-only", async () => {
    apiMocks.listProfiles.mockResolvedValueOnce({
      can_manage: false,
      items: [inactiveProfile, activeProfile],
    });
    render(<ProfilesPage session={readerSession} />);

    expect(await screen.findByText(/Read-only access\./)).toBeInTheDocument();
    expect(screen.getByText(inactiveProfile.bundle_sha256)).toBeInTheDocument();
    expect(screen.getByText("Latest imported version")).toBeInTheDocument();
    expect(screen.getByText("Globally current (server status)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import profile" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("uses the server capability even when session groups use another configured name", async () => {
    render(<ProfilesPage session={readerSession} />);

    expect(await screen.findByRole("button", { name: "Import profile" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toBeInTheDocument();
  });

  it("shows an accessible loading state and ignores a cancelled identity's response", async () => {
    let resolveFirst!: (profiles: ProfileList) => void;
    apiMocks.listProfiles
      .mockReset()
      .mockImplementationOnce(
        () => new Promise<ProfileList>((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockResolvedValueOnce({ can_manage: false, items: [activeProfile] });

    const { rerender } = render(<ProfilesPage session={adminSession} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading profiles");

    rerender(<ProfilesPage session={readerSession} />);
    expect(await screen.findByText(/Read-only access\./)).toBeInTheDocument();

    await act(async () => {
      resolveFirst({ can_manage: true, items: [inactiveProfile] });
    });
    expect(screen.queryByText(inactiveProfile.profile_version_id)).not.toBeInTheDocument();
  });

  it("requires a selected ZIP file before starting an import", async () => {
    apiMocks.listProfiles.mockResolvedValueOnce({ can_manage: true, items: [] });
    render(<ProfilesPage session={adminSession} />);

    await screen.findByText("No profile versions are available");
    fireEvent.click(screen.getByRole("button", { name: "Import profile" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Select a profile bundle ZIP file before importing.",
    );
    expect(apiMocks.importProfile).not.toHaveBeenCalled();
  });

  it("requires confirmation and shows busy state before activation", async () => {
    let resolveActivation!: (result: { profile_version_id: string; status: "active" }) => void;
    apiMocks.listProfiles
      .mockResolvedValueOnce({ can_manage: true, items: [inactiveProfile] })
      .mockResolvedValueOnce({
        can_manage: true,
        items: [{ ...inactiveProfile, status: "active" }],
      });
    apiMocks.activateProfile.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveActivation = resolve;
        }),
    );

    render(<ProfilesPage session={adminSession} />);
    await screen.findByRole("button", { name: "Activate" });
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(apiMocks.activateProfile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Activate profile" }));

    expect(apiMocks.activateProfile).toHaveBeenCalledWith(
      adminSession,
      inactiveProfile.profile_version_id,
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled();

    await act(async () => {
      resolveActivation({
        profile_version_id: inactiveProfile.profile_version_id,
        status: "active",
      });
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("reports an invalid server-side import without claiming it succeeded", async () => {
    const invalidBundle = new File(["not a profile archive"], "profile.zip", {
      type: "application/zip",
    });
    apiMocks.importProfile.mockRejectedValueOnce(
      new ApiError(400, "Profile bundle archive is invalid.", "http", "profile_bundle_invalid"),
    );
    render(<ProfilesPage session={adminSession} />);
    await screen.findByRole("button", { name: "Import profile" });

    fireEvent.change(screen.getByLabelText("Profile bundle ZIP"), {
      target: { files: [invalidBundle] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import profile" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "400: Profile bundle archive is invalid. (profile_bundle_invalid)",
      );
    });
    expect(apiMocks.listProfiles).toHaveBeenCalledOnce();
  });

  it("reports profile list server errors and allows an explicit retry", async () => {
    apiMocks.listProfiles
      .mockReset()
      .mockRejectedValueOnce(new ApiError(503, "Profile service unavailable."))
      .mockResolvedValueOnce({ can_manage: false, items: [activeProfile] });

    render(<ProfilesPage session={readerSession} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "503: Profile service unavailable.",
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText(activeProfile.profile_version_id)).toBeInTheDocument();
    expect(apiMocks.listProfiles).toHaveBeenCalledTimes(2);
  });
});
