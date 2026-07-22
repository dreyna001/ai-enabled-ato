import { ApiError } from "@/api/client";

export function formatApiError(err: unknown, fallback = "Unknown error"): string {
  if (err instanceof ApiError) {
    const codeSuffix = err.errorCode ? ` (${err.errorCode})` : "";
    const fieldSuffix =
      err.fieldErrors && err.fieldErrors.length > 0
        ? `: ${err.fieldErrors
            .map((field) =>
              field.path ? `${field.path} — ${field.message}` : field.message,
            )
            .join("; ")}`
        : "";
    if (err.status === 0) {
      return `${err.message}${codeSuffix}${fieldSuffix}`;
    }
    return `${err.status}: ${err.message}${codeSuffix}${fieldSuffix}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return fallback;
}
