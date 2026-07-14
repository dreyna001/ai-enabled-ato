const FILENAME_PATTERN =
  /^attachment;\s*filename="([A-Za-z0-9][A-Za-z0-9._-]{0,127}\.zip)"$/i;

/** Parse a server Content-Disposition attachment header into a safe local filename. */
export function parseContentDispositionFilename(
  header: string | null,
): string | null {
  if (!header) {
    return null;
  }
  const match = FILENAME_PATTERN.exec(header.trim());
  if (!match) {
    return null;
  }
  return match[1];
}

/** Sanitize a display label so hostile upload names render as plain text only. */
export function sanitizeDisplayFilename(name: string): string {
  return name.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 255);
}
