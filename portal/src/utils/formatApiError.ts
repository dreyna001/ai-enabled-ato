import { ApiError } from "@/api/client";

export function formatApiError(err: unknown, fallback = "Unknown error"): string {
  if (err instanceof ApiError) {
    const codeSuffix = err.errorCode ? ` (${err.errorCode})` : "";
    if (err.status === 0) {
      return `${err.message}${codeSuffix}`;
    }
    return `${err.status}: ${err.message}${codeSuffix}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return fallback;
}
