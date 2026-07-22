import { describe, expect, it } from "vitest";
import type { PackageRevision } from "@/types";
import {
  buildCreateRevisionInput,
  buildMetadataPatchPayload,
  metadataValuesFromRevision,
  normalizeMetadataFormForProfile,
  shouldRevealRevisionMetadata,
  validateMetadataForm,
} from "@/utils/revisionMetadata";

const revisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const systemId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function makeRevision(overrides: Partial<PackageRevision> = {}): PackageRevision {
  return {
    package_revision_id: revisionId,
    system_id: systemId,
    status: "awaiting_confirmation",
    package_preparation_status: "in_progress",
    revision_version: 2,
    profile_id: null,
    data_origin: null,
    sensitivity: null,
    impact_level: null,
    certification_class: null,
    ...overrides,
  };
}

describe("revisionMetadata helpers", () => {
  it("reveals metadata during upload and later intake stages", () => {
    expect(shouldRevealRevisionMetadata(makeRevision({ status: "uploading" }))).toBe(true);
    expect(shouldRevealRevisionMetadata(makeRevision({ status: "scanning" }))).toBe(true);
  });

  it("builds create input with profile-specific fields", () => {
    expect(
      buildCreateRevisionInput(
        {
          profile_id: "fedramp_20x_program",
          certification_class: "B",
          impact_level: "",
          data_origin: "synthetic",
          sensitivity: "internal_unclassified",
        },
        null,
      ),
    ).toEqual({
      parent_revision_id: null,
      profile_id: "fedramp_20x_program",
      certification_class: "B",
      impact_level: null,
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });

    expect(
      buildCreateRevisionInput(
        {
          profile_id: "fisma_agency_security",
          certification_class: "",
          impact_level: "high",
          data_origin: "customer_production",
          sensitivity: "cui",
        },
        "parent-id",
      ),
    ).toEqual({
      parent_revision_id: "parent-id",
      profile_id: "fisma_agency_security",
      certification_class: null,
      impact_level: "high",
      data_origin: "customer_production",
      sensitivity: "cui",
    });
  });

  it("leaves the dependent field blank for a profile-only form state", () => {
    const incomplete = normalizeMetadataFormForProfile({
      profile_id: "fisma_agency_security",
      certification_class: "",
      impact_level: "",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });

    expect(incomplete.impact_level).toBe("");
    expect(validateMetadataForm(incomplete)).toContain("Select an impact level.");
  });

  it("clears incompatible profile fields without inserting defaults", () => {
    const switchedTo20x = normalizeMetadataFormForProfile({
      profile_id: "fedramp_20x_program",
      certification_class: "",
      impact_level: "high",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });
    expect(switchedTo20x.certification_class).toBe("");
    expect(switchedTo20x.impact_level).toBe("");
    expect(validateMetadataForm(switchedTo20x)).toContain(
      "Select a FedRAMP 20x certification class.",
    );

    const switchedToFisma = normalizeMetadataFormForProfile({
      ...switchedTo20x,
      profile_id: "fisma_agency_security",
      certification_class: "C",
    });
    expect(switchedToFisma.certification_class).toBe("");
    expect(switchedToFisma.impact_level).toBe("");
    expect(validateMetadataForm(switchedToFisma)).toContain(
      "Select an impact level.",
    );
  });

  it("builds a minimal metadata patch payload", () => {
    const saved = metadataValuesFromRevision(
      makeRevision({
        profile_id: "fisma_agency_security",
        impact_level: "moderate",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
      }),
    );
    const current = {
      ...saved,
      impact_level: "high" as const,
    };
    expect(buildMetadataPatchPayload(saved, current)).toEqual({
      impact_level: "high",
    });
  });

  it("never inserts class or impact defaults into a patch", () => {
    const saved = metadataValuesFromRevision(makeRevision());
    const current = normalizeMetadataFormForProfile({
      ...saved,
      profile_id: "fisma_agency_security",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });

    expect(buildMetadataPatchPayload(saved, current)).toEqual({
      profile_id: "fisma_agency_security",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
      certification_class: null,
    });
  });
});
