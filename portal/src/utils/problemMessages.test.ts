import { describe, expect, it } from "vitest";
import { problemMessageForCode } from "@/utils/problemMessages";

describe("problemMessageForCode", () => {
  it("returns stable messages for known error codes", () => {
    expect(problemMessageForCode("self_approval_denied", "fallback")).toContain(
      "separation of duty",
    );
    expect(problemMessageForCode("review_incomplete", "fallback")).toContain(
      "Resolve every matrix disposition",
    );
  });

  it("falls back when code is unknown", () => {
    expect(problemMessageForCode(undefined, "Server error")).toBe("Server error");
  });
});
