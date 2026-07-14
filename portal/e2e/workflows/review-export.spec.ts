import { expect, test } from "@playwright/test";
import { loginViaDevOidc } from "../fixtures/auth";
import {
  confirmDraftWhenReady,
  createRevisionForProfile,
  createSystem,
  runDeterministicAnalysis,
  uploadAndFinalizePackage,
} from "../fixtures/workflow";
import { liveStackEnabled, liveStackSkipReason } from "../portal-env";

test.describe("live stack · review export and POA&M routing visibility", () => {
  test.skip(!liveStackEnabled(), liveStackSkipReason());

  test("review, comment, weakness disposition, export submit, and self-approval denial", async ({
    page,
  }) => {
    await loginViaDevOidc(page);
    await createSystem(page, "E2E Review Export System");
    await createRevisionForProfile(page, "fisma_agency_security");
    await uploadAndFinalizePackage(page, "fisma_agency_security");
    await confirmDraftWhenReady(page);
    await runDeterministicAnalysis(page);

    await page.getByRole("button", { name: "Open review revision" }).click();
    await expect(page.getByText("Review status")).toBeVisible();

    const dispositionSelects = page.locator('select[id^="decision-"]');
    const count = await dispositionSelects.count();
    expect(count).toBeGreaterThan(0);

    for (let index = 0; index < count; index += 1) {
      const select = dispositionSelects.nth(index);
      const decision = index === 0 ? "weakness_confirmed" : "accepted";
      await select.selectOption(decision);
      await select.locator("xpath=ancestor::div[contains(@class,'rounded-md')][1]")
        .getByRole("button", { name: "Save disposition" })
        .click();
      if (decision === "weakness_confirmed") {
        await expect(page.getByText(/POA&M candidate/i)).toBeVisible({ timeout: 15_000 });
      }
    }

    await page.locator("#review-comment").fill('<script>alert("xss")</script> review note');
    await page.getByRole("button", { name: "Add comment" }).click();
    await expect(page.getByText('<script>alert("xss")</script> review note')).toBeVisible();
    const xssTriggered = await page.evaluate(() => (window as { __xss?: number }).__xss);
    expect(xssTriggered).toBeUndefined();

    await page.getByRole("button", { name: "Submit review" }).click();
    await expect(page.getByText(/submitted/i)).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "Create export draft" }).click();
    await expect(page.getByText(/Export draft created/i)).toBeVisible();
    await page.getByRole("button", { name: "Submit for approval" }).click();
    await expect(page.getByText(/submitted for approval|pending/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByText(/different approver must approve|separation of duty/i),
    ).toBeVisible();
  });
});
