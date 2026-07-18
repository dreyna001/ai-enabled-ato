import { describe, expect, it } from "vitest";
import type { PackageDraftDocument } from "@/types";
import { createEmptySecurityControl } from "@/utils/draftDocument";
import {
  applyConflictCandidateSelection,
  extractCandidateValue,
  pruneResolvedConflictsAfterEdit,
  readDraftIntakeConflicts,
  removeIntakeConflictByPointer,
} from "@/utils/draftConflictResolution";
import {
  getValueAtJsonPointer,
  isValidJsonPointer,
  pointerTargetsHumanOnlyField,
  setValueAtJsonPointer,
  validateDraftJsonPointer,
} from "@/utils/jsonPointer";

function buildDocument(overrides: Partial<PackageDraftDocument> = {}): PackageDraftDocument {
  return {
    package: {
      profile_id: "fisma_agency_security",
      title: "Original title",
      prepared_for: "Agency",
      reporting_period: null,
    },
    system: {
      display_name: "Original System",
      authorization_boundary: "Boundary",
      mission_summary: "Mission",
      impact_level: "moderate",
      authorization_path: "agency",
    },
    contacts: {
      system_owner: [{ name: "Owner", role: "Owner", email: "owner@example.com" }],
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
    fisma_agency_security: null,
    extensions: {
      intake_conflicts: [
        {
          conflict_id: "conflict-1",
          target_pointer: "/system/display_name",
          resolution: "pending",
          candidates: [{ value: "Alpha" }, { value: "Beta" }],
        },
        {
          conflict_id: "conflict-2",
          target_pointer: "/package/title",
          resolution: "pending",
          candidates: [{ value: "Title A" }, { value: "Title B" }],
        },
      ],
    },
    ...overrides,
  };
}

describe("jsonPointer utilities", () => {
  it("rejects empty, non-absolute, and prototype-pollution pointers", () => {
    expect(validateDraftJsonPointer(""))?.toMatchObject({ code: "empty_pointer" });
    expect(validateDraftJsonPointer("package/title"))?.toMatchObject({
      code: "non_absolute_pointer",
    });
    expect(isValidJsonPointer("/__proto__/title")).toBe(false);
    expect(isValidJsonPointer("/package/constructor")).toBe(false);
    expect(pointerTargetsHumanOnlyField("/revision/data_origin")).toBe(true);
    expect(pointerTargetsHumanOnlyField("/revision/sensitivity")).toBe(true);
  });

  it("sets and reads values without mutating the source document", () => {
    const document = buildDocument();
    const setResult = setValueAtJsonPointer(document, "/system/display_name", "Updated");
    expect(setResult.ok).toBe(true);
    if (setResult.ok) {
      expect(getValueAtJsonPointer(setResult.document, "/system/display_name")).toBe("Updated");
      expect(document.system.display_name).toBe("Original System");
    }
  });

  it("rejects missing parent paths", () => {
    const document = buildDocument();
    const setResult = setValueAtJsonPointer(document, "/missing/field", "Value");
    expect(setResult.ok).toBe(false);
  });
});

describe("draftConflictResolution", () => {
  it("extracts bounded candidate values only", () => {
    expect(extractCandidateValue({ value: "Alpha" })).toBe("Alpha");
    expect(extractCandidateValue({ proposed_value: { nested: "object" } })).toEqual({
      nested: "object",
    });
    expect(extractCandidateValue({ value: { prompt: "secret" } })).toBeNull();
  });

  it("applies candidate value, removes one conflict, and leaves others intact", () => {
    const document = buildDocument();
    const result = applyConflictCandidateSelection(
      document,
      [
        {
          field: "/system/display_name",
          values: [{ value: "Cloud Platform Alpha" }, { value: "Platform Alpha Cloud" }],
        },
      ],
      "/system/display_name",
      0,
    );

    expect(result.ok).toBe(true);
    if (!result.ok) {
      return;
    }
    expect(result.document.system.display_name).toBe("Cloud Platform Alpha");
    expect(readDraftIntakeConflicts(result.document)).toHaveLength(1);
    expect(readDraftIntakeConflicts(result.document)[0]?.target_pointer).toBe("/package/title");
  });

  it("rejects human-only metadata pointers", () => {
    const document = buildDocument();
    const result = applyConflictCandidateSelection(
      document,
      [{ field: "/metadata/data_origin", values: [{ value: "synthetic" }, { value: "prod" }] }],
      "/metadata/data_origin",
      0,
    );
    expect(result.ok).toBe(false);
    if (result.ok) {
      return;
    }
    expect(result.error).toMatch(/attestation/i);
  });

  it("removes conflicts after manual edits change the conflicted pointer", () => {
    const previous = buildDocument();
    const next = {
      ...previous,
      system: { ...previous.system, display_name: "Operator edited name" },
    };
    const pruned = pruneResolvedConflictsAfterEdit(previous, next, [
      { field: "/system/display_name", values: [{ value: "Alpha" }, { value: "Beta" }] },
      { field: "/package/title", values: [{ value: "A" }, { value: "B" }] },
    ]);
    expect(readDraftIntakeConflicts(pruned).map((entry) => entry.target_pointer)).toEqual([
      "/package/title",
    ]);
  });

  it("does not remove conflicts when the pointer value is unchanged", () => {
    const previous = buildDocument();
    const next = {
      ...previous,
      package: { ...previous.package, prepared_for: "Updated audience" },
    };
    const pruned = pruneResolvedConflictsAfterEdit(previous, next, [
      { field: "/system/display_name", values: [{ value: "Alpha" }, { value: "Beta" }] },
    ]);
    expect(readDraftIntakeConflicts(pruned)).toHaveLength(2);
  });

  it("supports security control pointers with escaped segments", () => {
    const document = buildDocument({
      security_controls: {
        "AC/1": createEmptySecurityControl(),
      },
      extensions: {
        intake_conflicts: [
          {
            conflict_id: "control-conflict",
            target_pointer: "/security_controls/AC~11/implementation_statement",
            resolution: "pending",
            candidates: [{ value: "Statement A" }, { value: "Statement B" }],
          },
        ],
      },
    });
    const result = applyConflictCandidateSelection(
      document,
      [
        {
          field: "/security_controls/AC~11/implementation_statement",
          values: [{ value: "Statement A" }, { value: "Statement B" }],
        },
      ],
      "/security_controls/AC~11/implementation_statement",
      0,
    );
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(
        getValueAtJsonPointer(
          result.document,
          "/security_controls/AC~11/implementation_statement",
        ),
      ).toBe("Statement A");
      expect(readDraftIntakeConflicts(result.document)).toHaveLength(0);
    }
  });

  it("removeIntakeConflictByPointer is a no-op when the pointer is absent", () => {
    const document = buildDocument();
    const next = removeIntakeConflictByPointer(document, "/privacy/scope_notice");
    expect(next).toBe(document);
  });
});
