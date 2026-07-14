import { expect, type Page } from "@playwright/test";

export async function loginViaDevOidc(page: Page): Promise<void> {
  await page.context().clearCookies();
  await page.goto("/login");
  await expect(page.getByText("ATO Evidence Analysis Portal")).toBeVisible();
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/workflow/, { timeout: 30_000 });
  await expect(page.getByRole("navigation", { name: "Portal navigation" })).toBeVisible();
}

export async function expectAuthenticatedWorkflow(page: Page): Promise<void> {
  await expect(page.getByText("Systems")).toBeVisible();
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/login/, { timeout: 15_000 });
}
