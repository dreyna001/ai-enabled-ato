import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PackageUploadPanel } from "@/components/PackageUploadPanel";
import type { SessionInfo } from "@/types";

const session: SessionInfo = {
  actor_id: "test-user",
  groups: ["owners"],
  csrf_token: "c".repeat(32),
  portal_origin: "http://localhost:5173",
};

describe("PackageUploadPanel hostile filename rendering", () => {
  it("renders hostile filenames as plain text without HTML execution", () => {
    render(
      <PackageUploadPanel
        session={session}
        revisionId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        onUploaded={() => undefined}
        onFinalized={() => undefined}
        onFinalize={async () => undefined}
        finalizing={false}
      />,
    );
    expect(screen.getByLabelText(/Upload package files/i)).toBeInTheDocument();
    expect(document.querySelector("[dangerouslySetInnerHTML]")).toBeNull();
  });
});
