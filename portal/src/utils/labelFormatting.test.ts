import { describe, expect, it } from "vitest";
import { toTitleCaseWords } from "@/utils/labelFormatting";

describe("toTitleCaseWords", () => {
  it("title-cases workflow labels", () => {
    expect(toTitleCaseWords("package revisions")).toBe("Package Revisions");
    expect(toTitleCaseWords("revision workflow")).toBe("Revision Workflow");
    expect(toTitleCaseWords("awaiting_confirmation")).toBe("Awaiting Confirmation");
  });
});
