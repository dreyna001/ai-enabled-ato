import { describe, expect, it, vi } from "vitest";
import {
  approveAgencyDocxRender,
  createAgencyDocxRender,
  downloadAgencyDocxRender,
  downloadSspExport,
  mapAgencyDocxRenders,
  mapControlResponse,
  mapInformationTypes,
  mapSystemDefinition,
  mapWorkspaceEnvelope,
  previewAgencyDocxRender,
} from "./sspWorkspace";
import { DEFAULT_CONTROL_RESPONSE_OPTIONS, type SspWorkspace } from "@/sspWorkspaceTypes";
import { ApiError } from "@/api/client";
import type { SessionInfo } from "@/types";

const confirmedSystemDefinition = {
  status: "confirmed" as const,
  boundaryNarrative:
    "The authorization boundary includes all production application hosts.",
  diagramLinks: [],
  components: [
    {
      componentId: "web",
      name: "Web tier",
      purpose: "Public UI",
      placement: "inside" as const,
      evidence: [],
    },
  ],
  interconnections: [
    {
      interconnectionId: "ic-1",
      connectedOrganization: "Partner agency",
      connectedSystem: "Identity broker",
      direction: "inbound" as const,
      dataTypes: ["authentication"],
      interfaceProtocol: "HTTPS",
      connectionOwner: "System owner",
      agreementType: "ISA",
      agreementId: "",
      agreementStatus: "",
      agreementExpiration: "",
      boundaryProtections: "TLS and IP allowlisting",
      evidence: [],
    },
  ],
  proposal: null,
};

