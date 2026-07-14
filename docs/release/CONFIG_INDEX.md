# Runtime Configuration Index

**Purpose:** Map non-secret runtime settings to their contract sources inside the release package.  
**Rule:** JSON-only runtime settings with credential references; no secret bytes in archives.

## Shipped templates

| File | Profile | Use |
| --- | --- | --- |
| `deployment/config/runtime-config.onprem.example.json` | `onprem_production` | Customer production template copied to `/etc/ato-analyzer/runtime-config.json` |

Dev, WSL, and E2E runtime JSON files are **excluded** from release archives.

## Contract sources

| Artifact | Purpose |
| --- | --- |
| `docs/contracts/runtime-config.schema.json` | Machine validation for runtime JSON |
| `docs/contracts/preflight.schema.json` | Deterministic analysis/export eligibility |
| `docs/CONFIGURATION.md` | Operator semantics for profiles, capabilities, and egress |

## Customer paths (never bundled)

| Path | Notes |
| --- | --- |
| `/etc/ato-analyzer/runtime-config.json` | Live production JSON; installer never overwrites |
| `deployment/config/runtime-config.dev_local*.json` | Development only |
| `deployment/config/runtime-config.wsl_*.json` | WSL bootstrap only |

## Validation commands

```bash
ato-operator validate-config --config /etc/ato-analyzer/runtime-config.json
ato-operator preflight --config /etc/ato-analyzer/runtime-config.json
```

Release verification validates the shipped on-prem example against `runtime-config.schema.json` without reading customer live config.
