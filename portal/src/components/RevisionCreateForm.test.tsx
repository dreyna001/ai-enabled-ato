import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RevisionCreateForm } from "@/components/RevisionCreateForm";
import type { CreateRevisionInput, PackageRevision } from "@/types";

const readyParent: PackageRevision = {
  package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  status: "ready",
  package_preparation_status: "ready_for_external_review",
  revision_version: 2,
  profile_id: "fedramp_20x_program",
  data_origin: "synthetic",
  sensitivity: "internal_unclassified",
};

afterEach(() => {
  cleanup();
});

describe("RevisionCreateForm upload-first create", () => {
  it("renders upload-first guidance and no metadata controls", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => undefined} />);

    expect(
      screen.getByText(/Create a revision first, then upload package files/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Parent Revision \(Optional\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create revision" })).toBeInTheDocument();
    expect(document.querySelector("#profile-id")).toBeNull();
    expect(document.querySelector("#data-origin")).toBeNull();
    expect(document.querySelector("#sensitivity")).toBeNull();
    expect(document.querySelector("#certification-class")).toBeNull();
    expect(document.querySelector("#impact-level")).toBeNull();
  });

  it("submits only parent_revision_id null for a new lineage", () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => void>();

    render(<RevisionCreateForm revisions={[readyParent]} onCreate={onCreate} />);
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    expect(onCreate).toHaveBeenCalledOnce();
    expect(onCreate).toHaveBeenCalledWith({ parent_revision_id: null });
  });

  it("submits the selected ready parent revision id", () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => void>();

    render(<RevisionCreateForm revisions={[readyParent]} onCreate={onCreate} />);
    fireEvent.change(screen.getByLabelText(/Parent Revision \(Optional\)/i), {
      target: { value: readyParent.package_revision_id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    expect(onCreate).toHaveBeenCalledOnce();
    expect(onCreate).toHaveBeenCalledWith({
      parent_revision_id: readyParent.package_revision_id,
    });
  });
});