describe("mapWorkspaceEnvelope", () => {
  it("maps persisted metrics inputs without model-calculated counts", () => {
    const workspace = mapWorkspaceEnvelope({
      workspace_id: "10000000-0000-4000-8000-000000000001",
      system_id: "10000000-0000-4000-8000-000000000002",
      status: "working",
      system: { display_name: "Case Portal", external_system_id: null },
      profile: {
        profile_version_id: "10000000-0000-4000-8000-000000000003",
        profile_id: "agency-fisma-nist-sp800-53-rev5",
        version: "1.0.0",
        status: "active",
        impact_level: "moderate",
        provisional_impact_level: "moderate",
      },
      current_revision: {
        revision_id: "10000000-0000-4000-8000-000000000004",
        version: 2,
        status: "working",
        content_sha256: "a".repeat(64),
        created_at: "2026-07-27T12:00:00Z",
        content: {
          facts: [
            {
              key: "system.categorization_status",
              value: "confirmed",
              provenance: "isso_entered",
              evidence: [],
              state: "active",
            },
            {
              key: "system.purpose",
              value: "Manages internal cases.",
              provenance: "isso_entered",
              evidence: [],
              state: "active",
            },
          ],
          sections: [
            {
              key: "system.purpose",
              title: "System Purpose",
              content: "Manages internal cases.",
              state: "edited",
              evidence: [],
            },
          ],
          controls: [
            {
              control_id: "AC-1",
              title: "Policy and Procedures",
              implementation_status: "implemented",
              implementation_statement: "The agency maintains the policy.",
              responsibility: "system_specific",
              state: "reviewed",
              evidence: [],
              unresolved_reason: null,
            },
          ],
          questions: [],
        },
      },
      evidence: [],
      approvals: [],
      agent_patches: [],
      requirements: [
        {
          key: "system.purpose",
          value_type: "string",
          required: true,
          enum_values: [],
          min_length: 20,
          evidence_required_for_agent_value: true,
        },
      ],
      satisfied_requirement_ids: ["system.purpose"],
      metrics: {
        evidence: 0,
        processed_evidence: 0,
        screenshots: 0,
        selected_controls: 1,
        controls_drafted: 1,
        partial_controls: 0,
        open_questions: 0,
        evidence_links: 0,
        satisfied_required_items: 1,
        total_required_items: 1,
        ssp_completion_percent: 100,
      },
      control_response: {
        implementation_statuses: ["implemented", "unknown"],
        responsibilities: ["system_specific", "unknown"],
        question_owner_types: ["isso", "technical"],
        evidence_required_for_agent_statement: false,
      },
    });

    expect(workspace.name).toBe("Case Portal");
    expect(workspace.purpose).toBe("Manages internal cases.");
    expect(workspace.sections[0]?.satisfiedRequirementIds).toEqual([
      "system.purpose",
    ]);
    expect(workspace.controls[0]?.id).toBe("AC-1");
    expect(workspace.impactLevel).toBe("moderate");
    expect(workspace.categorization.confirmed).toBe(true);
    expect(workspace.controlResponse.implementationStatuses).toEqual([
      "implemented",
      "unknown",
    ]);
    expect(workspace.controlResponse.evidenceRequiredForAgentStatement).toBe(
      false,
    );
    expect(workspace.agencyDocxRenders).toEqual([]);
  });

  it("falls back to default control response options when envelope omits them", () => {
    const workspace = mapWorkspaceEnvelope({
      workspace_id: "10000000-0000-4000-8000-000000000001",
      system_id: "10000000-0000-4000-8000-000000000002",
      status: "working",
      system: { display_name: "Legacy Portal" },
      profile: {
        profile_version_id: "10000000-0000-4000-8000-000000000003",
        profile_id: "legacy-profile",
        version: "0.9.0",
        status: "active",
        impact_level: null,
        provisional_impact_level: "low",
      },
      current_revision: {
        revision_id: "10000000-0000-4000-8000-000000000004",
        version: 1,
        status: "working",
        content_sha256: "b".repeat(64),
        created_at: "2026-07-27T12:00:00Z",
        content: {
          facts: [],
          sections: [],
          controls: [],
          questions: [],
        },
      },
      evidence: [],
      approvals: [],
      agent_patches: [],
      requirements: [],
      satisfied_requirement_ids: [],
      metrics: {},
    });

    expect(workspace.controlResponse).toEqual(DEFAULT_CONTROL_RESPONSE_OPTIONS);
    expect(workspace.agencyDocxRenders).toEqual([]);
  });

  it("maps structured system definition sections and status facts", () => {
    const artifactId = "30000000-0000-4000-8000-000000000010";
    const workspace = mapWorkspaceEnvelope({
      workspace_id: "10000000-0000-4000-8000-000000000001",
      system_id: "10000000-0000-4000-8000-000000000002",
      status: "working",
      system: { display_name: "Boundary Portal" },
      profile: {
        profile_version_id: "10000000-0000-4000-8000-000000000003",
        profile_id: "agency-fisma-nist-sp800-53-rev5",
        version: "1.3.0",
        status: "active",
        impact_level: "moderate",
        provisional_impact_level: "moderate",
      },
      current_revision: {
        revision_id: "10000000-0000-4000-8000-000000000004",
        version: 3,
        status: "working",
        content_sha256: "c".repeat(64),
        created_at: "2026-07-27T12:00:00Z",
        content: {
          facts: [
            {
              key: "system.system_definition_status",
              value: "stale",
              provenance: "isso_entered",
              evidence: [],
              state: "active",
            },
          ],
          sections: [
            {
              key: "system.authorization_boundary",
              title: "Authorization Boundary",
              content: JSON.stringify({
                narrative: "The boundary includes all production hosts.",
                diagram_links: [
                  {
                    artifact_id: artifactId,
                    locator: { page: 1 },
                    label: "Boundary diagram",
                  },
                ],
              }),
              state: "edited",
              evidence: [],
            },
            {
              key: "system.components",
              title: "Components",
              content: JSON.stringify({
                components: [
                  {
                    component_id: "web",
                    name: "Web tier",
                    purpose: "Public UI",
                    placement: "inside",
                    evidence: [],
                  },
                ],
              }),
              state: "edited",
              evidence: [],
            },
            {
              key: "system.interconnections",
              title: "Interconnections",
              content: JSON.stringify({
                interconnections: [
                  {
                    interconnection_id: "ic-1",
                    connected_organization: "Partner agency",
                    connected_system: "Identity broker",
                    direction: "inbound",
                    data_types: ["authentication"],
                    interface_protocol: "HTTPS",
                    connection_owner: "System owner",
                    agreement_type: "ISA",
                    agreement_id: "",
                    agreement_status: "",
                    agreement_expiration: "",
                    boundary_protections: "TLS and IP allowlisting",
                    evidence: [],
                  },
                ],
              }),
              state: "edited",
              evidence: [],
            },
          ],
          controls: [],
          questions: [],
        },
      },
      evidence: [],
      approvals: [],
      agent_patches: [],
      requirements: [],
      satisfied_requirement_ids: [],
      metrics: {},
    });

    expect(workspace.systemDefinition.status).toBe("stale");
    expect(workspace.systemDefinition.boundaryNarrative).toContain(
      "production hosts",
    );
    expect(workspace.systemDefinition.diagramLinks).toEqual([
      {
        artifactId,
        locator: { page: 1 },
        label: "Boundary diagram",
      },
    ]);
    expect(workspace.systemDefinition.components[0]?.name).toBe("Web tier");
    expect(
      workspace.systemDefinition.interconnections[0]?.connectedSystem,
    ).toBe("Identity broker");
  });
});

