import type {
  DataOrigin,
  IntakeReportSuggestedMetadata,
  PackageRevision,
  PatchPackageRevisionMetadataInput,
  ProfileId,
  Sensitivity,
} from "@/types";

export type RevisionMetadataFormValues = {
  profile_id: ProfileId | "";
  certification_class: "B" | "C" | "";
  impact_level: "low" | "moderate" | "high" | "";
  data_origin: DataOrigin | "";
  sensitivity: Sensitivity | "";
};

export function shouldRevealRevisionMetadata(revision: PackageRevision): boolean {
  return revision.status !== "uploading";
}

export function isRevisionMetadataEditable(revision: PackageRevision): boolean {
  return (
    revision.status === "scanning" ||
    revision.status === "extracting" ||
    revision.status === "awaiting_confirmation"
  );
}

function isUnsetRevisionValue(value: string | null | undefined): boolean {
  return value == null || value === "";
}

function isProfileId(value: unknown): value is ProfileId {
  return (
    value === "fedramp_20x_program" ||
    value === "fedramp_rev5_transition" ||
    value === "fisma_agency_security"
  );
}

function isCertificationClass(value: unknown): value is "B" | "C" {
  return value === "B" || value === "C";
}

function isImpactLevel(value: unknown): value is "low" | "moderate" | "high" {
  return value === "low" || value === "moderate" || value === "high";
}

function isDataOrigin(value: unknown): value is DataOrigin {
  return (
    value === "synthetic" ||
    value === "redacted_nonproduction" ||
    value === "customer_production"
  );
}

function isSensitivity(value: unknown): value is Sensitivity {
  return (
    value === "public" ||
    value === "internal_unclassified" ||
    value === "customer_sensitive" ||
    value === "cui" ||
    value === "classified" ||
    value === "unknown"
  );
}

export function metadataValuesFromRevision(
  revision: PackageRevision,
): RevisionMetadataFormValues {
  return {
    profile_id: isProfileId(revision.profile_id) ? revision.profile_id : "",
    certification_class: isCertificationClass(revision.certification_class)
      ? revision.certification_class
      : "",
    impact_level: isImpactLevel(revision.impact_level) ? revision.impact_level : "",
    data_origin: isDataOrigin(revision.data_origin) ? revision.data_origin : "",
    sensitivity: isSensitivity(revision.sensitivity) ? revision.sensitivity : "",
  };
}

export function applySuggestedMetadata(
  revision: PackageRevision,
  suggestions: IntakeReportSuggestedMetadata | null | undefined,
): RevisionMetadataFormValues {
  const base = metadataValuesFromRevision(revision);
  if (!suggestions) {
    return base;
  }

  if (isUnsetRevisionValue(revision.profile_id) && isProfileId(suggestions.profile_id)) {
    base.profile_id = suggestions.profile_id;
  }
  if (
    isUnsetRevisionValue(revision.certification_class) &&
    isCertificationClass(suggestions.certification_class)
  ) {
    base.certification_class = suggestions.certification_class;
  }
  if (isUnsetRevisionValue(revision.impact_level) && isImpactLevel(suggestions.impact_level)) {
    base.impact_level = suggestions.impact_level;
  }

  return normalizeMetadataFormForProfile(base);
}

export function normalizeMetadataFormForProfile(
  values: RevisionMetadataFormValues,
): RevisionMetadataFormValues {
  if (values.profile_id === "fedramp_20x_program") {
    return {
      ...values,
      impact_level: "",
    };
  }
  if (values.profile_id === "fedramp_rev5_transition" || values.profile_id === "fisma_agency_security") {
    return {
      ...values,
      certification_class: "",
    };
  }
  return values;
}

export function validateMetadataForm(values: RevisionMetadataFormValues): string[] {
  const issues: string[] = [];
  if (!values.profile_id) {
    issues.push("Select an authorization profile.");
  }
  if (!values.data_origin) {
    issues.push("Select data origin (required human attestation).");
  }
  if (!values.sensitivity) {
    issues.push("Select sensitivity (required human attestation).");
  }
  if (values.profile_id === "fedramp_20x_program") {
    if (!values.certification_class) {
      issues.push("Select a FedRAMP 20x certification class.");
    }
  } else if (values.profile_id) {
    if (!values.impact_level) {
      issues.push("Select an impact level.");
    }
  }
  return issues;
}

export function isRevisionMetadataComplete(revision: PackageRevision): boolean {
  return validateMetadataForm(metadataValuesFromRevision(revision)).length === 0;
}

export function metadataFormEquals(
  left: RevisionMetadataFormValues,
  right: RevisionMetadataFormValues,
): boolean {
  return (
    left.profile_id === right.profile_id &&
    left.certification_class === right.certification_class &&
    left.impact_level === right.impact_level &&
    left.data_origin === right.data_origin &&
    left.sensitivity === right.sensitivity
  );
}

export function buildMetadataPatchPayload(
  saved: RevisionMetadataFormValues,
  current: RevisionMetadataFormValues,
): PatchPackageRevisionMetadataInput | null {
  const normalizedCurrent = normalizeMetadataFormForProfile(current);
  const normalizedSaved = normalizeMetadataFormForProfile(saved);
  if (metadataFormEquals(normalizedSaved, normalizedCurrent)) {
    return null;
  }

  const patch: PatchPackageRevisionMetadataInput = {};
  if (normalizedCurrent.profile_id !== normalizedSaved.profile_id) {
    patch.profile_id = normalizedCurrent.profile_id || undefined;
  }
  if (normalizedCurrent.data_origin !== normalizedSaved.data_origin) {
    patch.data_origin = normalizedCurrent.data_origin || undefined;
  }
  if (normalizedCurrent.sensitivity !== normalizedSaved.sensitivity) {
    patch.sensitivity = normalizedCurrent.sensitivity || undefined;
  }

  const profileId = normalizedCurrent.profile_id || normalizedSaved.profile_id;
  if (profileId === "fedramp_20x_program") {
    if (normalizedCurrent.certification_class !== normalizedSaved.certification_class) {
      patch.certification_class = normalizedCurrent.certification_class || null;
    }
    if (
      normalizedCurrent.profile_id !== normalizedSaved.profile_id ||
      normalizedSaved.impact_level !== ""
    ) {
      patch.impact_level = null;
    }
  } else if (profileId) {
    if (normalizedCurrent.impact_level !== normalizedSaved.impact_level) {
      patch.impact_level = normalizedCurrent.impact_level || null;
    }
    if (
      normalizedCurrent.profile_id !== normalizedSaved.profile_id ||
      normalizedSaved.certification_class !== ""
    ) {
      patch.certification_class = null;
    }
  }

  return Object.keys(patch).length > 0 ? patch : null;
}
