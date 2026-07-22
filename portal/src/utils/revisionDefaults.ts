import type { DataOrigin, ProfileId, Sensitivity } from "@/types";

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
