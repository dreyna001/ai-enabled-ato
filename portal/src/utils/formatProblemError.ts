import { ApiError, type ProblemFieldError } from "@/api/client";
import { humanizeDraftPointer } from "@/utils/draftValidation";
import { problemMessageForCode } from "@/utils/problemMessages";

const GENERIC_VALIDATION_DETAILS = new Set([
  "One or more request fields failed validation.",
  "Request schema invalid",
]);

function formatFieldErrors(fieldErrors: ProblemFieldError[]): string {
  if (fieldErrors.length === 0) {
    return "";
  }
  if (fieldErrors.length === 1) {
    const issue = fieldErrors[0];
    const label = issue.path ? humanizeDraftPointer(issue.path) : "Package";
    return `${label}: ${issue.message}`;
  }
  return fieldErrors
    .map((issue) => {
      const label = issue.path ? humanizeDraftPointer(issue.path) : "Package";
      return `• ${label}: ${issue.message}`;
    })
    .join("\n");
}

export function formatProblemError(err: unknown, fallback = "Unknown error"): string {
  if (!(err instanceof ApiError)) {
    if (err instanceof Error) {
      return err.message;
    }
    return fallback;
  }

  if (err.fieldErrors && err.fieldErrors.length > 0) {
    return formatFieldErrors(err.fieldErrors);
  }

  const detail = err.message.trim();
  const friendlyDetail = problemMessageForCode(err.errorCode, detail);

  if (GENERIC_VALIDATION_DETAILS.has(detail) && friendlyDetail !== detail) {
    return friendlyDetail;
  }

  if (detail && !detail.startsWith(`${err.status}:`)) {
    return friendlyDetail;
  }

  const codeSuffix = err.errorCode ? ` (${err.errorCode})` : "";
  if (err.status === 0) {
    return `${friendlyDetail}${codeSuffix}`;
  }
  return `${friendlyDetail}${codeSuffix}`;
}
