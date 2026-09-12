# Preferred-stack alignment and SSP hardening

Deployment profile: shared application stack plus customer-controlled on-prem
services. This is not a cloud migration. Existing project contracts take precedence
over personal defaults unless the current requirement calls for a change.

## Technology decisions

| Area | Decision | Reason and boundary |
| --- | --- | --- |
| Backend | Keep Python 3.12, FastAPI/Uvicorn and Pydantic v2 | Already the preferred application stack. Domain models, database rows and API contracts retain separate responsibilities. |
| Structured model calls | Adopt PydanticAI 2.42.0 on the active SSP paths | Native output schemas, asynchronous transports and a single bounded model step replace prompt-only SSP transport. Existing grounding and identity validators remain authoritative. |
| Model quality | Adopt Pydantic Evals in the development environment | Deterministic tests remain in pytest. Synthetic evaluation smoke tests do not establish real model quality or authorize production model access. |
| PostgreSQL | Keep PostgreSQL, SQLAlchemy 2 and Alembic; use psycopg 3 | No demonstrated asyncpg-specific performance requirement. Existing configured asyncpg URLs are normalized to the same psycopg implementation. No database migration is needed for the driver change. |
| Configuration | Keep the validated runtime JSON contract | `pydantic-settings` is the default for environment-driven settings, not a reason to replace this product's explicit JSON/credential-reference contract. |
| Observability | Add OpenTelemetry spans at the structured model boundary | Record bounded outcome metadata, not prompts, evidence, images, credentials or raw exceptions. Exporter/collector configuration remains deployment-owned; spans alone are not an operational monitoring qualification. |
| Frontend | Keep React, TypeScript, Vite, Tailwind, Radix, Vitest and Playwright | Repair the typed build, use a modal primitive for keyboard/focus behavior and test the active SSP routes. Preserve Federal SOC Dark; network fonts are not required. |
| Document extraction | Keep the existing bounded per-format extractors | No demonstrated need for a document-platform replacement. Production processing must fail closed before storage/parsing when malware scanning is not qualified. |
| Deployment | Keep systemd on the supported on-prem host | No new cloud, container platform or Terraform path is required for this change. Use the locked dependency constraints during installation, release assembly and CI. |

Pydantic Graph is a transitive PydanticAI dependency, not a new application workflow
engine. FastMCP, OPA, pgvector, LiteLLM, gVisor and self-hosted inference are
conditional choices: this work does not establish a requirement for those systems.

## Model runtime contract

`ssp_workspace/model_runtime.py` owns lazy, application-scoped async text and vision
clients. Policy checks run before credential resolution and model access. Every SSP
prompt carries its output schema, including repair prompts. The runtime requests
native structured output, validates the response locally and rejects refusals,
truncation and unsupported native-output models without prompt-only fallback.
Provider failures do not establish trustworthy facts.

Production SSP revisions do not yet carry approved data-origin and sensitivity
labels. SSP model calls therefore fail closed in `onprem_production`; enabling a
process capability and approving an endpoint do not authorize an unknown customer
data boundary. The `dev_local` path is for synthetic data only. Governed production
classification must be implemented and qualified before those calls are enabled.

Production evidence uploads use the configured malware-scanning gate before
storage or parsing. Agency DOCX template uploads remain separately blocked by
HS-005 pending their approved scanning integration; configuring an evidence
scanner does not silently enable that template workflow.

The complete system prompt, user prompt, schema, output reserve and repair reserve
are included in context preflight. Vision includes a conservative image allowance.
The calculation is an estimate with margin, not exact provider tokenization. Each
repair is rechecked. Oversized context fails visibly; evidence is not silently
omitted. Small-context local profiles cannot accommodate arbitrary whole-profile SSP
generation. Select a qualified larger-context profile or reduce input scope.

Provider support must be verified for the exact deployed model and endpoint.
OpenAI-compatible protocol support alone does not prove that a server enforces
schemas. Bedrock native-output support is model-specific; older catalog entries
are not automatically native-output qualified. See the official
[PydanticAI output contract](https://pydantic.dev/docs/ai/core-concepts/output/),
[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html).

## Verification and deployment limits

Run the locked deterministic checks before installing a new release:

```bash
python -m pip install -c requirements.lock -e '.[dev,bedrock]'
python -m ruff check .
python -m pytest -m 'not integration'
cd portal
npm ci
npm run test -- --run
npm run build
```

The PostgreSQL SSP integration tests and portal browser tests must also pass against
their isolated test environments. Mocks do not establish PostgreSQL concurrency,
real-provider schema support, model quality or production malware-scanner readiness.

The preferred-stack US-origin model policy still requires documented provenance for
the actual deployed model/version, including local model aliases. A configurable
endpoint name or generic catalog profile is not provenance evidence. No live model
quality, endpoint approval, data classification or malware-scanner qualification is
implied by deterministic test results.

The working checkout is separate from the installed `/opt/ato-analyzer` service.
Editing this repository does not upgrade that service. Follow the existing
[WSL deployment journey](../README.md) and validate the installed release after the
repository checks pass; do not bypass the production readiness gates.
