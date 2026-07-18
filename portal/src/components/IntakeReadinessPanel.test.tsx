import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  IntakeConflictList,
  sanitizeConflictCandidate,
} from "@/components/IntakeConflictList";
import {
  IntakeReadinessPanel,
  type IntakeReportLike,
} from "@/components/IntakeReadinessPanel";

const REVISION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ARTIFACT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function buildReport(overrides: Partial<IntakeReportLike> = {}): IntakeReportLike {
  return {
    package_revision_id: REVISION_ID,
    revision_version: 1,
    status: "awaiting_confirmation",
    intake_stage: "awaiting_human_review",
    files: [
      {
        artifact_id: ARTIFACT_ID,
        display_filename: "ssp-section-1.pdf",
        sha256: "c".repeat(64),
        size_bytes: 2048,
        artifact_kind: "source_artifact",
        malware_scan_status: "clean",
        extraction_status: "succeeded",
        uploaded_at: "2026-07-17T12:00:00Z",
      },
    ],
    human_attestation: {
      data_origin: "missing",
      sensitivity: "present",
    },
    suggested_metadata: {
      profile_id: "fedramp_20x_program",
      certification_class: "C",
      impact_level: "moderate",
    },
    gaps: [
      {
        code: "missing_boundary_statement",
        message: "System boundary statement was not found in uploaded artifacts.",
      },
    ],
    conflicts: [
      {
        field: "/system/system_name",
        values: [
          {
            value: "Cloud Platform Alpha",
            display_filename: "ssp-section-1.pdf",
            extraction_method: "structured_extract",
            source_artifact_id: ARTIFACT_ID,
            source_locator: { page: 2, segment_index: 0 },
          },
          {
            value: "Platform Alpha Cloud",
            display_filename: "boundary.docx",
            extraction_method: "structured_extract",
            source_artifact_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          },
        ],
      },
    ],
    omitted_chunks: [
      {
        artifact_id: ARTIFACT_ID,
        segment_id: "seg-001",
      },
    ],
    context_complete: false,
    map_steps: [
      {
        step_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        step_key: "metadata_map",
        status: "completed",
        validation_outcome: "accepted",
        llm_call_count: 2,
        error_code: null,
      },
    ],
    confirmation: {
      allowed: false,
      blockers: ["unresolved_intake_conflicts"],
    },
    generated_at: "2026-07-17T12:05:00Z",
    ...overrides,
  };
}

afterEach(cleanup);

describe("IntakeReadinessPanel states", () => {
  it("renders loading state with accessible busy region", () => {
    render(<IntakeReadinessPanel loading report={null} />);
    expect(screen.getByLabelText(/Loading intake readiness/i)).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByText(/Loading intake report/i)).toBeInTheDocument();
  });

  it("renders error state with alert text and retry", () => {
    const onRetry = vi.fn();
    render(
      <IntakeReadinessPanel
        error="Intake report unavailable for this revision."
        onRetry={onRetry}
        report={null}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/Intake report unavailable/i);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders empty state when no report is supplied", () => {
    render(<IntakeReadinessPanel report={null} />);
    expect(screen.getByText("Intake report unavailable")).toBeInTheDocument();
  });
});