const baseEnvelope = {
  workspace_id: "10000000-0000-4000-8000-000000000001",
  system_id: "10000000-0000-4000-8000-000000000002",
  status: "working",
  system: { display_name: "Legacy Portal" },
  profile: {
    profile_version_id: "10000000-0000-4000-8000-000000000003",
    profile_id: "legacy-profile",
    version: "0.9.0",
    status: "active",
    impact_level: null,
    provisional_impact_level: "low",
  },
  current_revision: {
    revision_id: "10000000-0000-4000-8000-000000000004",
    version: 1,
    status: "working",
    content_sha256: "b".repeat(64),
    created_at: "2026-07-27T12:00:00Z",
    content: {
      facts: [],
      sections: [],
      controls: [],
      questions: [],
    },
  },
  evidence: [],
  approvals: [],
  agent_patches: [],
  requirements: [],
  satisfied_requirement_ids: [],
  metrics: {},
};

describe("mapInformationTypes", () => {
  it("returns empty defaults when structured section is absent", () => {
    expect(mapInformationTypes([], [])).toEqual({
      status: null,
      entries: [],
      confirmed: false,
    });
  });

  it("maps status facts and structured register content", () => {
    expect(
      mapInformationTypes(
        [
          {
            key: "system.information_types_status",
            value: "confirmed",
          },
        ],
        [
          {
            key: "system.data_types",
            content: JSON.stringify({
              information_types: [
                {
                  entry_id: "entry-1",
                  catalog_identifier: "D.14.2",
                  catalog_title: "Financial Management — Grants",
                  description: "Grant intake data.",
                  catalog_confidentiality: "moderate",
                  catalog_integrity: "moderate",
                  catalog_availability: "moderate",
                  adjusted_confidentiality: null,
                  adjusted_integrity: null,
                  adjusted_availability: null,
                  adjustment_rationale: "",
                  evidence: [],
                },
              ],
            }),
          },
        ],
      ),
    ).toEqual({
      status: "confirmed",
      confirmed: true,
      entries: [
        expect.objectContaining({
          entryId: "entry-1",
          catalogIdentifier: "D.14.2",
          description: "Grant intake data.",
        }),
      ],
    });
  });
});

