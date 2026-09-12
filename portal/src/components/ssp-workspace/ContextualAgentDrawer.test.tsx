import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { ContextualAgentDrawer } from "./ContextualAgentDrawer";

const context = {
  targetType: "workspace" as const,
  targetId: "workspace-1",
  label: "Test workspace",
};

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
        Open drawer
      </button>
      {open ? (
        <ContextualAgentDrawer
          context={context}
          patches={[]}
          restoreFocusRef={triggerRef}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("ContextualAgentDrawer", () => {
  it("focuses the dialog, exposes accessible naming, and closes on Escape", async () => {
    render(<DrawerHarness />);
    const trigger = screen.getByRole("button", { name: "Open drawer" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("SSP assistant");
    expect(dialog).toHaveAccessibleDescription("Context: Test workspace");
    expect(screen.getByLabelText("Agent instruction")).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
