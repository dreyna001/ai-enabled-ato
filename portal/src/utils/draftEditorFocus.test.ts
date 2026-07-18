import { describe, expect, it } from "vitest";
import { isEditableDraftPointer, tabForDraftPointer } from "@/utils/draftEditorFocus";

describe("draftEditorFocus", () => {
  it("identifies editable draft pointers and tabs", () => {
    expect(isEditableDraftPointer("/system/display_name")).toBe(true);
    expect(isEditableDraftPointer("/package/profile_id")).toBe(false);
    expect(isEditableDraftPointer("/assessor_inputs/foo")).toBe(false);
    expect(tabForDraftPointer("/system/display_name")).toBe("system");
    expect(tabForDraftPointer("/package/title")).toBe("package");
    expect(tabForDraftPointer("/security_controls/AC-1/implementation_status")).toBe(
      "controls",
    );
  });
});
