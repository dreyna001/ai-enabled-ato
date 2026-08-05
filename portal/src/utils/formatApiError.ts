import { ApiError } from "@/api/client";

const FRIENDLY_ERROR_CODE_MESSAGES: Record<string, string> = {
  workspace_not_reviewable:
    "This revision is not ready to approve yet. Open Review and Export and resolve every item marked Attention, including evidence links on agent-drafted controls.",
};

export function formatApiError(err: unknown, fallback = "Unknown error"): string {
  if (err instanceof ApiError) {
    const friendly =
      err.errorCode && FRIENDLY_ERROR_CODE_MESSAGES[err.errorCode];
    const message = friendly ?? err.message;
    const codeSuffix =
      !friendly && err.errorCode ? ` (${err.errorCode})` : "";
    const fieldSuffix =
      err.fieldErrors && err.fieldErrors.length > 0
        ? `: ${err.fieldErrors
            .map((field) =>
              field.path ? `${field.path} — ${field.message}` : field.message,
            )
            .join("; ")}`
        : "";
    if (err.status === 0) {
      return `${message}${codeSuffix}${fieldSuffix}`;
    }
    return friendly
      ? message
      : `${err.status}: ${message}${codeSuffix}${fieldSuffix}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return fallback;
}
