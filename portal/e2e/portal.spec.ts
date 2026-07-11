import { expect, test } from "@playwright/test";

test("login screen renders in dark theme", async ({ page, context }) => {
  await context.clearCookies();
  await page.goto("/login");
  await expect(page.getByText("ATO Evidence Analysis Portal")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.locator("html")).toHaveClass(/dark/);
});
