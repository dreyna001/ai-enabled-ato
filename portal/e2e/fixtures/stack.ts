import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { portalEnv } from "../portal-env";

const env = portalEnv();

export async function waitForApiLive(request: APIRequestContext): Promise<void> {
  const deadline = Date.now() + 60_000;
  let lastError = "API did not respond";
  while (Date.now() < deadline) {
    try {
      const response = await request.get(`${env.apiURL}/health/live`);
      if (response.ok()) {
        return;
      }
      lastError = `API liveness returned HTTP ${response.status()}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(`Timed out waiting for API liveness at ${env.apiURL}/health/live: ${lastError}`);
}

export async function waitForRevisionStatus(
  request: APIRequestContext,
  revisionId: string,
  expectedStatus: string,
  timeoutMs = 120_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "unknown";
  while (Date.now() < deadline) {
    const response = await request.get(`${env.apiURL}/api/v1/package-revisions/${revisionId}`, {
      failOnStatusCode: false,
    });
    if (response.ok()) {
      const payload = (await response.json()) as { status?: string };
      lastStatus = payload.status ?? "unknown";
      if (lastStatus === expectedStatus) {
        return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  throw new Error(
    `Revision ${revisionId} did not reach status ${expectedStatus} within ${timeoutMs}ms (last: ${lastStatus})`,
  );
}

export async function waitForRunStatus(
  request: APIRequestContext,
  runId: string,
  expectedStatus: string,
  timeoutMs = 120_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "unknown";
  while (Date.now() < deadline) {
    const response = await request.get(`${env.apiURL}/api/v1/runs/${runId}`, {
      failOnStatusCode: false,
    });
    if (response.ok()) {
      const payload = (await response.json()) as { status?: string };
      lastStatus = payload.status ?? "unknown";
      if (lastStatus === expectedStatus) {
        return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  throw new Error(
    `Run ${runId} did not reach status ${expectedStatus} within ${timeoutMs}ms (last: ${lastStatus})`,
  );
}

export async function expectVisibleProblem(page: Page, pattern: RegExp | string): Promise<void> {
  await expect(page.getByText(pattern)).toBeVisible();
}
