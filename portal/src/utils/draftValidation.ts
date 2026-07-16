import type { PackageDraftDocument, ProfileId } from "@/types";

export type DraftFieldIssue = {
  pointer: string;
  message: string;
  tab: "package" | "system" | "contacts" | "controls" | "privacy" | "profile";
};

const IMPACT_LEVELS = new Set(["low", "moderate", "high"]);

const IMPLEMENTATION_STATUSES = new Set([
  "implemented",
  "partial",
  "planned",
  "not_applicable",
  "not_implemented",
]);

const UNSUPPORTED_AUTHORIZATION_PATHS = new Set([
  "classified",
  "ccri",
  "dod",
  "dod_rmf",
  "emass",
  "fedramp_agency_certification",
  "ic",
  "intelligence_community",
  "intelligence",
  "privacy",
]);

function normalizeAuthorizationPath(value: string): string {
  return value.trim().toLowerCase().replace(/-/g, "_").replace(/ /g, "_");
}

function isSupportedAuthorizationPath(value: string): boolean {
  const normalized = normalizeAuthorizationPath(value);
  if (!normalized) {
    return true;
  }
  if (UNSUPPORTED_AUTHORIZATION_PATHS.has(normalized)) {
    return false;
  }
  for (const blocked of UNSUPPORTED_AUTHORIZATION_PATHS) {
    if (
      normalized.startsWith(`${blocked}_`) ||
      normalized.endsWith(`_${blocked}`) ||
      normalized.includes(`_${blocked}_`)
    ) {
      return false;
    }
  }
  return /^[a-z0-9][a-z0-9_./-]{0,499}$/.test(normalized);
}

function expectedAuthorizationPath(profileId: ProfileId): string {
  if (profileId === "fedramp_20x_program" || profileId === "fedramp_rev5_transition") {
    return "fedramp";
  }
  return "agency";
}

export function authorizationPathLabel(profileId: ProfileId): string {
  return expectedAuthorizationPath(profileId) === "agency" ? "Agency" : "FedRAMP";
}

export function normalizeDraftDocumentForProfile(
  document: PackageDraftDocument,
  options?: { revisionImpactLevel?: string | null },
): PackageDraftDocument {
  const profileId = document.package.profile_id;
  const system = { ...document.system };

  if (!impactLevelEditableForProfile(profileId)) {
    system.impact_level = null;
  } else if (
    system.impact_level === null &&
    options?.revisionImpactLevel &&
    IMPACT_LEVELS.has(options.revisionImpactLevel)
  ) {
    system.impact_level = options.revisionImpactLevel;
  }

  system.authorization_path = expectedAuthorizationPath(profileId);

  return {
    ...document,
    system,
  };
}

function requiresImpactLevel(profileId: ProfileId): boolean {
  return profileId !== "fedramp_20x_program";
}

export function impactLevelEditableForProfile(profileId: ProfileId): boolean {
  return requiresImpactLevel(profileId);
}

const FEDRAMP_20X_IMPACT_LEVEL_MESSAGE =
  "FedRAMP 20x does not use FIPS 199 impact level in the draft. Leave this empty; certification class (B or C) is set on the revision.";

function resolvedImpactLevel(
  document: PackageDraftDocument,
  revisionImpactLevel?: string | null,
): string | null {
  return document.system.impact_level ?? revisionImpactLevel ?? null;
}

export function humanizeDraftPointer(pointer: string): string {
  const labels: Record<string, string> = {
    "/package/title": "Package title",
    "/package/prepared_for": "Prepared for",
    "/package/profile_id": "Profile",
    "/system/display_name": "Display name",
    "/system/authorization_boundary": "Authorization boundary",
    "/system/mission_summary": "Mission summary",
    "/system/impact_level": "Impact level",
    "/system/authorization_path": "Authorization path",
    "/privacy/scope_notice": "Privacy scope notice",
  };
  if (labels[pointer]) {
    return labels[pointer];
  }
  const controlMatch = pointer.match(/^\/security_controls\/([^/]+)\/(.+)$/);
  if (controlMatch) {
    const field = controlMatch[2].replace(/~1/g, "/");
    return `Control ${controlMatch[1]} · ${field.replace(/_/g, " ")}`;
  }
  const segment = pointer.split("/").filter(Boolean).at(-1);
  return segment ? segment.replace(/_/g, " ") : "Field";
}

