import { expect, test } from "@playwright/test";
import { loginViaDevOidc } from "../fixtures/auth";
import {
  confirmDraftWhenReady,
  createRevisionForProfile,
  createSystem,
  runDeterministicAnalysis,
  uploadAndFinalizePackage,
} from "../fixtures/workflow";
import { liveStackEnabled, liveStackSkipReason, portalEnv } from "../portal-env";

const env = portalEnv();

test.describe("live stack · CSRF, session, etag, and download safety", () => {
  test.skip(!liveStackEnabled(), liveStackSkipReason());

  test("CSRF-protected mutation fails without token", async ({ page, request }) => {
    await loginViaDevOidc(page);
    await createSystem(page, "E2E CSRF System");
    const sessionResponse = await request.get(`${env.apiURL}/api/v1/auth/session`);
    expect(sessionResponse.ok()).toBeTruthy();
    const session = await sessionResponse.json();

    const blocked = await request.post(`${env.apiURL}/api/v1/systems`, {
      headers: {
        Origin: session.portal_origin,
        "Idempotency-Key": "csrf-missing-key-01",
      },
      data: {
        display_name: "Blocked",
        external_system_id: null,
        owner_group: "owners",
        viewer_groups: [],
      },
    });
    expect(blocked.status()).toBe(403);
    const body = await blocked.json();
    expect(body.error_code ?? body.error).toMatch(/csrf/i);
  });

  test("stale etag on draft save surfaces reload guidance", async ({ page, request }) => {
    await loginViaDevOidc(page);
    await createSystem(page, "E2E ETag System");
    await createRevisionForProfile(page, "fisma_agency_security");
    await uploadAndFinalizePackage(page, "fisma_agency_security");
    await confirmDraftWhenReady(page);

    const revisionUrl = page.url();
    const revisionId = revisionUrl.match(/revisions\/([0-9a-f-]{36})/i)?.[1];
    expect(revisionId).toBeTruthy();

    const sessionResponse = await request.get(`${env.apiURL}/api/v1/auth/session`);
    const session = await sessionResponse.json();
    const stale = await request.put(`${env.apiURL}/api/v1/package-revisions/${revisionId}/draft`, {
      headers: {
        Origin: session.portal_origin,
        "X-CSRF-Token": session.csrf_token,
        "If-Match": '"v0"',
        "Content-Type": "application/json",
      },
      data: {
        document: {
          package: { title: "stale" },
          system: { display_name: "stale" },
          contacts: {},
          control_set: {
            source: {},
            tailoring: [],
            organization_defined_parameters: {},
            inheritance: [],
          },
          security_controls: {},
          evidence: {},
          findings: {},
          poam_candidates: {},
          assessor_inputs: {},
          privacy: { artifacts_present: false, scope_notice: "" },
          fedramp_20x: null,
          fedramp_rev5_transition: null,
          fisma_agency_security: {},
          extensions: {},
        },
      },
    });
    expect(stale.status()).toBe(412);
    const staleBody = await stale.json();
    expect(staleBody.error_code ?? staleBody.error).toMatch(/etag/i);
  });

  test("session cookie loss requires re-authentication", async ({ page, context }) => {
    await loginViaDevOidc(page);
    await expect(page.getByText("Systems")).toBeVisible();
    await context.clearCookies();
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("export reject path blocks unsafe download before approval", async ({ page }) => {
    await loginViaDevOidc(page);
    await createSystem(page, "E2E Export Reject System");
    await createRevisionForProfile(page, "fisma_agency_security");
    await uploadAndFinalizePackage(page, "fisma_agency_security");
    await confirmDraftWhenReady(page);
    await runDeterministicAnalysis(page);

    await page.getByRole("button", { name: "Open review revision" }).click();
    const dispositionSelects = page.locator('select[id^="decision-"]');
    const count = await dispositionSelects.count();
    for (let index = 0; index < count; index += 1) {
      const select = dispositionSelects.nth(index);
      await select.selectOption("accepted");
      await select
        .locator("xpath=ancestor::div[contains(@class,'rounded-md')][1]")
        .getByRole("button", { name: "Save disposition" })
        .click();
    }
    await page.getByRole("button", { name: "Submit review" }).click();
    await page.getByRole("button", { name: "Create export draft" }).click();
    await page.getByRole("button", { name: "Submit for approval" }).click();

    await page.getByLabel("Reject reason").fill("E2E reject unsafe download");
    await page.getByRole("button", { name: "Reject export" }).click();
    await expect(page.getByText(/rejected|Reason: E2E reject unsafe download/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("button", { name: "Download ZIP" })).toHaveCount(0);
  });
});
