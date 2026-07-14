const REVIEW_KEY_PREFIX = "ato-portal-review:";

export function loadStoredReviewRevisionId(runId: string): string | null {
  try {
    return sessionStorage.getItem(`${REVIEW_KEY_PREFIX}${runId}`);
  } catch {
    return null;
  }
}

export function saveStoredReviewRevisionId(
  runId: string,
  reviewRevisionId: string,
): void {
  try {
    sessionStorage.setItem(`${REVIEW_KEY_PREFIX}${runId}`, reviewRevisionId);
  } catch {
    // ignore quota / privacy mode
  }
}

export function clearStoredReviewRevisionId(runId: string): void {
  try {
    sessionStorage.removeItem(`${REVIEW_KEY_PREFIX}${runId}`);
  } catch {
    // ignore
  }
}
