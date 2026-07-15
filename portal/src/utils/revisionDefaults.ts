import type {
  CreateRevisionInput,
  DataOrigin,
  PackageRevision,
  ProfileId,
  Sensitivity,
} from "@/types";

export const PROFILE_OPTIONS: Array<{ id: ProfileId; label: string }> = [
  { id: "fisma_agency_security", label: "Agency FISMA security" },
  { id: "fedramp_rev5_transition", label: "FedRAMP Rev. 5 transition" },
  { id: "fedramp_20x_program", label: "FedRAMP 20x program" },
];

export const DATA_ORIGIN_OPTIONS: Array<{ id: DataOrigin; label: string }> = [
  { id: "synthetic", label: "Synthetic (demo / lab)" },
  { id: "redacted_nonproduction", label: "Redacted non-production" },
  { id: "customer_production", label: "Customer production" },
];

export const SENSITIVITY_OPTIONS: Array<{ id: Sensitivity; label: string }> = [
  { id: "internal_unclassified", label: "Internal unclassified" },
  { id: "public", label: "Public" },
  { id: "customer_sensitive", label: "Customer sensitive" },
  { id: "cui", label: "CUI" },
  { id: "unknown", label: "Unknown" },
];

export const CERTIFICATION_CLASS_OPTIONS = [
  { id: "B", label: "Class B" },
  { id: "C", label: "Class C" },
] as const;

export const IMPACT_LEVEL_OPTIONS = [
  { id: "low", label: "Low" },
  { id: "moderate", label: "Moderate" },
  { id: "high", label: "High" },
] as const;

type ProfileFields = Pick<CreateRevisionInput, "certification_class" | "impact_level">;

function isCertificationClass(value: unknown): value is "B" | "C" {
  return value === "B" || value === "C";
}

function isImpactLevel(value: unknown): value is "low" | "moderate" | "high" {
  return value === "low" || value === "moderate" || value === "high";
}

export function profileFieldsForRevision(profileId: ProfileId, source?: { certification_class?: unknown; impact_level?: unknown } | null): ProfileFields {
  if (profileId === "fedramp_20x_program") {
    return {
      certification_class: isCertificationClass(source?.certification_class) ? source.certification_class : "B",
      impact_level: null,
    };
  }
  return {
    certification_class: null,
    impact_level: isImpactLevel(source?.impact_level) ? source.impact_level : "moderate",
  };
}

export function defaultRevisionInput(
  parentRevision?: PackageRevision | null,
): CreateRevisionInput {
  const dataOrigin: DataOrigin =
    parentRevision?.data_origin === "customer_production"
      ? "customer_production"
      : "synthetic";
  const profileId = (parentRevision?.profile_id as ProfileId) ?? "fisma_agency_security";
  return {
    parent_revision_id: parentRevision?.package_revision_id ?? null,
    profile_id: profileId,
    ...profileFieldsForRevision(profileId, parentRevision),
    data_origin: dataOrigin,
    sensitivity: (parentRevision?.sensitivity as Sensitivity) ?? "internal_unclassified",
  };
}
