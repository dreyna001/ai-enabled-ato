import { ApiError } from "@/api/client";
import { formatSspFieldErrors } from "@/utils/sspFieldErrors";

const FRIENDLY_ERROR_CODE_MESSAGES: Record<string, string> = {
  workspace_not_reviewable:
    "This revision is not ready to approve yet. Open Review and Export and resolve every item marked Attention, including evidence links on agent-drafted controls.",
  request_schema_invalid:
    "Fix the highlighted categorization fields and try again.",
};

const GENERIC_VALIDATION_DETAILS = new Set([
  "One or more request fields failed validation.",
  "Request schema invalid",
]);

export function formatApiError(err: unknown, fallback = "Unknown error"): string {
  if (err instanceof ApiError) {
    const friendly =
      err.errorCode && FRIENDLY_ERROR_CODE_MESSAGES[err.errorCode];
    const message = friendly ?? err.message;
    const fieldDetail =
      err.fieldErrors && err.fieldErrors.length > 0
        ? formatSspFieldErrors(err.fieldErrors)
        : "";
    const codeSuffix =
      !friendly && err.errorCode ? ` (${err.errorCode})` : "";
    if (fieldDetail) {
      return fieldDetail;
    }
    if (
      friendly &&
      err.errorCode === "request_schema_invalid" &&
      GENERIC_VALIDATION_DETAILS.has(err.message.trim())
    ) {
      return friendly;
    }
    if (err.status === 0) {
      return `${message}${codeSuffix}`;
    }
    return friendly
      ? message
      : `${err.status}: ${message}${codeSuffix}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return fallback;
}
