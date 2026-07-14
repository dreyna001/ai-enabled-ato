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

export function defaultRevisionInput(
  parentRevision?: PackageRevision | null,
): CreateRevisionInput {
  const dataOrigin: DataOrigin =
    parentRevision?.data_origin === "customer_production"
      ? "customer_production"
      : "synthetic";
  return {
    parent_revision_id: parentRevision?.package_revision_id ?? null,
    profile_id: (parentRevision?.profile_id as ProfileId) ?? "fisma_agency_security",
    certification_class: null,
    impact_level: (parentRevision?.impact_level as CreateRevisionInput["impact_level"]) ?? "moderate",
    data_origin: dataOrigin,
    sensitivity: (parentRevision?.sensitivity as Sensitivity) ?? "internal_unclassified",
  };
}
