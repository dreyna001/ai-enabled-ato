/** Map stable backend error codes to operator-facing portal messages. */
const PROBLEM_MESSAGES: Record<string, string> = {
  authorization_denied: "You do not have permission for this action.",
  self_approval_denied: "Export approval requires a different user (separation of duty).",
  export_expired: "This export approval has expired. Submit a new export draft.",
  approval_expired: "This approval window has expired.",
  approval_already_decided: "This approval was already decided.",
  approval_payload_mismatch: "Export payload changed. Create a new export draft.",
  illegal_state_transition: "This action is not allowed in the current workflow state.",
  review_incomplete: "Resolve every matrix disposition before submitting review.",
  review_already_submitted: "This analysis run already has a submitted review revision.",
  export_not_ready:
    "Export readiness blockers remain. Review the export blockers listed in Preflight or below.",
  review_not_submitted: "Submit the review revision before creating an export draft.",
  etag_mismatch: "The server version changed. Reload and try again.",
  if_match_required: "Missing concurrency token. Reload and try again.",
  reconciliation_required: "Operator reconciliation is required before continuing.",
  package_not_ready: "Package is not ready for export.",
  request_schema_invalid:
    "Fix the highlighted package fields before saving or confirming.",
  unsupported_authorization_path:
    "Authorization path is outside product scope. Use agency or fedramp for supported profiles.",
};

export function problemMessageForCode(code: string | undefined, fallback: string): string {
  if (!code) {
    return fallback;
  }
  return PROBLEM_MESSAGES[code] ?? fallback;
}
