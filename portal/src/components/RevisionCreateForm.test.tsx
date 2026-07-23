import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RevisionCreateForm } from "@/components/RevisionCreateForm";
import type { CreateRevisionInput, PackageRevision } from "@/types";

const readyParent: PackageRevision = {
  package_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  system_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  status: "ready",
  package_preparation_status: "ready_for_external_review",
  revision_version: 2,
  profile_id: "fedramp_20x_program",
  certification_class: "C",
  data_origin: "customer_production",
  sensitivity: "customer_sensitive",
};

afterEach(() => {
  cleanup();
});

function expandCreateForm() {
  fireEvent.click(screen.getByRole("button", { name: "New revision" }));
}

function fillRequiredFismaFields() {
  fireEvent.change(screen.getByLabelText("Profile"), {
    target: { value: "fisma_agency_security" },
  });
  fireEvent.change(screen.getByLabelText("Impact level"), {
    target: { value: "moderate" },
  });
  fireEvent.change(screen.getByLabelText("Data origin"), {
    target: { value: "synthetic" },
  });
  fireEvent.change(screen.getByLabelText("Sensitivity"), {
    target: { value: "internal_unclassified" },
  });
}

describe("RevisionCreateForm collapsed create flow", () => {
  it("is collapsed by default and hides the create form", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => true} />);

    expect(screen.getByRole("button", { name: "New revision" })).toBeInTheDocument();
    expect(
      screen.queryByText(/Set authorization profile and required human attestation/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create revision" })).not.toBeInTheDocument();
  });

  it("expands the create form when New revision is clicked", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => true} />);

    expandCreateForm();

    expect(
      screen.getByText(/Set authorization profile and required human attestation/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Profile")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create revision" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("collapses and resets fields when Cancel is clicked", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => true} />);

    expandCreateForm();
    fillRequiredFismaFields();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "New revision" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument();

    expandCreateForm();
    expect(screen.getByLabelText("Profile")).toHaveValue("");
    expect(screen.getByLabelText("Data origin")).toHaveValue("");
    expect(screen.getByLabelText("Sensitivity")).toHaveValue("");
  });

  it("collapses after a successful create", async () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => boolean>(() => true);

    render(<RevisionCreateForm revisions={[]} onCreate={onCreate} />);
    expandCreateForm();
    fillRequiredFismaFields();
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "New revision" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Create revision" })).not.toBeInTheDocument();
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it("stays expanded after a failed create", async () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => boolean>(() => false);

    render(<RevisionCreateForm revisions={[]} onCreate={onCreate} />);
    expandCreateForm();
    fillRequiredFismaFields();
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledOnce();
    });
    expect(screen.getByRole("button", { name: "Create revision" })).toBeInTheDocument();
    expect(screen.getByLabelText("Profile")).toHaveValue("fisma_agency_security");
  });
});

describe("RevisionCreateForm metadata-first create", () => {
  it("renders required metadata controls and disabled create until valid", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => true} />);
    expandCreateForm();

    expect(
      screen.getByText(/Set authorization profile and required human attestation/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Profile")).toBeInTheDocument();
    expect(screen.getByLabelText("Data origin")).toBeInTheDocument();
    expect(screen.getByLabelText("Sensitivity")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create revision" })).toBeDisabled();
  });

  it("shows certification class for FedRAMP 20x and requires it before create", () => {
    render(<RevisionCreateForm revisions={[]} onCreate={() => true} />);
    expandCreateForm();

    fireEvent.change(screen.getByLabelText("Profile"), {
      target: { value: "fedramp_20x_program" },
    });
    expect(screen.getByLabelText("Certification class")).toBeInTheDocument();
    expect(screen.queryByLabelText("Impact level")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create revision" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Certification class"), {
      target: { value: "B" },
    });
    fireEvent.change(screen.getByLabelText("Data origin"), {
      target: { value: "synthetic" },
    });
    fireEvent.change(screen.getByLabelText("Sensitivity"), {
      target: { value: "internal_unclassified" },
    });
    expect(screen.getByRole("button", { name: "Create revision" })).not.toBeDisabled();
  });

  it("submits full metadata for a new lineage", async () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => boolean>(() => true);

    render(<RevisionCreateForm revisions={[]} onCreate={onCreate} />);
    expandCreateForm();
    fillRequiredFismaFields();
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledOnce();
    });
    expect(onCreate).toHaveBeenCalledWith({
      parent_revision_id: null,
      profile_id: "fisma_agency_security",
      impact_level: "moderate",
      certification_class: null,
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });
  });

  it("does not autofill metadata from a selected parent", async () => {
    const onCreate = vi.fn<(input: CreateRevisionInput) => boolean>(() => true);

    render(<RevisionCreateForm revisions={[readyParent]} onCreate={onCreate} />);
    expandCreateForm();
    fireEvent.change(screen.getByLabelText(/Parent Revision \(Optional\)/i), {
      target: { value: readyParent.package_revision_id },
    });

    expect(screen.getByLabelText("Profile")).toHaveValue("");
    expect(screen.getByLabelText("Data origin")).toHaveValue("");
    expect(screen.getByLabelText("Sensitivity")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Create revision" })).toBeDisabled();

    fillRequiredFismaFields();
    fireEvent.click(screen.getByRole("button", { name: "Create revision" }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledOnce();
    });
    expect(onCreate).toHaveBeenCalledWith({
      parent_revision_id: readyParent.package_revision_id,
      profile_id: "fisma_agency_security",
      certification_class: null,
      impact_level: "moderate",
      data_origin: "synthetic",
      sensitivity: "internal_unclassified",
    });
  });
});
