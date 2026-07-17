import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PackageAssistantPanel } from "@/components/PackageAssistantPanel";
import type { SessionInfo } from "@/types";

const session: SessionInfo = {
  actor_id: "test-user",
  groups: ["owners"],
  csrf_token: "c".repeat(32),
  portal_origin: "http://localhost:5173",
};

afterEach(cleanup);

describe("PackageAssistantPanel run gating", () => {
  it("keeps search usable while chat waits for a succeeded run", () => {
    render(
      <PackageAssistantPanel
        session={session}
        revisionId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        runId={null}
        enabled
      />,
    );

    fireEvent.change(screen.getByLabelText("Search package content"), {
      target: { value: "AC-2" },
    });

    expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
    expect(screen.getByLabelText("Ask about this package")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(
      screen.getByText(
        "Select a succeeded analysis run before asking citation-backed questions.",
      ),
    ).toBeVisible();
  });

  it("enables chat input when a succeeded run id is supplied", () => {
    render(
      <PackageAssistantPanel
        session={session}
        revisionId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        runId="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        enabled
      />,
    );

    expect(screen.getByLabelText("Ask about this package")).toBeEnabled();
    expect(
      screen.queryByText(
        "Select a succeeded analysis run before asking citation-backed questions.",
      ),
    ).not.toBeInTheDocument();
  });
});
