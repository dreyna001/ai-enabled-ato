import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ControlWorkbench } from "@/components/ssp-workspace/ControlWorkbench";
import {
  DEFAULT_CONTROL_RESPONSE_OPTIONS,
  type ControlStatement,
} from "@/sspWorkspaceTypes";

const controls: ControlStatement[] = [
  {
    id: "AC-2",
    title: "Account Management",
    family: "AC",
    state: "partial",
    implementationStatus: "implemented",
    responsibility: "system_specific",
    statement: "Accounts are managed centrally.",
    evidenceLinks: [],
  },
];

afterEach(cleanup);

describe("ControlWorkbench", () => {
  it("renders profile-driven selects for status and responsibility", () => {
    const onSave = vi.fn();
    render(
      <ControlWorkbench
        controls={controls}
        controlResponse={DEFAULT_CONTROL_RESPONSE_OPTIONS}
        selectedControlId="AC-2"
        onSelectControl={() => undefined}
        onSave={onSave}
        onOpenAgent={() => undefined}
      />,
    );

    const statusSelect = screen.getByLabelText("Implementation status");
    const responsibilitySelect = screen.getByLabelText(
      "Responsibility and inheritance",
    );

    expect(statusSelect.tagName).toBe("SELECT");
    expect(responsibilitySelect.tagName).toBe("SELECT");
    expect(
      Array.from(statusSelect.querySelectorAll("option")).map(
        (option) => option.textContent,
      ),
    ).toContain("Implemented");
    expect(
      Array.from(responsibilitySelect.querySelectorAll("option")).map(
        (option) => option.textContent,
      ),
    ).toContain("System Specific");

    fireEvent.change(statusSelect, { target: { value: "planned" } });
    fireEvent.click(screen.getByRole("button", { name: /save control/i }));

    expect(onSave).toHaveBeenCalledWith({
      controlId: "AC-2",
      implementationStatus: "planned",
      responsibility: "system_specific",
      statement: "Accounts are managed centrally.",
    });
  });

  it("keeps legacy values visible when they are outside profile options", () => {
    render(
      <ControlWorkbench
        controls={[
          {
            ...controls[0],
            implementationStatus: "Implemented",
            responsibility: "Hybrid",
          },
        ]}
        controlResponse={DEFAULT_CONTROL_RESPONSE_OPTIONS}
        selectedControlId="AC-2"
        onSelectControl={() => undefined}
        onOpenAgent={() => undefined}
      />,
    );

    const statusSelect = screen.getByLabelText("Implementation status");
    expect(statusSelect).toHaveValue("Implemented");
    expect(
      Array.from(statusSelect.querySelectorAll("option")).map(
        (option) => option.value,
      ),
    ).toContain("Implemented");
  });
});
