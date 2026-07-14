import { expect, test } from "@playwright/test";

test("login screen renders in dark theme", async ({ page, context }) => {
  await context.clearCookies();
  await page.goto("/login");
  await expect(page.getByText("ATO Evidence Analysis Portal")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("hostile upload filename renders as text when workflow is mocked", async ({
  page,
}) => {
  const hostileName = '<img src=x onerror="window.__xss=1">report.json';

  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor_id: "e2e-user",
        groups: ["owners"],
        csrf_token: "d".repeat(32),
        portal_origin: "http://127.0.0.1:5173",
      }),
    });
  });

  await page.route("**/health/ready", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", checks: { database: "ok" } }),
    });
  });

  await page.route("**/api/v1/systems", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            system_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            display_name: "E2E System",
            owner_group: "owners",
            viewer_groups: ["viewers"],
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/systems/*/package-revisions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            package_revision_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            system_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            status: "uploading",
            revision_version: 1,
            profile_id: "fisma_agency_security",
            data_origin: "synthetic",
            sensitivity: "internal_unclassified",
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/package-revisions/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        package_revision_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        system_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        status: "uploading",
        revision_version: 1,
        profile_id: "fisma_agency_security",
        data_origin: "synthetic",
        sensitivity: "internal_unclassified",
      }),
    });
  });

  await page.goto("/workflow");

  await page.getByLabel(/Upload package files/i).setInputFiles({
    name: hostileName,
    mimeType: "application/json",
    buffer: Buffer.from('{"demo":true}'),
  });

  await expect(page.getByText(hostileName, { exact: false })).toBeVisible();
  const xssTriggered = await page.evaluate(() => (window as { __xss?: number }).__xss);
  expect(xssTriggered).toBeUndefined();
});

test("role denial surfaces actionable problem message", async ({ page }) => {
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor_id: "viewer-only",
        groups: ["viewers"],
        csrf_token: "e".repeat(32),
        portal_origin: "http://127.0.0.1:5173",
      }),
    });
  });

  await page.route("**/health/ready", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", checks: {} }),
    });
  });

  await page.route("**/api/v1/systems", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({
        error_code: "authorization_denied",
        detail: "Authorization denied.",
      }),
    });
  });

  await page.goto("/workflow");
  await expect(page.getByText(/403:|Authorization denied|permission/i)).toBeVisible();
});
