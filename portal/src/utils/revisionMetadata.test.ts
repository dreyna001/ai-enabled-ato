import { describe, expect, it } from "vitest";
import type { IntakeReportSuggestedMetadata, PackageRevision } from "@/types";
import {
  applySuggestedMetadata,
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

const suggestions: IntakeReportSuggestedMetadata = {
  profile_id: "fisma_agency_security",
  certification_class: null,
  impact_level: "high",
};

describe("revisionMetadata helpers", () => {
  it("hides metadata while uploading and reveals after finalize", () => {
    expect(shouldRevealRevisionMetadata(makeRevision({ status: "uploading" }))).toBe(false);
    expect(shouldRevealRevisionMetadata(makeRevision({ status: "scanning" }))).toBe(true);
  });

  it("prefills suggestions only when revision values are null", () => {
    const prefilled = applySuggestedMetadata(makeRevision(), suggestions);
    expect(prefilled.profile_id).toBe("fisma_agency_security");
    expect(prefilled.impact_level).toBe("high");
    expect(prefilled.data_origin).toBe("");
    expect(prefilled.sensitivity).toBe("");
  });

  it("leaves the dependent field blank for a profile-only suggestion", () => {
    const prefilled = applySuggestedMetadata(makeRevision(), {
      profile_id: "fisma_agency_security",
      certification_class: null,
      impact_level: null,
    });

    expect(prefilled.profile_id).toBe("fisma_agency_security");
    expect(prefilled.impact_level).toBe("");
    expect(validateMetadataForm(prefilled)).toContain("Select an impact level.");
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

  it("does not overwrite existing human revision values on refresh", () => {
    const revision = makeRevision({
      profile_id: "fedramp_rev5_transition",
      impact_level: "moderate",
    });
    const refreshed = applySuggestedMetadata(revision, {
      profile_id: "fisma_agency_security",
      certification_class: null,
      impact_level: "high",
    });
    expect(refreshed.profile_id).toBe("fedramp_rev5_transition");
    expect(refreshed.impact_level).toBe("moderate");
  });

  it("never prefill human-only data origin or sensitivity from suggestions", () => {
    const prefilled = applySuggestedMetadata(makeRevision(), suggestions);
    expect(prefilled.data_origin).toBe("");
    expect(prefilled.sensitivity).toBe("");
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
