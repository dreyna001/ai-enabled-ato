import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import { formatApiError } from "@/utils/formatApiError";

describe("formatApiError", () => {
  it("formats transport failures without a misleading status prefix", () => {
    expect(formatApiError(new ApiError(0, "Request timed out.", "timeout"))).toBe(
      "Request timed out.",
    );
  });

  it("includes HTTP status for API failures", () => {
    expect(
      formatApiError(
        new ApiError(403, "Authorization denied.", "http", "authorization_denied"),
      ),
    ).toBe("403: Authorization denied. (authorization_denied)");
  });

  it("uses a friendly message for workspace approval gates", () => {
    expect(
      formatApiError(
        new ApiError(
          422,
          "workspace_not_reviewable",
          "http",
          "workspace_not_reviewable",
        ),
      ),
    ).toContain("not ready to approve");
  });

  it("prefers field errors over generic validation detail", () => {
    expect(
      formatApiError(
        new ApiError(
          422,
          "One or more request fields failed validation.",
          "http",
          "request_schema_invalid",
          [
            {
              path: "confidentiality_evidence.0.artifact_id",
              code: "uuid_parsing",
              message: "Input should be a valid UUID",
            },
          ],
        ),
      ),
    ).toBe("Confidentiality evidence: Input should be a valid UUID");
  });
});
