import { describe, expect, it } from "vitest";
import { metadataDefaultsFromParent, profileFieldsForRevision } from "@/utils/revisionDefaults";

describe("profileFieldsForRevision", () => {
  it("defaults to class B and null impact for FedRAMP 20x", () => {
    expect(profileFieldsForRevision("fedramp_20x_program")).toEqual({
      certification_class: "B",
      impact_level: null,
    });
  });

  it("inherits a 20x certification class while preserving its null impact", () => {
    expect(
      profileFieldsForRevision("fedramp_20x_program", {
        certification_class: "C",
        impact_level: null,
      }),
    ).toEqual({
      certification_class: "C",
      impact_level: null,
    });
  });

  it("uses null certification and a valid impact level outside FedRAMP 20x", () => {
    expect(profileFieldsForRevision("fisma_agency_security")).toEqual({
      certification_class: null,
      impact_level: "moderate",
    });
  });
});

describe("metadataDefaultsFromParent", () => {
  it("defaults to synthetic origin for new revisions", () => {
    const defaults = metadataDefaultsFromParent(null);
    expect(defaults.data_origin).toBe("synthetic");
    expect(defaults.profile_id).toBe("fisma_agency_security");
  });

  it("inherits customer origin from parent revision", () => {
    const defaults = metadataDefaultsFromParent({
      package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      status: "ready",
      package_preparation_status: "in_progress",
      revision_version: 2,
      profile_id: "fedramp_20x_program",
      data_origin: "customer_production",
      sensitivity: "customer_sensitive",
    });
    expect(defaults.data_origin).toBe("customer_production");
    expect(defaults.profile_id).toBe("fedramp_20x_program");
    expect(defaults.sensitivity).toBe("customer_sensitive");
  });
});
