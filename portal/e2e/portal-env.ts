export type PortalE2EEnv = {
  baseURL: string;
  apiURL: string;
  stackDir: string;
  managedStack: boolean;
  useExistingStack: boolean;
  stackReady: boolean;
};

function envFlag(name: string): boolean {
  const value = process.env[name];
  return value === "1" || value === "true";
}

export function portalEnv(): PortalE2EEnv {
  const baseURL = process.env.VITE_PORTAL_BASE_URL ?? "http://127.0.0.1:5173";
  const apiURL = process.env.ATO_E2E_API_URL ?? "http://127.0.0.1:8000";
  const stackDir = process.env.ATO_E2E_STACK_DIR ?? "../.e2e-stack";
  const managedStack = envFlag("ATO_E2E_MANAGED_STACK");
  const useExistingStack = envFlag("ATO_E2E_USE_EXISTING_STACK");
  const stackReady = envFlag("ATO_E2E_STACK_READY");

  return {
    baseURL,
    apiURL,
    stackDir,
    managedStack,
    useExistingStack,
    stackReady,
  };
}

export function liveStackEnabled(env: PortalE2EEnv = portalEnv()): boolean {
  return env.stackReady || env.useExistingStack || env.managedStack;
}

export function liveStackSkipReason(env: PortalE2EEnv = portalEnv()): string {
  return [
    "Live E2E stack is not running.",
    "Start it with: bash scripts/e2e-stack-start.sh --portal",
    "Or run managed tests: cd portal && npm run test:e2e:managed",
    "Set ATO_E2E_STACK_READY=1 when targeting a prestarted stack.",
  ].join(" ");
}
