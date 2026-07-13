export const ARTIFACT_KINDS = [
  "manifest",
  "evidence_document",
  "scanner_export",
  "oscal",
  "attestation",
  "architecture",
  "reference_catalog",
  "fedramp_cpo",
  "fedramp_sdr",
  "fedramp_ocr",
  "fedramp_scg",
] as const;

export type ArtifactKind = (typeof ARTIFACT_KINDS)[number];

export const ARTIFACT_KIND_LABELS: Record<ArtifactKind, string> = {
  manifest: "Structured manifest (JSON)",
  evidence_document: "Evidence document",
  scanner_export: "Scanner export",
  oscal: "OSCAL baseline",
  attestation: "Assessor attestation",
  architecture: "Architecture diagram",
  reference_catalog: "Reference catalog",
  fedramp_cpo: "FedRAMP CPO",
  fedramp_sdr: "FedRAMP SDR",
  fedramp_ocr: "FedRAMP OCR",
  fedramp_scg: "FedRAMP SCG",
};

export const UPLOAD_ACCEPT =
  ".json,.pdf,.docx,.xlsx,.txt,.md,.xml,.png,.jpg,.jpeg,.webp,.svg,application/json,application/pdf,text/plain,text/markdown,application/xml,image/png,image/jpeg,image/webp,image/svg+xml";

export function inferArtifactKind(file: File): ArtifactKind {
  const name = file.name.toLowerCase();
  const type = file.type.toLowerCase();

  if (type === "application/json" || name.endsWith(".json")) {
    return "manifest";
  }
  if (
    name.includes("nessus") ||
    name.endsWith(".sarif") ||
    name.includes("scan") ||
    name.includes("stig")
  ) {
    return "scanner_export";
  }
  if (name.includes("oscal") || (name.endsWith(".xml") && name.includes("catalog"))) {
    return "oscal";
  }
  if (name.includes("sar") || name.includes("attestation")) {
    return "attestation";
  }
  if (name.includes("architecture") || name.includes("diagram")) {
    return "architecture";
  }
  if (name.includes("baseline") || name.includes("catalog")) {
    return "reference_catalog";
  }
  return "evidence_document";
}

export function isArtifactKind(value: string): value is ArtifactKind {
  return (ARTIFACT_KINDS as readonly string[]).includes(value);
}
