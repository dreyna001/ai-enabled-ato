import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { PortalApp } from "@/PortalApp";
import type { SessionInfo } from "@/types";

const apiMocks = vi.hoisted(() => ({
  fetchReadiness: vi.fn(),
  fetchSession: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/api/client", async () => ({
  ...(await vi.importActual<typeof import("@/api/client")>("@/api/client")),
  ...apiMocks,
}));

vi.mock("@/pages/SspWorkspaceRoute", () => ({
  SspWorkspaceRoute: () => <div>SSP workspace</div>,
}));

const session: SessionInfo = {
  actor_id: "logout-user",
  groups: ["owners"],
  csrf_token: "l".repeat(32),
  portal_origin: "https://portal.example.gov",
};

beforeEach(() => {
  apiMocks.fetchReadiness.mockResolvedValue({ status: "ok", checks: {} });
  apiMocks.fetchSession.mockResolvedValueOnce(session).mockResolvedValueOnce(null);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PortalApp sign-out flow", () => {
  it("logs out with the current session before refreshing authentication", async () => {
    let completeLogout!: () => void;
    apiMocks.logout.mockReturnValue(
      new Promise<void>((resolve) => {
        completeLogout = resolve;
      }),
    );

    render(
      <MemoryRouter initialEntries={["/ssp"]}>
        <PortalApp />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Sign Out" }));

    expect(apiMocks.logout).toHaveBeenCalledOnce();
    expect(apiMocks.logout).toHaveBeenCalledWith(session);
    expect(apiMocks.fetchSession).toHaveBeenCalledOnce();

    await act(async () => {
      completeLogout();
    });

    await screen.findByRole("button", { name: "Sign in" });
    await waitFor(() => expect(apiMocks.fetchSession).toHaveBeenCalledTimes(2));
  });
});
