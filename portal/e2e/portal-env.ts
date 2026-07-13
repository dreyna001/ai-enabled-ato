export function portalEnv() {
  return {
    baseURL: process.env.VITE_PORTAL_BASE_URL ?? "http://127.0.0.1:5173",
  };
}