describe("IntakeReadinessPanel report content", () => {
  it("renders inventory, gaps, suggestions, and MAP status", () => {
    render(<IntakeReadinessPanel report={buildReport()} />);
    expect(screen.getByRole("heading", { name: "Intake readiness" })).toBeInTheDocument();
    expect(screen.getByText(/1 file\(s\) uploaded/i)).toBeInTheDocument();
    expect(screen.getByText("ssp-section-1.pdf")).toBeInTheDocument();
    expect(
      screen.getByText(/System boundary statement was not found/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Fedramp 20x Program/i)).toBeInTheDocument();
    expect(screen.getByText("metadata_map")).toBeInTheDocument();
    expect(screen.getByText(/Omitted chunks/i)).toBeInTheDocument();
  });

  it("shows context incomplete as a visible gap without inventing confirm blockers", () => {
    render(
      <IntakeReadinessPanel
        report={buildReport({
          context_complete: false,
          confirmation: { allowed: true, blockers: [] },
        })}
      />,
    );
    expect(screen.getByText("Incomplete")).toBeInTheDocument();
    expect(
      screen.getByText(/Intake did not read all relevant context/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/No confirmation blockers reported/i)).toBeInTheDocument();
  });

  it("shows confirmation blockers only from backend confirmation.blockers", () => {
    render(<IntakeReadinessPanel report={buildReport()} />);
    expect(screen.getByText("unresolved_intake_conflicts")).toBeInTheDocument();
  });

  it("shows human attestation present/missing without suggested attestation values", () => {
    render(<IntakeReadinessPanel report={buildReport()} />);
    expect(screen.getByText("Human attestation")).toBeInTheDocument();
    expect(screen.getByText("Provided by operator")).toBeInTheDocument();
    expect(screen.getByText("Not yet provided")).toBeInTheDocument();
    expect(screen.queryByText(/synthetic/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/customer_production/i)).not.toBeInTheDocument();
  });
});

describe("IntakeReadinessPanel conflict callbacks", () => {
  it("forwards candidate selection and manual edit callbacks", () => {
    const onSelectCandidate = vi.fn();
    const onManualEdit = vi.fn();
    render(
      <IntakeReadinessPanel
        report={buildReport()}
        onManualConflictEdit={onManualEdit}
        onSelectConflictCandidate={onSelectCandidate}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Use candidate 1 for /system/system_name",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Edit /system/system_name manually",
      }),
    );

    expect(onSelectCandidate).toHaveBeenCalledWith({
      field: "/system/system_name",
      candidateIndex: 0,
    });
    expect(onManualEdit).toHaveBeenCalledWith({
      field: "/system/system_name",
    });
  });
});

describe("IntakeConflictList safety and malformed candidates", () => {
  it("does not render raw HTML, prompt text, or storage keys", () => {
    render(
      <IntakeConflictList
        conflicts={[
          {
            field: "/package/title",
            values: [
              {
                value: "<img src=x onerror=alert(1)>Title",
                prompt_text: "SECRET PROMPT",
                storage_key: "blob/store/key",
                source_descriptor: "SSP excerpt",
              },
            ],
          },
        ]}
        onSelectCandidate={() => undefined}
      />,
    );

    expect(screen.getByText("<img src=x onerror=alert(1)>Title")).toBeInTheDocument();
    expect(screen.queryByText(/SECRET PROMPT/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/blob\/store\/key/i)).not.toBeInTheDocument();
    expect(document.querySelector("[dangerouslySetInnerHTML]")).toBeNull();
  });

  it("handles malformed candidate shapes with bounded plain text", () => {
    expect(sanitizeConflictCandidate(null)).toEqual({
      valueLabel: "Unsupported value shape",
      sourceLabel: "Source unavailable",
    });
    expect(sanitizeConflictCandidate(["unexpected"])).toEqual({
      valueLabel: "Unsupported value shape",
      sourceLabel: "Source unavailable",
    });
    expect(
      sanitizeConflictCandidate({
        proposed_value: 42,
        display_filename: "notes.txt",
      }),
    ).toEqual({
      valueLabel: "42",
      sourceLabel: "notes.txt",
    });
  });

  it("exposes keyboard-accessible resolution controls with stable keys", () => {
    render(
      <IntakeConflictList
        conflicts={[
          {
            field: "profile_id",
            values: [{ value: "fedramp_20x_program" }, { value: "fisma_agency_security" }],
          },
        ]}
        onManualEdit={() => undefined}
        onSelectCandidate={() => undefined}
      />,
    );

    const useButtons = screen.getAllByRole("button", { name: /Use candidate/i });
    expect(useButtons).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Edit Profile Id manually" })).toBeInTheDocument();
  });
});
