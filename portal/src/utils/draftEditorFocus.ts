import type { DraftFieldIssue } from "@/utils/draftValidation";
import { lookupDraftIssue, validateDraftForSeal } from "@/utils/draftValidation";
import type { PackageDraftDocument } from "@/types";
import { isValidJsonPointer } from "@/utils/jsonPointer";

const EDITABLE_POINTER_PATTERNS: RegExp[] = [
  /^\/package\/title$/,
  /^\/package\/prepared_for$/,
  /^\/system\/display_name$/,
  /^\/system\/authorization_boundary$/,
  /^\/system\/mission_summary$/,
  /^\/system\/impact_level$/,
  /^\/privacy\/scope_notice$/,
  /^\/security_controls\/[^/]+\/implementation_status$/,
  /^\/security_controls\/[^/]+\/implementation_statement$/,
];

export function isEditableDraftPointer(pointer: string): boolean {
  if (!isValidJsonPointer(pointer)) {
    return false;
  }
  return EDITABLE_POINTER_PATTERNS.some((pattern) => pattern.test(pointer));
}

export function tabForDraftPointer(
  pointer: string,
  document?: PackageDraftDocument,
): DraftFieldIssue["tab"] | null {
  if (document) {
    const issue = lookupDraftIssue(validateDraftForSeal(document), pointer);
    if (issue) {
      return issue.tab;
    }
  }

  if (pointer.startsWith("/package/")) {
    return "package";
  }
  if (pointer.startsWith("/system/")) {
    return "system";
  }
  if (pointer.startsWith("/privacy/")) {
    return "privacy";
  }
  if (pointer.startsWith("/security_controls/")) {
    return "controls";
  }
  return null;
}

export function focusDraftField(pointer: string): boolean {
  const wrapper = document.querySelector<HTMLElement>(
    `[data-draft-pointer="${CSS.escape(pointer)}"]`,
  );
  if (!wrapper) {
    return false;
  }
  const focusable =
    wrapper.querySelector<HTMLElement>("input, textarea, select, button") ?? wrapper;
  wrapper.scrollIntoView?.({ block: "center", behavior: "smooth" });
  focusable.focus({ preventScroll: true });
  return true;
}
