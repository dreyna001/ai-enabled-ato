import { describe, expect, it } from "vitest";
import type { FieldProvenanceEntry, PackageDraftDocument } from "@/types";
import {
  cloneDraftDocument,
  createEmptySecurityControl,
  draftDocumentsEqual,
  formatProvenanceDetails,
  isModelAssistedProvenance,
  listSecurityControlIds,
  lookupProvenance,
  profileSectionLabel,
  provenanceLabel,
} from "@/utils/draftDocument";

const SAMPLE_DOCUMENT: PackageDraftDocument = {
  package: {
    profile_id: "fisma_agency_security",
    title: "Demo",
    prepared_for: "Agency",
    reporting_period: null,
  },
  system: {
    display_name: "System",
    authorization_boundary: "Boundary",
    mission_summary: "Mission",
    impact_level: "moderate",
    authorization_path: "agency",
  },
  contacts: {
    system_owner: [{ name: "Owner", role: "SO", email: "so@example.com" }],
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
  security_controls: {
    "AC-1": createEmptySecurityControl(),
  },
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

describe("draftDocument utilities", () => {
  it("clones and compares draft documents", () => {
    const clone = cloneDraftDocument(SAMPLE_DOCUMENT);
    expect(draftDocumentsEqual(SAMPLE_DOCUMENT, clone)).toBe(true);
    clone.package.title = "Changed";
    expect(draftDocumentsEqual(SAMPLE_DOCUMENT, clone)).toBe(false);
  });

  it("lists security control ids in sorted order", () => {
    expect(listSecurityControlIds(SAMPLE_DOCUMENT)).toEqual(["AC-1"]);
  });

  it("labels provenance by extraction method", () => {
    const deterministic: FieldProvenanceEntry = {
      source_artifact_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      source_sha256: "a".repeat(64),
      source_locator: { kind: "json" },
      extraction_method: "deterministic",
    };
    const modelAssisted: FieldProvenanceEntry = {
      ...deterministic,
      extraction_method: "llm_normalize",
    };
    expect(provenanceLabel(deterministic)).toBe("From upload");
    expect(provenanceLabel(modelAssisted)).toBe("Model-assisted");
    expect(isModelAssistedProvenance(modelAssisted)).toBe(true);
    expect(lookupProvenance({ "/package/title": deterministic }, "/package/title")).toBe(
      deterministic,
    );
    expect(formatProvenanceDetails({
      ...deterministic,
      source_locator: { kind: "json_pointer", json_pointer: "/package/title" },
    })).toContain("Package title");
  });

  it("maps profile section labels", () => {
    expect(profileSectionLabel("fedramp_20x_program")).toBe("FedRAMP 20x");
    expect(profileSectionLabel("unknown")).toBeNull();
  });
});
