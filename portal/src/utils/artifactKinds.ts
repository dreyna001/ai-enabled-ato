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
  "privacy_artifact",
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
  privacy_artifact: "Privacy artifact",
};

export const UPLOAD_ACCEPT =
  ".json,.pdf,.docx,.xlsx,.txt,.md,.xml,.png,.jpg,.jpeg,.webp,.svg,application/json,application/pdf,text/plain,text/markdown,application/xml,image/png,image/jpeg,image/webp,image/svg+xml";

/** Matches `source_artifacts._ALLOWED_DECLARED_MEDIA_TYPES` on the API. */
export const ALLOWED_UPLOAD_MEDIA_TYPES = [
  "application/json",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/xml",
  "image/jpeg",
  "image/png",
  "image/svg+xml",
  "image/webp",
  "text/markdown",
  "text/plain",
] as const;

export type AllowedUploadMediaType = (typeof ALLOWED_UPLOAD_MEDIA_TYPES)[number];

const GENERIC_BROWSER_MEDIA_TYPES = new Set(["", "application/octet-stream"]);

const EXTENSION_MEDIA_TYPES: Record<string, AllowedUploadMediaType> = {
  ".json": "application/json",
  ".pdf": "application/pdf",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".xml": "application/xml",
  ".md": "text/markdown",
  ".markdown": "text/markdown",
  ".txt": "text/plain",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
};

const OFFICE_OPENXML_MEDIA_TYPES = new Set<AllowedUploadMediaType>([
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

function normalizeBrowserMediaType(value: string): string {
  return value.trim().split(";")[0]?.trim().toLowerCase() ?? "";
}

function mediaTypeFromFilename(filename: string): AllowedUploadMediaType | null {
  const lower = filename.toLowerCase();
  for (const [extension, mediaType] of Object.entries(EXTENSION_MEDIA_TYPES)) {
    if (lower.endsWith(extension)) {
      return mediaType;
    }
  }
  return null;
}

export function isAllowedUploadMediaType(value: string): value is AllowedUploadMediaType {
  return (ALLOWED_UPLOAD_MEDIA_TYPES as readonly string[]).includes(value);
}

/** Resolve the declared Content-Type the API upload boundary expects. */
export function resolveUploadMediaType(file: File): string {
  const extensionType = mediaTypeFromFilename(file.name);
  const browserType = normalizeBrowserMediaType(file.type);
  const browserIsGeneric =
    GENERIC_BROWSER_MEDIA_TYPES.has(browserType) || !browserType;
  const browserIsAllowed =
    browserType.length > 0 && isAllowedUploadMediaType(browserType);

  if (extensionType) {
    if (browserIsGeneric || !browserIsAllowed) {
      return extensionType;
    }
    if (extensionType === "text/markdown" && browserType === "text/plain") {
      return extensionType;
    }
    if (OFFICE_OPENXML_MEDIA_TYPES.has(extensionType) && extensionType !== browserType) {
      return extensionType;
    }
    if (extensionType === "application/json" && browserType !== extensionType) {
      return extensionType;
    }
  }

  if (browserIsAllowed) {
    return browserType;
  }
  if (extensionType) {
    return extensionType;
  }
  return browserType;
}

export function validateUploadFile(file: File): string | null {
  const mediaType = resolveUploadMediaType(file);
  if (!mediaType || !isAllowedUploadMediaType(mediaType)) {
    return "This file type is not supported for package upload. Use JSON, PDF, DOCX, XLSX, XML, Markdown, plain text, or supported images.";
  }
  return null;
}

/** Re-wrap the file so multipart upload sends a supported declared media type. */
export function prepareUploadFile(file: File): File {
  const mediaType = resolveUploadMediaType(file);
  if (file.type === mediaType) {
    return file;
  }
  return new File([file], file.name, {
    type: mediaType,
    lastModified: file.lastModified,
  });
}

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
