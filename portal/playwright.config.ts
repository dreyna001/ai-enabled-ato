import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { portalEnv } from "./e2e/portal-env";

const env = portalEnv();
const managedStack = env.managedStack;
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const managedStackCommand = `bash ${repoRoot}/scripts/e2e-stack-start.sh --for-playwright`;
const portalOnlyCommand =
  "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 180_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: env.baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: env.useExistingStack
    ? undefined
    : {
        command: managedStack ? managedStackCommand : portalOnlyCommand,
        url: env.baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: managedStack ? 180_000 : 60_000,
        env: managedStack
          ? {
              ...process.env,
              ATO_E2E_STACK_READY: "1",
              ATO_E2E_MANAGED_STACK: "1",
              VITE_PORTAL_BASE_URL: env.baseURL,
              ATO_E2E_API_URL: env.apiURL,
            }
          : {
              ...process.env,
              VITE_DEV_API_TARGET: env.apiURL,
              VITE_PORTAL_BASE_URL: env.baseURL,
            },
      },
});
