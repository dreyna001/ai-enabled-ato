import type {
  FieldProvenanceEntry,
  FieldProvenanceMap,
  PackageDraftDocument,
  SecurityControlEntry,
} from "@/types";
import { humanizeDraftPointer } from "@/utils/draftValidation";

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
  return "From upload";
}

function provenanceSourceField(entry: FieldProvenanceEntry): string | null {
  const locator = entry.source_locator;
  if (!locator || typeof locator !== "object") {
    return null;
  }
  if (locator.kind === "json_pointer" && typeof locator.json_pointer === "string") {
    return humanizeDraftPointer(locator.json_pointer);
  }
  if (typeof locator.json_pointer === "string") {
    return humanizeDraftPointer(locator.json_pointer);
  }
  return null;
}

export function formatProvenanceDetails(entry: FieldProvenanceEntry): string {
  const artifactRef = `Upload ${entry.source_artifact_id.slice(0, 8)}`;
  const sourceField = provenanceSourceField(entry);
  if (sourceField) {
    return `${artifactRef} · pre-filled ${sourceField}`;
  }
  return `${artifactRef} · extracted during intake`;
}

export function formatProvenanceHint(entry: FieldProvenanceEntry): string {
  const sourceField = provenanceSourceField(entry);
  if (sourceField) {
    return `Pre-filled from upload (${sourceField})`;
  }
  return "Pre-filled from uploaded artifact";
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