describe("saveSspInformationTypes", () => {
  it("posts structured information type payloads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(baseEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { saveSspInformationTypes } = await import("./sspWorkspace");
    await saveSspInformationTypes(session, workspace, {
      entries: [
        {
          entryId: "entry-1",
          catalogIdentifier: "D.14.2",
          catalogTitle: "Financial Management — Grants",
          description: "Grant intake data.",
          catalogConfidentiality: "moderate",
          catalogIntegrity: "moderate",
          catalogAvailability: "moderate",
          adjustedConfidentiality: "",
          adjustedIntegrity: "",
          adjustedAvailability: "",
          adjustmentRationale: "",
          evidence: [],
        },
      ],
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/information-types",
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toMatchObject({
      expected_revision_id: workspace.revisionId,
      information_types: [
        expect.objectContaining({
          catalog_identifier: "D.14.2",
          description: "Grant intake data.",
        }),
      ],
    });

    vi.unstubAllGlobals();
  });
});

describe("fetchSp80060Catalog", () => {
  it("maps catalog picker data from the API", async () => {
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

    const { fetchSp80060Catalog } = await import("./sspWorkspace");
    await expect(fetchSp80060Catalog()).resolves.toEqual({
      sourceId: "nist-sp-800-60-rev2",
      title: "NIST SP 800-60",
      version: "2.0.0",
      reference: "https://example.test/sp800-60",
      informationTypes: [
        {
          identifier: "D.14.2",
          title: "Financial Management — Grants",
          confidentiality: "moderate",
          integrity: "moderate",
          availability: "moderate",
        },
      ],
    });

    vi.unstubAllGlobals();
  });
});

describe("mapSystemDefinition", () => {
  it("returns empty defaults when structured sections are absent", () => {
    expect(mapSystemDefinition([], [])).toEqual({
      status: null,
      boundaryNarrative: "",
      diagramLinks: [],
      components: [],
      interconnections: [],
      proposal: null,
    });
  });
});

describe("saveSspSystemDefinition", () => {
  it("posts structured system definition payloads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(baseEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { saveSspSystemDefinition } = await import("./sspWorkspace");
    await saveSspSystemDefinition(session, workspace, {
      boundaryNarrative:
        "The authorization boundary includes all production application hosts.",
      diagramLinks: [],
      components: confirmedSystemDefinition.components,
      interconnections: confirmedSystemDefinition.interconnections,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/system-definition",
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toMatchObject({
      expected_revision_id: workspace.revisionId,
      boundary_narrative:
        "The authorization boundary includes all production application hosts.",
      components: [
        expect.objectContaining({ name: "Web tier", placement: "inside" }),
      ],
      interconnections: [
        expect.objectContaining({
          connected_system: "Identity broker",
          direction: "inbound",
        }),
      ],
    });

    vi.unstubAllGlobals();
  });
});

describe("mapAgencyDocxRenders", () => {
  it("maps agency docx render metadata and nested exceptions", () => {
    const renderId = "20000000-0000-4000-8000-000000000010";
    const renders = mapAgencyDocxRenders([
      {
        render_id: renderId,
        profile_version_id: "10000000-0000-4000-8000-000000000003",
        source_revision_id: "10000000-0000-4000-8000-000000000004",
        source_revision_sha256: "c".repeat(64),
        template_sha256: "d".repeat(64),
        template_filename: "agency-template.docx",
        output_sha256: "e".repeat(64),
        status: "review_failed",
        created_by: "isso@example.test",
        created_at: "2026-07-28T10:00:00Z",
        resolved_by: null,
        resolved_at: null,
        mapping_summary: "Mapped 12 placeholders.",
        mapping_exceptions: [
          {
            severity: "blocker",
            code: "missing_placeholder",
            message: "Body placeholder not found.",
          },
          {
            severity: "invalid",
            code: "ignored",
            message: "Dropped malformed exception.",
          },
        ],
        review_summary: "Review found blockers.",
        review_issues: [
          {
            severity: "warning",
            code: "style_drift",
            message: "Heading style differs from template.",
            locator: "section:1",
          },
        ],
        can_approve: false,
        can_preview: true,
        can_download: false,
      },
    ]);

    expect(renders).toEqual([
      {
        id: renderId,
        profileVersionId: "10000000-0000-4000-8000-000000000003",
        sourceRevisionId: "10000000-0000-4000-8000-000000000004",
        sourceRevisionSha256: "c".repeat(64),
        templateSha256: "d".repeat(64),
        templateFilename: "agency-template.docx",
        outputSha256: "e".repeat(64),
        status: "review_failed",
        createdBy: "isso@example.test",
        createdAt: "2026-07-28T10:00:00Z",
        resolvedBy: null,
        resolvedAt: null,
        mappingSummary: "Mapped 12 placeholders.",
        mappingExceptions: [
          {
            severity: "blocker",
            code: "missing_placeholder",
            message: "Body placeholder not found.",
          },
        ],
        reviewSummary: "Review found blockers.",
        reviewIssues: [
          {
            severity: "warning",
            code: "style_drift",
            message: "Heading style differs from template.",
            locator: "section:1",
          },
        ],
        canApprove: false,
        canPreview: true,
        canDownload: false,
      },
    ]);
  });

  it("returns an empty array for missing or malformed payloads", () => {
    expect(mapAgencyDocxRenders(undefined)).toEqual([]);
    expect(mapAgencyDocxRenders(null)).toEqual([]);
    expect(mapAgencyDocxRenders({})).toEqual([]);
    expect(mapAgencyDocxRenders([{ render_id: "x", status: "unknown" }])).toEqual(
      [],
    );
  });
});

describe("mapWorkspaceEnvelope agency docx renders", () => {
  it("maps agency_docx_renders from the envelope", () => {
    const workspace = mapWorkspaceEnvelope({
      ...baseEnvelope,
      agency_docx_renders: [
        {
          render_id: "20000000-0000-4000-8000-000000000011",
          profile_version_id: "10000000-0000-4000-8000-000000000003",
          source_revision_id: "10000000-0000-4000-8000-000000000004",
          source_revision_sha256: "f".repeat(64),
          template_sha256: "a".repeat(64),
          template_filename: "template.docx",
          output_sha256: "b".repeat(64),
          status: "awaiting_approval",
          created_by: "isso@example.test",
          created_at: "2026-07-28T11:00:00Z",
          resolved_by: null,
          resolved_at: null,
          mapping_summary: "",
          mapping_exceptions: [],
          review_summary: "",
          review_issues: [],
          can_approve: true,
          can_preview: true,
          can_download: false,
        },
      ],
    });

    expect(workspace.agencyDocxRenders).toHaveLength(1);
    expect(workspace.agencyDocxRenders[0]?.status).toBe("awaiting_approval");
    expect(workspace.agencyDocxRenders[0]?.canApprove).toBe(true);
  });
});

describe("mapControlResponse", () => {
  it("returns defaults for malformed payloads", () => {
    expect(mapControlResponse(null)).toEqual(DEFAULT_CONTROL_RESPONSE_OPTIONS);
    expect(mapControlResponse({ implementation_statuses: [] })).toEqual(
      DEFAULT_CONTROL_RESPONSE_OPTIONS,
    );
  });
});

const session: SessionInfo = {
  csrf_token: "csrf-token",
  portal_origin: "https://portal.example.test",
  actor_id: "actor-1",
  groups: ["isso"],
};

const workspace: SspWorkspace = {
  id: "10000000-0000-4000-8000-000000000001",
  name: "Legacy Portal",
  purpose: "",
  hosting: "",
  impactLevel: "",
  provisionalImpactLevel: "low",
  categorization: {
    confidentiality: "",
    integrity: "",
    availability: "",
    confidentialityRationale: "",
    integrityRationale: "",
    availabilityRationale: "",
    confidentialityEvidence: [],
    integrityEvidence: [],
    availabilityEvidence: [],
    status: "unconfirmed",
    confirmed: false,
    agentSuggestion: null,
  },
  authorizationPath: "",
  profile: {
    id: "10000000-0000-4000-8000-000000000003",
    name: "legacy-profile",
    version: "0.9.0",
    baseline: "Unconfirmed",
  },
  controlResponse: DEFAULT_CONTROL_RESPONSE_OPTIONS,
  revisionId: "10000000-0000-4000-8000-000000000004",
  revisionUpdatedAt: "2026-07-27T12:00:00Z",
  currentContentHash: "b".repeat(64),
  processingJobsTerminal: true,
  revisionSaved: true,
  internallyConsistent: true,
  requirements: [],
  evidence: [],
  sections: [],
  controls: [],
  questions: [],
  patches: [],
  agencyDocxRenders: [],
  systemDefinition: confirmedSystemDefinition,
  informationTypes: {
    status: "confirmed",
    entries: [],
    confirmed: true,
  },
};

describe("agency docx render API helpers", () => {
  it("uploads templates with revision_id multipart fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(baseEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["template"], "agency-template.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    const updated = await createAgencyDocxRender(session, workspace, file);

    expect(updated.agencyDocxRenders).toEqual([]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/agency-docx-renders");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "X-CSRF-Token": "csrf-token",
      Origin: "https://portal.example.test",
    });
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("revision_id")).toBe(
      workspace.revisionId,
    );
    expect((init.body as FormData).get("file")).toBe(file);

    vi.unstubAllGlobals();
  });

  it("posts approve mutations and maps the returned envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(baseEnvelope), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await approveAgencyDocxRender(
      session,
      workspace,
      "20000000-0000-4000-8000-000000000010",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/agency-docx-renders/20000000-0000-4000-8000-000000000010/approve",
    );
    expect(init.method).toBe("POST");

    vi.unstubAllGlobals();
  });

  it("surfaces download failures as ApiError instances", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "render preview is unavailable" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      previewAgencyDocxRender(
        workspace,
        "20000000-0000-4000-8000-000000000010",
      ),
    ).rejects.toEqual(new ApiError(409, "render preview is unavailable"));

    vi.unstubAllGlobals();
  });

  it("downloads approved renders with agency-shaped fallback filenames", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(
        new Blob(["docx"], {
          type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }),
      ),
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-disposition"
            ? 'attachment; filename="agency-shaped-draft-20000000-0000-4000-8000-000000000010.docx"'
            : null,
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL,
      revokeObjectURL,
    });
    const click = vi.fn();
    vi.stubGlobal(
      "document",
      {
        createElement: vi.fn(() => ({
          click,
          href: "",
          download: "",
        })),
      } as unknown as Document,
    );

    await downloadAgencyDocxRender(
      workspace,
      "20000000-0000-4000-8000-000000000010",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/agency-docx-renders/20000000-0000-4000-8000-000000000010/download",
      { credentials: "include" },
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalledOnce();

    vi.unstubAllGlobals();
  });
});

