import { describe, expect, it } from "vitest";
import { defaultRevisionInput } from "@/utils/revisionDefaults";

describe("defaultRevisionInput", () => {
  it("defaults to synthetic origin for new revisions", () => {
    const input = defaultRevisionInput(null);
    expect(input.data_origin).toBe("synthetic");
    expect(input.profile_id).toBe("fisma_agency_security");
    expect(input.parent_revision_id).toBeNull();
  });

  it("inherits customer origin from parent revision", () => {
    const input = defaultRevisionInput({
      package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      revision_version: 2,
      profile_id: "fedramp_20x_program",
      data_origin: "customer_production",
      sensitivity: "customer_sensitive",
    });
    expect(input.data_origin).toBe("customer_production");
    expect(input.profile_id).toBe("fedramp_20x_program");
    expect(input.parent_revision_id).toBe("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  });

  it("inherits a 20x certification class while preserving its null impact", () => {
    const input = defaultRevisionInput({
      package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      revision_version: 2,
      profile_id: "fedramp_20x_program",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
      certification_class: "C",
      impact_level: null,
    });
    expect(input.certification_class).toBe("C");
    expect(input.impact_level).toBeNull();
  });

  it("uses null certification and a valid impact level outside FedRAMP 20x", () => {
    expect(defaultRevisionInput(null)).toMatchObject({
      certification_class: null,
      impact_level: "moderate",
    });
  });
});
