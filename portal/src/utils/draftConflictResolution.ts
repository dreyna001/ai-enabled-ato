import type { IntakeReportConflictLike } from "@/components/IntakeConflictList";
import type { PackageDraftDocument } from "@/types";
import { cloneDraftDocument } from "@/utils/draftDocument";
import {
  getValueAtJsonPointer,
  setValueAtJsonPointer,
  validateDraftJsonPointer,
  valuesEqualAtPointer,
} from "@/utils/jsonPointer";

const MAX_JSON_VALUE_BYTES = 8000;

const PROHIBITED_NESTED_KEYS = new Set([
  "credential",
  "password",
  "prompt",
  "prompt_payload",
  "prompt_text",
  "raw_model_response",
  "raw_response",
  "secret",
  "storage_key",
  "token",
]);

const CANDIDATE_VALUE_KEYS = ["value", "proposed_value"] as const;

export type DraftIntakeConflict = {
  conflict_id?: string;
  target_pointer: string;
  resolution?: string;
  candidates?: unknown[];
};

export type ConflictCandidateApplyResult =
  | { ok: true; document: PackageDraftDocument }
  | { ok: false; error: string };

function jsonByteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

function containsProhibitedNestedKey(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some((entry) => containsProhibitedNestedKey(entry));
  }
  if (!value || typeof value !== "object") {
    return false;
  }
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (PROHIBITED_NESTED_KEYS.has(key.toLowerCase())) {
      return true;
    }
    if (containsProhibitedNestedKey(child)) {
      return true;
    }
  }
  return false;
}

export function isBoundedConflictValue(value: unknown): boolean {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return jsonByteLength(value) <= MAX_JSON_VALUE_BYTES;
  }
  if (Array.isArray(value) || (value && typeof value === "object")) {
    if (containsProhibitedNestedKey(value)) {
      return false;
    }
    return jsonByteLength(value) <= MAX_JSON_VALUE_BYTES;
  }
  return false;
}

export function extractCandidateValue(candidate: unknown): unknown | null {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return null;
  }
  const record = candidate as Record<string, unknown>;
  for (const key of CANDIDATE_VALUE_KEYS) {
    if (key in record) {
      const value = record[key];
      return isBoundedConflictValue(value) ? value : null;
    }
  }
  return null;
}

export function readDraftIntakeConflicts(
  document: PackageDraftDocument,
): DraftIntakeConflict[] {
  const raw = document.extensions.intake_conflicts;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter(
    (entry): entry is DraftIntakeConflict =>
      Boolean(entry) && typeof entry === "object" && !Array.isArray(entry),
  );
}

export function removeIntakeConflictByPointer(
  document: PackageDraftDocument,
  targetPointer: string,
): PackageDraftDocument {
  const existing = readDraftIntakeConflicts(document);
  const nextConflicts = existing.filter(
    (entry) => entry.target_pointer !== targetPointer,
  );
  if (nextConflicts.length === existing.length) {
    return document;
  }
  return {
    ...document,
    extensions: {
      ...document.extensions,
      intake_conflicts: nextConflicts,
    },
  };
}

export function findReportConflict(
  conflicts: IntakeReportConflictLike[],
  pointer: string,
): IntakeReportConflictLike | undefined {
  return conflicts.find((conflict) => conflict.field === pointer);
}

export function applyConflictCandidateSelection(
  document: PackageDraftDocument,
  conflicts: IntakeReportConflictLike[],
  pointer: string,
  candidateIndex: number,
): ConflictCandidateApplyResult {
  const pointerError = validateDraftJsonPointer(pointer);
  if (pointerError) {
    return { ok: false, error: pointerError.message };
  }

  const conflict = findReportConflict(conflicts, pointer);
  if (!conflict) {
    return { ok: false, error: "Conflict was not found in the intake report." };
  }
  if (
    !Number.isInteger(candidateIndex) ||
    candidateIndex < 0 ||
    candidateIndex >= conflict.values.length
  ) {
    return { ok: false, error: "Selected conflict candidate is out of range." };
  }

  const candidateValue = extractCandidateValue(conflict.values[candidateIndex]);
  if (candidateValue === null) {
    return {
      ok: false,
      error: "Selected candidate does not contain a bounded draft value.",
    };
  }

  let nextDocument = cloneDraftDocument(document);
  const setResult = setValueAtJsonPointer(nextDocument, pointer, candidateValue);
  if (!setResult.ok) {
    return { ok: false, error: setResult.error.message };
  }
  nextDocument = setResult.document;
  nextDocument = removeIntakeConflictByPointer(nextDocument, pointer);
  return { ok: true, document: nextDocument };
}

export function pruneResolvedConflictsAfterEdit(
  previousDocument: PackageDraftDocument,
  nextDocument: PackageDraftDocument,
  conflicts: IntakeReportConflictLike[],
): PackageDraftDocument {
  let updated = nextDocument;
  for (const conflict of conflicts) {
    const pointerError = validateDraftJsonPointer(conflict.field);
    if (pointerError) {
      continue;
    }
    let previousValue: unknown;
    let nextValue: unknown;
    try {
      previousValue = getValueAtJsonPointer(previousDocument, conflict.field);
      nextValue = getValueAtJsonPointer(updated, conflict.field);
    } catch {
      continue;
    }
    if (!valuesEqualAtPointer(previousValue, nextValue)) {
      updated = removeIntakeConflictByPointer(updated, conflict.field);
    }
  }
  return updated;
}

export function isMetadataOnlyConflictField(field: string): boolean {
  return !field.startsWith("/");
}
