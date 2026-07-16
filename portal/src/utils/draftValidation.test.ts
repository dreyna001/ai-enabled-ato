import { describe, expect, it } from "vitest";
import type { PackageDraftDocument } from "@/types";
import { createEmptySecurityControl } from "@/utils/draftDocument";
import {
  formatDraftValidationIssues,
  humanizeDraftPointer,
  normalizeDraftDocumentForProfile,
  validateDraftForSeal,
} from "@/utils/draftValidation";

const BASE_DOCUMENT: PackageDraftDocument = {
  package: {
    profile_id: "fisma_agency_security",
    title: "Demo package",
    prepared_for: "Agency",
    reporting_period: null,
  },
  system: {
    display_name: "Customer Records Portal",
    authorization_boundary: "Single VPC",
    mission_summary: "Case management",
    impact_level: "moderate",
    authorization_path: "agency",
  },
  contacts: {
    system_owner: [],
    isso: [],
    issm: [],
    control_owners: [],
    assessors: [],
    approvers: [],
  },
  control_set: {
    source: {},
    tailoring: [],
    organization_defined_parameters: {},
    inheritance: [],
  },
  security_controls: {},
  evidence: {},
  findings: {},
  poam_candidates: {},
  assessor_inputs: {},
  privacy: { artifacts_present: false, scope_notice: "" },
  fedramp_20x: null,
  fedramp_rev5_transition: null,
  fisma_agency_security: {},
  extensions: {},
};

describe("validateDraftForSeal", () => {
  it("accepts a minimal valid FISMA draft", () => {
    expect(validateDraftForSeal(BASE_DOCUMENT)).toEqual([]);
  });

  it("flags missing impact level before confirm", () => {
    const document = {
      ...BASE_DOCUMENT,
      system: { ...BASE_DOCUMENT.system, impact_level: null },
    };
    const issues = validateDraftForSeal(document, { revisionImpactLevel: null });
    expect(issues.some((issue) => issue.pointer === "/system/impact_level")).toBe(true);
    expect(formatDraftValidationIssues(issues)).toContain("Impact level");
  });

  it("flags invalid control implementation status", () => {
    const document = {
      ...BASE_DOCUMENT,
      security_controls: {
        "AC-1": {
          ...createEmptySecurityControl(),
          implementation_status: "unknown",
          implementation_statement: "Statement",
        },
      },
    };
    const issues = validateDraftForSeal(document);
    expect(issues[0]?.pointer).toContain("implementation_status");
  });

  it("humanizes json pointers for operator messages", () => {
    expect(humanizeDraftPointer("/system/impact_level")).toBe("Impact level");
    expect(humanizeDraftPointer("/security_controls/AC-1/implementation_status")).toContain(
      "AC-1",
    );
  });

  it("normalizes profile-specific fields so invalid combinations cannot persist", () => {
    const document = {
      ...BASE_DOCUMENT,
      package: { ...BASE_DOCUMENT.package, profile_id: "fedramp_20x_program" as const },
      system: {
        ...BASE_DOCUMENT.system,
        impact_level: "low" as const,
        authorization_path: "agency",
      },
      fedramp_20x: {},
      fisma_agency_security: null,
    };
    const normalized = normalizeDraftDocumentForProfile(document);
    expect(normalized.system.impact_level).toBeNull();
    expect(normalized.system.authorization_path).toBe("fedramp");
    expect(
      validateDraftForSeal(normalized).some((issue) => issue.pointer === "/system/impact_level"),
    ).toBe(false);
  });

  it("locks authorization path to agency for FISMA drafts", () => {
    const document = {
      ...BASE_DOCUMENT,
      system: { ...BASE_DOCUMENT.system, authorization_path: "fedramp" },
    };
    const normalized = normalizeDraftDocumentForProfile(document);
    expect(normalized.system.authorization_path).toBe("agency");
  });
});