describe("downloadSspExport", () => {
  function stubSuccessfulDownload() {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob(['{"oscal":true}'], { type: "application/json" })),
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-disposition"
            ? 'attachment; filename="server-named.oscal.json"'
            : null,
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const anchor = { click: vi.fn(), href: "", download: "" };
    vi.stubGlobal(
      "document",
      {
        createElement: vi.fn(() => anchor),
      } as unknown as Document,
    );
    return { fetchMock, anchor };
  }

  it("requests oscal-json exports with revision query params and safe filenames", async () => {
    const { fetchMock, anchor } = stubSuccessfulDownload();

    await downloadSspExport(workspace, "oscal-json");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/ssp-workspaces/10000000-0000-4000-8000-000000000001/exports/oscal-json?revision_id=10000000-0000-4000-8000-000000000004&include_open_questions=true",
      { credentials: "include" },
    );
    expect(anchor.download).toBe("server-named.oscal.json");
    expect(anchor.click).toHaveBeenCalledOnce();

    vi.unstubAllGlobals();
  });

  it("falls back to ssp revision oscal filenames when Content-Disposition is absent", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob(["{}"], { type: "application/json" })),
      headers: { get: () => null },
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    });
    const anchor = { click: vi.fn(), href: "", download: "" };
    vi.stubGlobal(
      "document",
      {
        createElement: vi.fn(() => anchor),
      } as unknown as Document,
    );

    await downloadSspExport(workspace, "oscal-json");

    expect(anchor.download).toBe(
      "ssp-10000000-0000-4000-8000-000000000004.oscal.json",
    );

    vi.unstubAllGlobals();
  });

  it("forwards docx and json export formats unchanged", async () => {
    const { fetchMock } = stubSuccessfulDownload();

    await downloadSspExport(workspace, "json");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/exports/json?");

    await downloadSspExport(workspace, "docx");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("/exports/docx?");

    vi.unstubAllGlobals();
  });
});