export function validateDraftForSeal(
  document: PackageDraftDocument,
  options?: { revisionImpactLevel?: string | null },
): DraftFieldIssue[] {
  const issues: DraftFieldIssue[] = [];
  const profileId = document.package.profile_id;

  if (!document.package.title.trim()) {
    issues.push({
      pointer: "/package/title",
      message: "Enter a package title before saving or confirming.",
      tab: "package",
    });
  }

  if (!document.system.display_name.trim()) {
    issues.push({
      pointer: "/system/display_name",
      message: "Enter the system display name.",
      tab: "system",
    });
  }

  if (!document.system.authorization_boundary.trim()) {
    issues.push({
      pointer: "/system/authorization_boundary",
      message: "Describe the authorization boundary.",
      tab: "system",
    });
  }

  if (!document.system.mission_summary.trim()) {
    issues.push({
      pointer: "/system/mission_summary",
      message: "Provide a mission summary.",
      tab: "system",
    });
  }

  if (requiresImpactLevel(profileId)) {
    const impactLevel = resolvedImpactLevel(document, options?.revisionImpactLevel);
    if (!impactLevel || !IMPACT_LEVELS.has(impactLevel)) {
      issues.push({
        pointer: "/system/impact_level",
        message:
          "Select low, moderate, or high impact, then save the draft before confirming.",
        tab: "system",
      });
    }
  } else if (document.system.impact_level !== null) {
    // Defense in depth if normalization was bypassed.
    issues.push({
      pointer: "/system/impact_level",
      message: FEDRAMP_20X_IMPACT_LEVEL_MESSAGE,
      tab: "system",
    });
  }

  const authorizationPath = document.system.authorization_path?.trim() ?? "";
  const profileAuthorizationPath = expectedAuthorizationPath(profileId);
  if (!authorizationPath) {
    issues.push({
      pointer: "/system/authorization_path",
      message: `Authorization path is required (expected "${profileAuthorizationPath}" for this profile).`,
      tab: "system",
    });
  } else if (normalizeAuthorizationPath(authorizationPath) !== profileAuthorizationPath) {
    issues.push({
      pointer: "/system/authorization_path",
      message: `Authorization path must be "${profileAuthorizationPath}" for this profile.`,
      tab: "system",
    });
  } else if (!isSupportedAuthorizationPath(authorizationPath)) {
    issues.push({
      pointer: "/system/authorization_path",
      message:
        "This authorization path is outside product scope. Use agency or fedramp for supported profiles.",
      tab: "system",
    });
  }

  for (const [controlId, control] of Object.entries(document.security_controls)) {
    const pointerBase = `/security_controls/${controlId.replace(/\//g, "~1")}`;
    if (!IMPLEMENTATION_STATUSES.has(control.implementation_status)) {
      issues.push({
        pointer: `${pointerBase}/implementation_status`,
        message: `Choose a valid implementation status for ${controlId}.`,
        tab: "controls",
      });
    }
    if (!control.implementation_statement.trim()) {
      issues.push({
        pointer: `${pointerBase}/implementation_statement`,
        message: `Add an implementation statement for ${controlId}.`,
        tab: "controls",
      });
    }
  }

  return issues;
}

export function formatDraftValidationIssues(issues: DraftFieldIssue[]): string {
  if (issues.length === 0) {
    return "";
  }
  if (issues.length === 1) {
    const issue = issues[0];
    return `${humanizeDraftPointer(issue.pointer)}: ${issue.message}`;
  }
  return issues
    .map((issue) => `• ${humanizeDraftPointer(issue.pointer)}: ${issue.message}`)
    .join("\n");
}

export function lookupDraftIssue(
  issues: DraftFieldIssue[],
  pointer: string,
): DraftFieldIssue | undefined {
  return issues.find((issue) => issue.pointer === pointer);
}
