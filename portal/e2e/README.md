# Portal Playwright E2E

Deterministic portal end-to-end tests run against a bounded local stack:

- embedded dev OIDC (`/dev-oidc`)
- PostgreSQL (schema migrated via Alembic)
- dev malware scanner substitute (integrity re-verification only)
- synthetic intake poller + deterministic analyzer worker
- no Docker and no public network

Runtime contract: [`deployment/config/runtime-config.dev_local.e2e.json`](../../deployment/config/runtime-config.dev_local.e2e.json)

## Prerequisites

1. **PostgreSQL 16+** listening on `127.0.0.1:5432`.
2. **Python 3.12+** with repo dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```

3. **Node.js 22+** and portal dependencies:

   ```bash
   cd portal && npm ci && npm run install:e2e-browsers
   ```

4. Optional overrides:
   - `ATO_E2E_DATABASE_URL` — full `postgresql+asyncpg://…` DSN
   - `ATO_E2E_API_PORT` / `ATO_E2E_PORTAL_PORT` — loopback ports (defaults `8000` / `5173`)

## Commands

Start the stack manually (Linux/WSL):

```bash
bash scripts/e2e-stack-start.sh --portal
cd portal && npm run test:e2e:existing
bash scripts/e2e-stack-stop.sh
```

Managed mode (Playwright starts/stops the stack via `webServer`):

```bash
cd portal && npm run test:e2e:managed
```

Unit/component tests only (no live stack):

```bash
cd portal && npm test && npm run build
```

Mocked security regressions run without PostgreSQL. Live workflow and auth-safety suites require the stack (`ATO_E2E_STACK_READY=1`).

## Test layout

| Path | Scope |
| --- | --- |
| `e2e/workflows/profile-primary.spec.ts` | OIDC login + intake + confirm + deterministic run + search/chat per supported profile |
| `e2e/workflows/review-export.spec.ts` | Review, comment, POA&M routing visibility, export submit/self-approval denial |
| `e2e/security/rendering-authz.spec.ts` | Mocked XSS, role denial, empty/error/degraded UX |
| `e2e/security/live-auth-safety.spec.ts` | Live CSRF, stale ETag, session reauth, export reject/download guard |

## Troubleshooting

- **PostgreSQL not reachable** — start local PostgreSQL or set `ATO_E2E_DATABASE_URL`.
- **API never becomes ready** — inspect `.e2e-stack/logs/api.log`.
- **Intake stuck in `scanning`** — confirm intake poller log at `.e2e-stack/logs/intake-poller.log`.
- **Runs stay `queued`** — confirm analyzer worker log at `.e2e-stack/logs/analyzer-worker.log`.

Stop and clean processes:

```bash
bash scripts/e2e-stack-stop.sh
```

Stack state lives under `.e2e-stack/` (gitignored). Credentials are generated locally; production deployment assets are unchanged.
