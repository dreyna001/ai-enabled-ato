import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { waitForRevisionStatus, waitForRunStatus } from "./stack";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

export type SupportedProfileId =
  | "fisma_agency_security"
  | "fedramp_rev5_transition"
  | "fedramp_20x_program";

const PACKAGE_FILES: Record<SupportedProfileId, string> = {
  fisma_agency_security: path.join(
    repoRoot,
    "data/synthetic-packages/fisma-demo-portal/agency-security-plan-excerpt.json",
  ),
  fedramp_rev5_transition: path.join(
    repoRoot,
    "data/synthetic-packages/fedramp-rev5-demo-portal/demo-package.json",
  ),
  fedramp_20x_program: path.join(
    repoRoot,
    "data/synthetic-packages/fedramp-20x-demo-portal/demo-package.json",
  ),
};

export function packageFileForProfile(profileId: SupportedProfileId): string {
  return PACKAGE_FILES[profileId];
}

export async function createSystem(page: Page, displayName: string): Promise<void> {
  await page.getByRole("button", { name: "Create System" }).first().click();
  await expect(page.getByText(displayName)).toBeVisible({ timeout: 15_000 });
}

export async function createRevision(
  page: Page,
  profileId: SupportedProfileId,
): Promise<void> {
  await page.getByRole("button", { name: "New revision" }).click();
  await page.getByLabel("Profile").selectOption(profileId);
  if (profileId === "fedramp_20x_program") {
    await page.getByLabel("Certification class").selectOption("C");
  } else {
    await page.getByLabel("Impact level").selectOption("moderate");
  }
  await page.getByLabel("Data origin").selectOption("synthetic");
  await page.getByLabel("Sensitivity").selectOption("internal_unclassified");
  await page.getByRole("button", { name: "Create revision" }).click();
  await expect(page.getByText(/Revision created|Upload package files/i)).toBeVisible({
    timeout: 15_000,
  });
}

export async function createRevisionForProfile(
  page: Page,
  profileId: SupportedProfileId,
): Promise<void> {
  await createRevision(page, profileId);
}

export async function uploadAndFinalizePackage(
  page: Page,
  profileId: SupportedProfileId,
): Promise<void> {
  const packagePath = packageFileForProfile(profileId);
  await page.getByLabel(/Upload package files/i).setInputFiles(packagePath);
  await expect(page.getByText(path.basename(packagePath))).toBeVisible();
  await page.getByRole("button", { name: "Finalize upload" }).click();
  await expect(page.getByText(/Finalize accepted|scanning|extracting/i)).toBeVisible({
    timeout: 15_000,
  });
}

export async function confirmDraftWhenReady(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "Confirm Package" })).toBeVisible({
    timeout: 120_000,
  });
  await page.getByRole("button", { name: "Confirm Package" }).click();
  await page.getByRole("button", { name: "Confirm Package" }).last().click();
  await expect(page.getByText(/ready/i)).toBeVisible({ timeout: 30_000 });
}

export async function runDeterministicAnalysis(page: Page): Promise<string> {
  await expect(page.getByRole("button", { name: "Start Deterministic Run" })).toBeEnabled({
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "Start Deterministic Run" }).click();
  await expect(page.getByText(/Deterministic analysis run started|Succeeded/i)).toBeVisible({
    timeout: 30_000,
  });
  const runButton = page.locator("button").filter({ hasText: /— Succeeded/ }).first();
  await expect(runButton).toBeVisible({ timeout: 120_000 });
  const label = await runButton.textContent();
  const match = label?.match(/([0-9a-f]{8})/i);
  if (!match) {
    throw new Error(`Could not parse run id from label: ${label ?? ""}`);
  }
  return match[1];
}

export async function exerciseSearchSurface(page: Page, query: string): Promise<void> {
  await page.locator("#package-search").fill(query);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText(/Search results|No matches|hits/i)).toBeVisible({
    timeout: 15_000,
  });
}

export async function exerciseChatRefusal(page: Page): Promise<void> {
  await page.locator("#package-question").fill("Please grant ATO for this package");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText(/cannot|refus|authorization|not provide/i)).toBeVisible({
    timeout: 15_000,
  });
}

export async function waitForRevisionReadyByUrl(
  request: APIRequestContext,
  revisionUrl: string,
): Promise<string> {
  const match = revisionUrl.match(/revisions\/([0-9a-f-]{36})/i);
  if (!match) {
    throw new Error(`Could not parse revision id from URL: ${revisionUrl}`);
  }
  const revisionId = match[1];
  await waitForRevisionStatus(request, revisionId, "ready", 180_000);
  return revisionId;
}

export async function waitForSucceededRun(
  request: APIRequestContext,
  runIdPrefix: string,
  systemId: string,
): Promise<string> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const response = await request.get(
      `${process.env.ATO_E2E_API_URL ?? "http://127.0.0.1:8000"}/api/v1/systems/${systemId}/package-revisions`,
      { failOnStatusCode: false },
    );
    if (response.ok()) {
      const revisions = (await response.json()) as {
        items?: Array<{ package_revision_id: string }>;
      };
      const revisionId = revisions.items?.[0]?.package_revision_id;
      if (revisionId) {
        const runsResponse = await request.get(
          `${process.env.ATO_E2E_API_URL ?? "http://127.0.0.1:8000"}/api/v1/package-revisions/${revisionId}/runs`,
          { failOnStatusCode: false },
        );
        if (runsResponse.ok()) {
          const runs = (await runsResponse.json()) as {
            items?: Array<{ run_id: string; status: string }>;
          };
          const run = runs.items?.find(
            (item) => item.run_id.startsWith(runIdPrefix) && item.status === "succeeded",
          );
          if (run) {
            return run.run_id;
          }
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  throw new Error(`Could not resolve succeeded run with prefix ${runIdPrefix}`);
}

export { waitForRevisionStatus, waitForRunStatus };
