import { describe, expect, it } from "vitest";
import {
  parseContentDispositionFilename,
  sanitizeDisplayFilename,
} from "@/utils/downloadFilename";

describe("downloadFilename utilities", () => {
  it("parses valid Content-Disposition attachment filenames", () => {
    expect(
      parseContentDispositionFilename('attachment; filename="ato-export-abc123.zip"'),
    ).toBe("ato-export-abc123.zip");
  });

  it("rejects path traversal and hostile filename patterns", () => {
    expect(parseContentDispositionFilename('attachment; filename="../evil.zip"')).toBeNull();
    expect(parseContentDispositionFilename('attachment; filename="x<script>.zip"')).toBeNull();
    expect(parseContentDispositionFilename(null)).toBeNull();
  });

  it("sanitizes display filenames for text rendering", () => {
    expect(sanitizeDisplayFilename("safe-report.json")).toBe("safe-report.json");
    expect(sanitizeDisplayFilename("x\u0000<script>alert(1)</script>.pdf")).not.toContain("\u0000");
  });
});
