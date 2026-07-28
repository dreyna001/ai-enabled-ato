import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SspWorkspaceRoute } from "@/pages/SspWorkspaceRoute";
import type { SessionInfo } from "@/types";

const apiMocks = vi.hoisted(() => ({
  listSspProfiles: vi.fn(),
  listSspWorkspaces: vi.fn(),
}));

vi.mock("@/api/sspWorkspace", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/sspWorkspace")>()),
  ...apiMocks,
}));

const session: SessionInfo = {
  actor_id: "isso@example.gov",
  groups: ["isso"],
  csrf_token: "csrf-token",
  portal_origin: "http://127.0.0.1:5173",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SspWorkspaceRoute", () => {
  it("starts with a new-system form and keeps the system name editable", async () => {
    apiMocks.listSspWorkspaces.mockResolvedValue([]);
    apiMocks.listSspProfiles.mockResolvedValue([
      {
        profile_version_id: "22222222-2222-4222-8222-222222222222",
        profile_id: "nist-rev5",
        version: "1.0.0",
        status: "active",
        display_name: "NIST SP 800-53 Rev. 5",
      },
    ]);

    render(<SspWorkspaceRoute session={session} />);

    const systemName = await screen.findByLabelText("System name");
    expect(systemName).toBeEnabled();
    expect(screen.queryByLabelText("Existing system")).not.toBeInTheDocument();

    fireEvent.change(systemName, { target: { value: "New agency system" } });

    await waitFor(() => {
      expect(systemName).toHaveValue("New agency system");
    });
  });
});
