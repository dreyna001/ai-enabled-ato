import { describe, expect, it } from "vitest";
import {
  hydrateCategorizationValues,
  normalizeCategorizationEvidenceLinks,
  validateCategorizationChange,
} from "@/utils/sspFieldErrors";

describe("sspFieldErrors", () => {
  it("drops invalid artifact ids and empty locators", () => {
    const artifactId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const normalized = normalizeCategorizationEvidenceLinks(
      [
        {
          id: "bad:categorization",
          artifactId: "artifact-1",
          locator: JSON.stringify({ kind: "categorization_attestation" }),
        },
        {
          id: `${artifactId}:categorization`,
          artifactId,
          locator: "{}",
        },
      ],
      [
        {
          id: artifactId,
          name: "overview.docx",
          mediaType:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          state: "processed",
          uploadedAt: "2026-09-06",
        },
      ],
    );

    expect(normalized).toEqual([
      {
        artifact_id: artifactId,
        locator: { kind: "categorization_attestation" },
      },
    ]);
  });

  it("reports missing processed evidence selections", () => {
    const issues = validateCategorizationChange(
      {
        confidentiality: "moderate",
        integrity: "moderate",
        availability: "moderate",
        confidentialityRationale: "Rationale",
        integrityRationale: "Rationale",
        availabilityRationale: "Rationale",
        confidentialityEvidence: [
          {
            id: "artifact-1:categorization",
            artifactId: "artifact-1",
            locator: JSON.stringify({ kind: "categorization_attestation" }),
          },
        ],
        integrityEvidence: [],
        availabilityEvidence: [],
      },
      [
        {
          id: "artifact-1",
          name: "overview.docx",
          mediaType: "text/plain",
          state: "processed",
          uploadedAt: "2026-09-06",
        },
      ],
    );

    expect(issues.map((issue) => issue.field)).toEqual([
      "confidentialityEvidence",
      "integrityEvidence",
      "availabilityEvidence",
    ]);
  });

  it("hydrates agent evidence into processed artifact checkboxes", () => {
    const artifactId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const hydrated = hydrateCategorizationValues(
      {
        confidentiality: "moderate",
        integrity: "moderate",
        availability: "moderate",
        confidentialityRationale: "Rationale",
        integrityRationale: "Rationale",
        availabilityRationale: "Rationale",
        confidentialityEvidence: [
          {
            id: "legacy:0",
            artifactId: artifactId,
            locator: "{}",
          },
        ],
        integrityEvidence: [],
        availabilityEvidence: [],
      },
      [
        {
          id: artifactId,
          name: "overview.docx",
          mediaType:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          state: "processed",
          uploadedAt: "2026-09-06",
        },
      ],
    );

    expect(hydrated.confidentialityEvidence).toEqual([
      {
        id: `${artifactId}:categorization`,
        artifactId,
        locator: JSON.stringify({ kind: "categorization_attestation" }),
      },
    ]);
  });
});
