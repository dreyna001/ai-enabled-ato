export type PreflightCheckInfo = {
  title: string;
  description: string;
  action?: string;
};

const CHECK_INFO: Record<string, PreflightCheckInfo> = {
  "revision.ready": {
    title: "Revision not sealed for analysis",
    description:
      "The package revision must finish intake and reach ready status before export.",
    action:
      "Complete upload, intake, proposal review, and confirm the package draft.",
  },
  "package.sealed_content": {
    title: "Sealed package content missing",
    description: "No immutable sealed package snapshot exists for this revision.",
    action: "Confirm the package draft after intake so the revision can be sealed.",
  },
  "assessor.inputs_present": {
    title: "Assessor inputs missing",
    description:
      "The sealed package has no assessor-owned inputs. Export requires imported assessor context, not only system evidence.",
    action:
      "Before confirming, upload an assessor attestation or independent assessment artifact. Intake merges assessor_inputs automatically; do not edit them manually in the draft.",
  },
  missing_assessor_inputs: {
    title: "Assessor inputs missing",
    description:
      "Export validation found no assessor_inputs section in the sealed package.",
    action:
      "Upload assessor-owned artifacts during intake, save the draft, then confirm. Create a new revision if the package is already sealed.",
  },
  "privacy.artifacts_present": {
    title: "Privacy artifacts not attached",
    description:
      "The sealed package must record that required privacy artifacts are present.",
    action:
      "Before confirming, upload privacy artifacts with kind Privacy artifact. Intake sets privacy.artifacts_present when the upload succeeds.",
  },
  missing_privacy_artifacts: {
    title: "Privacy artifacts not attached",
    description:
      "Export validation requires privacy.artifacts_present in the sealed package.",
    action:
      "Upload privacy artifacts during intake, save the draft, then confirm.",
  },
  "profile.section_populated": {
    title: "Profile section empty",
    description:
      "The profile-specific section of the sealed package is missing or empty.",
    action:
      "Populate the profile section (FISMA, FedRAMP 20x, or Rev 5) in the package draft during intake confirmation.",
  },
  missing_fedramp_20x_section: {
    title: "FedRAMP 20x section missing",
    description: "The sealed package has no fedramp_20x section.",
    action: "Add fedramp_20x content to the package draft before confirming.",
  },
  hs_009_missing_independent_assessment: {
    title: "Independent assessment missing",
    description:
      "FedRAMP 20x export requires independent_assessment data in the sealed package.",
    action:
      "Upload an assessor attestation or independent assessment import before confirming.",
  },
  missing_ksi_methods: {
    title: "KSI methods missing",
    description: "FedRAMP 20x export requires ksi_methods in the sealed package.",
    action:
      "Complete FedRAMP 20x intake uploads and accepted proposals so ksi_methods is populated before confirming.",
  },
  missing_fedramp_rev5_section: {
    title: "FedRAMP Rev 5 section missing",
    description: "The sealed package has no fedramp_rev5_transition section.",
    action: "Add Rev 5 transition content to the package draft before confirming.",
  },
  package_not_ready: {
    title: "Package not ready for export",
    description: "Sealed package content is required to build an export bundle.",
    action: "Confirm and seal the package revision before creating an export draft.",
  },
};

const REV5_ARTIFACT_LABELS: Record<string, string> = {
  ssp: "System Security Plan (SSP)",
  sap: "Security Assessment Plan (SAP)",
  sar: "Security Assessment Report (SAR)",
  poam: "Plan of Action and Milestones (POA&M)",
};

function titleCaseToken(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function resolvePreflightCheck(
  code: string,
  apiMessage?: string,
): PreflightCheckInfo {
  const known = CHECK_INFO[code];
  if (known) {
    return known;
  }

  if (code.startsWith("missing_rev5_")) {
    const key = code.slice("missing_rev5_".length);
    const label = REV5_ARTIFACT_LABELS[key] ?? titleCaseToken(key);
    return {
      title: `${label} missing`,
      description: `FedRAMP Rev 5 export requires ${label} in the sealed package.`,
      action: `Add ${label} to fedramp_rev5_transition before confirming the revision.`,
    };
  }

  if (code.startsWith("missing_")) {
    const label = titleCaseToken(code.slice("missing_".length));
    return {
      title: `${label} missing`,
      description: apiMessage ?? "Required export content is missing from the sealed package.",
      action:
        "Upload the required artifacts during intake, save the draft, and confirm—or start a new revision if this package is already sealed.",
    };
  }

  if (code.startsWith("structural_invalid_")) {
    const artifact = code.slice("structural_invalid_".length);
    return {
      title: `Invalid ${titleCaseToken(artifact)} structure`,
      description:
        apiMessage ??
        "An export artifact failed structural validation against the profile schema.",
      action: "Correct the underlying package or profile section content before export.",
    };
  }

  if (code.startsWith("schema_unavailable_")) {
    return {
      title: "Schema validation unavailable",
      description:
        apiMessage ??
        "Export could not validate one artifact because its schema is unavailable.",
      action: "Contact an operator or retry after authority/schema assets are installed.",
    };
  }

  if (code.startsWith("hs002_") || code.startsWith("hs_001_")) {
    return {
      title: titleCaseToken(code),
      description:
        apiMessage ??
        "Authority or template-pack readiness is still open for operator review.",
      action:
        "This is an operator hard-stop item; export may proceed only after readiness review closes.",
    };
  }

  return {
    title: titleCaseToken(code),
    description: apiMessage ?? "Resolve this check before export.",
    action: "See the Preflight panel above for the full readiness evaluation.",
  };
}

export function exportNotReadyMessage(blockers: string[]): string {
  if (blockers.length === 0) {
    return "Export readiness blockers remain. Review the Preflight panel above.";
  }
  if (blockers.length === 1) {
    return `Export blocked: ${resolvePreflightCheck(blockers[0]).title}.`;
  }
  return `Export blocked by ${blockers.length} readiness items. See the list below.`;
}
