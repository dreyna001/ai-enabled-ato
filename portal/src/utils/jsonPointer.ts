const JSON_POINTER_PATTERN = /^(\/([^~/]|~[01])*)*$/;
const MAX_POINTER_LENGTH = 1000;

const PROTOTYPE_POLLUTION_SEGMENTS = new Set(["__proto__", "constructor", "prototype"]);

export type JsonPointerError = {
  code:
    | "empty_pointer"
    | "non_absolute_pointer"
    | "invalid_pointer"
    | "human_only_field"
    | "missing_parent"
    | "type_mismatch";
  message: string;
};

export function decodeJsonPointerSegment(segment: string): string {
  return segment.replace(/~1/g, "/").replace(/~0/g, "~");
}

export function pointerSegments(pointer: string): string[] {
  if (!pointer.startsWith("/")) {
    return [];
  }
  if (pointer === "/") {
    return [""];
  }
  return pointer.slice(1).split("/");
}

export function isValidJsonPointer(pointer: string): boolean {
  if (!pointer || pointer.length > MAX_POINTER_LENGTH) {
    return false;
  }
  if (!JSON_POINTER_PATTERN.test(pointer)) {
    return false;
  }
  return pointerSegments(pointer).every(
    (segment) => !PROTOTYPE_POLLUTION_SEGMENTS.has(decodeJsonPointerSegment(segment)),
  );
}

export function pointerTargetsHumanOnlyField(pointer: string): boolean {
  const humanOnly = new Set(["data_origin", "sensitivity"]);
  return pointerSegments(pointer).some((segment) =>
    humanOnly.has(decodeJsonPointerSegment(segment)),
  );
}

export function validateDraftJsonPointer(pointer: string): JsonPointerError | null {
  if (!pointer) {
    return { code: "empty_pointer", message: "Conflict field pointer is empty." };
  }
  if (!pointer.startsWith("/")) {
    return {
      code: "non_absolute_pointer",
      message: "Conflict field must be an absolute JSON pointer.",
    };
  }
  if (!isValidJsonPointer(pointer)) {
    return {
      code: "invalid_pointer",
      message: "Conflict field pointer is invalid or uses forbidden segments.",
    };
  }
  if (pointerTargetsHumanOnlyField(pointer)) {
    return {
      code: "human_only_field",
      message: "Revision metadata attestation fields cannot be changed through draft edits.",
    };
  }
  return null;
}

export function getValueAtJsonPointer(root: unknown, pointer: string): unknown {
  const validationError = validateDraftJsonPointer(pointer);
  if (validationError) {
    throw new Error(validationError.message);
  }
  if (pointer === "/") {
    return root;
  }
  let current: unknown = root;
  for (const segment of pointerSegments(pointer)) {
    const key = decodeJsonPointerSegment(segment);
    if (Array.isArray(current)) {
      const index = Number(key);
      if (!Number.isInteger(index) || index < 0 || index >= current.length) {
        throw new Error("Conflict field pointer references a missing array index.");
      }
      current = current[index];
      continue;
    }
    if (!current || typeof current !== "object") {
      throw new Error("Conflict field pointer references a missing parent object.");
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

export function setValueAtJsonPointer<T extends Record<string, unknown>>(
  root: T,
  pointer: string,
  value: unknown,
): { ok: true; document: T } | { ok: false; error: JsonPointerError } {
  const validationError = validateDraftJsonPointer(pointer);
  if (validationError) {
    return { ok: false, error: validationError };
  }
  const segments = pointerSegments(pointer);
  if (segments.length === 0) {
    return {
      ok: false,
      error: { code: "empty_pointer", message: "Conflict field pointer is empty." },
    };
  }

  const document = structuredClone(root);
  let current: unknown = document;

  for (let index = 0; index < segments.length; index += 1) {
    const key = decodeJsonPointerSegment(segments[index]!);
    const isLast = index === segments.length - 1;

    if (Array.isArray(current)) {
      const arrayIndex = Number(key);
      if (!Number.isInteger(arrayIndex) || arrayIndex < 0 || arrayIndex >= current.length) {
        return {
          ok: false,
          error: {
            code: "missing_parent",
            message: "Conflict field pointer references a missing array index.",
          },
        };
      }
      if (isLast) {
        current[arrayIndex] = value;
        return { ok: true, document };
      }
      current = current[arrayIndex];
      continue;
    }

    if (!current || typeof current !== "object") {
      return {
        ok: false,
        error: {
          code: "missing_parent",
          message: "Conflict field pointer references a missing parent object.",
        },
      };
    }

    const record = current as Record<string, unknown>;
    if (isLast) {
      record[key] = value;
      return { ok: true, document };
    }

    if (!(key in record)) {
      return {
        ok: false,
        error: {
          code: "missing_parent",
          message: "Conflict field pointer references a missing parent path.",
        },
      };
    }

    current = record[key];
  }

  return {
    ok: false,
    error: { code: "invalid_pointer", message: "Conflict field pointer is invalid." },
  };
}

export function valuesEqualAtPointer(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
