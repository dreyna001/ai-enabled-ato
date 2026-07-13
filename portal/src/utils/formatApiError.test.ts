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
});
