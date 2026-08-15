import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InformationTypesPanel } from "@/components/ssp-workspace/InformationTypesPanel";
import type { InformationTypes } from "@/sspWorkspaceTypes";

const informationTypes: InformationTypes = {
  status: "unconfirmed",
  entries: [],
  confirmed: false,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("InformationTypesPanel", () => {
  it("requires catalog selection and description before confirming", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          source_id: "nist-sp-800-60-rev2",
          title: "NIST SP 800-60",
          version: "2.0.0",
          reference: "https://example.test/sp800-60",
          information_types: [
            {
              identifier: "D.14.2",
              title: "Financial Management — Grants",
              confidentiality: "moderate",
              integrity: "moderate",
              availability: "moderate",
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onSave = vi.fn();
    render(
      <InformationTypesPanel
        informationTypes={informationTypes}
        evidence={[]}
        onSave={onSave}
      />,
    );

    const confirmButton = screen.getByRole("button", {
      name: "Confirm information types",
    });
    expect(confirmButton).toBeDisabled();

    await waitFor(() => {
      expect(screen.getByLabelText("Catalog information type")).toBeEnabled();
    });
    fireEvent.change(screen.getByLabelText("Catalog information type"), {
      target: { value: "D.14.2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add type" }));
    fireEvent.change(screen.getByLabelText("Information type 1 description"), {
      target: { value: "The system processes grant application data." },
    });

    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    expect(onSave).toHaveBeenCalledWith({
      entries: [
        expect.objectContaining({
          catalogIdentifier: "D.14.2",
          description: "The system processes grant application data.",
        }),
      ],
    });
  });
});
