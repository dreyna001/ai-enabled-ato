import { describe, expect, it } from "vitest";
import { exportNotReadyMessage, resolvePreflightCheck } from "@/utils/preflightLabels";

describe("resolvePreflightCheck", () => {
  it("maps assessor input blockers to operator guidance", () => {
    const info = resolvePreflightCheck("assessor.inputs_present");
    expect(info.title).toBe("Assessor inputs missing");
    expect(info.action).toMatch(/upload/i);
    expect(info.action).not.toMatch(/manual edits/i);
  });

  it("maps privacy blockers to operator guidance", () => {
    const info = resolvePreflightCheck("privacy.artifacts_present");
    expect(info.title).toBe("Privacy artifacts not attached");
    expect(info.action).toMatch(/upload/i);
    expect(info.action).not.toMatch(/artifacts_present to true/i);
  });

  it("uses API message as fallback description for unknown codes", () => {
    const info = resolvePreflightCheck("custom.check", "Custom failure detail.");
    expect(info.description).toBe("Custom failure detail.");
  });
});

describe("exportNotReadyMessage", () => {
  it("summarizes a single blocker", () => {
    expect(exportNotReadyMessage(["privacy.artifacts_present"])).toBe(
      "Export blocked: Privacy artifacts not attached.",
    );
  });

  it("summarizes multiple blockers", () => {
    expect(
      exportNotReadyMessage(["assessor.inputs_present", "privacy.artifacts_present"]),
    ).toBe("Export blocked by 2 readiness items. See the list below.");
  });
});
