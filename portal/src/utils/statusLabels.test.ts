import { describe, expect, it } from "vitest";
import {
  packagePreparationStatusLabel,
  revisionStatusLabel,
} from "@/utils/statusLabels";

describe("revisionStatusLabel", () => {
  it("uses explicit operator-facing label for sealed ready revisions", () => {
    expect(revisionStatusLabel("ready")).toBe("Sealed — ready for analysis");
  });

  it("title-cases other revision statuses", () => {
    expect(revisionStatusLabel("awaiting_confirmation")).toBe("Awaiting Confirmation");
  });
});

describe("packagePreparationStatusLabel", () => {
  it("labels preparation states for operators", () => {
    expect(packagePreparationStatusLabel("in_progress")).toBe("In progress");
    expect(packagePreparationStatusLabel("ready_for_external_review")).toBe(
      "Ready for external review",
    );
  });
});
