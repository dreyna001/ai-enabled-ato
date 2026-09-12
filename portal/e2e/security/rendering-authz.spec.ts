import { expect, test, type Page } from "@playwright/test";

const WORKSPACE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SYSTEM_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const PROFILE_VERSION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const REVISION_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

function sspEnvelope({
  displayName = "E2E SSP system",
  evidence = [],
  agentPatches = [],
}: {
  displayName?: string;
  evidence?: Array<Record<string, unknown>>;
  agentPatches?: Array<Record<string, unknown>>;
} = {}) {
  return {
    workspace_id: WORKSPACE_ID,
    system_id: SYSTEM_ID,
    status: "working",
    system: { display_name: displayName, external_system_id: null },
    profile: {
      profile_version_id: PROFILE_VERSION_ID,
      profile_id: "fisma_agency_security",
      version: "2026.1",
      status: "active",
      impact_level: "moderate",
      provisional_impact_level: "moderate",
    },
    current_revision: {
      revision_id: REVISION_ID,
      version: 1,
      status: "working",
      content_sha256: "a".repeat(64),
      created_at: "2026-09-11T12:00:00Z",
      content: {
        facts: [
          {
            key: "system.purpose",
            value: "A bounded system used for mocked security coverage.",
            provenance: "isso_entered",
            evidence: [],
            state: "active",
          },
        ],
        sections: [
          {
            key: "system.purpose",
            title: "System purpose",
            content: "A bounded system used for mocked security coverage.",
            state: "edited",
            evidence: [],
          },
        ],
        controls: [],
        questions: [],
      },
    },
    evidence,
    approvals: [],
    agent_patches: agentPatches,
    requirements: [],
    satisfied_requirement_ids: [],
    metrics: {},
    control_response: {
      implementation_statuses: ["implemented", "unknown"],
      responsibilities: ["system_specific", "unknown"],
      question_owner_types: ["isso", "technical"],
      evidence_required_for_agent_statement: true,
    },
  };
}

async function mockSspSession(
  page: Page,
  {
    groups = ["owners"],
    workspaces = [sspEnvelope()],
    workspaceStatus = 200,
    readinessStatus = 200,
    readinessBody = { status: "ok", checks: { database: "ok" } },
  }: {
    groups?: string[];
    workspaces?: Array<Record<string, unknown>>;
    workspaceStatus?: number;
    readinessStatus?: number;
    readinessBody?: Record<string, unknown>;
  } = {},
): Promise<void> {
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor_id: "e2e-user",
        groups,
        csrf_token: "d".repeat(32),
        portal_origin: "http://127.0.0.1:5173",
      }),
    });
  });

  await page.route("**/health/ready", async (route) => {
    await route.fulfill({
      status: readinessStatus,
      contentType: "application/json",
      body: JSON.stringify(readinessBody),
    });
  });

  await page.route("**/api/v1/ssp-profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            profile_version_id: PROFILE_VERSION_ID,
            profile_id: "fisma_agency_security",
            version: "2026.1",
            status: "active",
            display_name: "Agency FISMA security",
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/ssp-workspaces", async (route) => {
    if (workspaceStatus !== 200) {
      await route.fulfill({
        status: workspaceStatus,
        contentType: "application/json",
        body: JSON.stringify({
          error_code: "authorization_denied",
          detail: "Authorization denied.",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: workspaces }),
    });
  });
}

test.describe("mocked security · rendering and authorization surfaces", () => {
  test("login screen renders in dark theme", async ({ page, context }) => {
    await context.clearCookies();
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({ status: 401 });
    });
    await page.route("**/health/ready", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", checks: {} }),
      });
    });
    await page.goto("/login");
    await expect(page).toHaveTitle("ATO Evidence Analysis Portal");
    await expect(page.getByText("Internal SSP Drafting Portal")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("hostile evidence filename renders as text in the SSP workspace", async ({ page }) => {
    const hostileName = '<img src=x onerror="window.__xss=1">report.json';

    await mockSspSession(page);
    await page.route("**/api/v1/ssp-workspaces/*/evidence", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...sspEnvelope({
            evidence: [
              {
                evidence_artifact_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                display_filename: hostileName,
                media_type: "application/json",
                status: "processed",
                uploaded_at: "2026-09-11T12:01:00Z",
              },
            ],
          }),
        }),
      });
    });

    await page.goto("/ssp");
    await page.getByRole("button", { name: "Intake & evidence" }).click();

    await page.locator('input[type="file"]').setInputFiles({
      name: hostileName,
      mimeType: "application/json",
      buffer: Buffer.from('{"demo":true}'),
    });

    await expect(page.getByText(hostileName, { exact: true })).toBeVisible();
    const xssTriggered = await page.evaluate(() => (window as { __xss?: number }).__xss);
    expect(xssTriggered).toBeUndefined();
  });

  test("hostile agent output renders as text in the SSP workspace", async ({ page }) => {
    const hostile = '<img src=x onerror="window.__storedXss=1">agent output';

    await mockSspSession(page, {
      workspaces: [
        sspEnvelope({
          agentPatches: [
            {
              patch_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
              status: "proposed",
              summary: hostile,
              operations: [{ patches: [] }],
            },
          ],
        }),
      ],
    });

    await page.goto("/ssp");
    await page.getByRole("button", { name: "Ask agent", exact: true }).click();
    await expect(page.getByRole("dialog").getByText(hostile, { exact: true })).toBeVisible();
    const storedXss = await page.evaluate(() => (window as { __storedXss?: number }).__storedXss);
    expect(storedXss).toBeUndefined();
  });

  test("role denial surfaces actionable problem message", async ({ page }) => {
    await mockSspSession(page, { groups: ["viewers"], workspaceStatus: 403 });
    await page.goto("/ssp");
    await expect(page.getByText(/403:|Authorization denied|permission/i)).toBeVisible();
  });

  test("empty workspace state renders creation guidance", async ({ page }) => {
    await mockSspSession(page, { workspaces: [] });
    await page.goto("/ssp");
    await expect(page.getByText("Create system workspace")).toBeVisible();
  });

  test("readiness degradation surfaces dependency error", async ({ page }) => {
    await mockSspSession(page, {
      workspaces: [],
      readinessStatus: 503,
      readinessBody: {
        detail: "API readiness degraded.",
        status: "degraded",
        checks: { database: "error" },
      },
    });
    await page.goto("/ssp");
    await expect(page.getByRole("status").getByText(/503|degraded/i)).toBeVisible();
  });
});
