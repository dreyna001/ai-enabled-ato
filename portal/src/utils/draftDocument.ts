import type {
  FieldProvenanceEntry,
  FieldProvenanceMap,
  PackageDraftDocument,
  SecurityControlEntry,
} from "@/types";

export function cloneDraftDocument(document: PackageDraftDocument): PackageDraftDocument {
  return structuredClone(document);
}

export function draftDocumentsEqual(
  left: PackageDraftDocument,
  right: PackageDraftDocument,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function lookupProvenance(
  provenance: FieldProvenanceMap,
  jsonPointer: string,
): FieldProvenanceEntry | undefined {
  return provenance[jsonPointer];
}

export function isModelAssistedProvenance(entry: FieldProvenanceEntry | undefined): boolean {
  return entry?.extraction_method === "llm_normalize";
}

export function provenanceLabel(entry: FieldProvenanceEntry | undefined): string | null {
  if (!entry) {
    return null;
  }
  if (entry.extraction_method === "llm_normalize") {
    return "Model-assisted";
  }
  return "Extracted";
}

export function formatProvenanceDetails(entry: FieldProvenanceEntry): string {
  const locator = JSON.stringify(entry.source_locator);
  return `Artifact ${entry.source_artifact_id.slice(0, 8)}… · ${entry.extraction_method} · ${locator}`;
}

export function listSecurityControlIds(document: PackageDraftDocument): string[] {
  return Object.keys(document.security_controls).sort();
}

export function createEmptySecurityControl(): SecurityControlEntry {
  return {
    implementation_status: "planned",
    implementation_statement: "",
    responsible_parties: [],
    evidence_links: [],
  };
}

export function profileSectionLabel(profileId: string): string | null {
  switch (profileId) {
    case "fedramp_20x_program":
      return "FedRAMP 20x";
    case "fedramp_rev5_transition":
      return "FedRAMP Rev. 5 transition";
    case "fisma_agency_security":
      return "Agency FISMA";
    default:
      return null;
  }
}
