import { describe, expect, it } from "vitest";
import {
  prepareUploadFile,
  resolveUploadMediaType,
  validateUploadFile,
} from "@/utils/artifactKinds";

function makeFile(name: string, type: string, contents = "demo"): File {
  return new File([contents], name, { type });
}

describe("resolveUploadMediaType", () => {
  it("maps markdown files when the browser sends an empty or generic type", () => {
    expect(resolveUploadMediaType(makeFile("README.md", ""))).toBe("text/markdown");
    expect(resolveUploadMediaType(makeFile("README.md", "application/octet-stream"))).toBe(
      "text/markdown",
    );
  });

  it("prefers markdown over plain text for .md files", () => {
    expect(resolveUploadMediaType(makeFile("policy.md", "text/plain"))).toBe("text/markdown");
  });

  it("keeps a supported browser type when it already matches", () => {
    expect(resolveUploadMediaType(makeFile("manifest.json", "application/json"))).toBe(
      "application/json",
    );
  });
});

describe("validateUploadFile", () => {
  it("accepts markdown evidence files", () => {
    expect(validateUploadFile(makeFile("note.md", ""))).toBeNull();
  });

  it("rejects unsupported extensions", () => {
    expect(validateUploadFile(makeFile("archive.zip", "application/zip"))).toMatch(
      /not supported/i,
    );
  });
});

describe("prepareUploadFile", () => {
  it("re-wraps the file with the resolved media type", () => {
    const prepared = prepareUploadFile(makeFile("README.md", ""));
    expect(prepared.type).toBe("text/markdown");
    expect(prepared.name).toBe("README.md");
  });
});
