import { describe, expect, it } from "vitest";

import { formatRunListLabel, runTypeLabel } from "@/utils/runLabels";

describe("runLabels", () => {
  it("maps run types to operator labels", () => {
    expect(runTypeLabel("deterministic_only")).toBe("Deterministic");
    expect(runTypeLabel("targeted")).toBe("Targeted");
    expect(runTypeLabel("full")).toBe("Full");
  });

  it("formats run list labels with type and requested time", () => {
    const label = formatRunListLabel({
      run_type: "deterministic_only",
      requested_at: "2026-07-16T22:45:30Z",
    });
    expect(label.startsWith("Deterministic ·")).toBe(true);
    expect(label).not.toContain("5ba459e1");
  });
});
