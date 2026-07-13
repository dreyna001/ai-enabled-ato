# Synthetic FISMA demo package (portal)

Upload this package through the **ATO Evidence Analysis Portal** at `http://localhost:5173/workflow`.

**File to upload:** [`agency-security-plan-excerpt.json`](agency-security-plan-excerpt.json)

This is synthetic, unclassified demo data only. It is not official authorization evidence.

## Portal walkthrough

1. **Create system** — e.g. `Customer Records Portal (demo)`.
2. **Create revision** — portal pre-fills `fisma_agency_security`, `synthetic`, `moderate`.
3. **Upload JSON** — choose `agency-security-plan-excerpt.json` when status is `uploading`.
4. **Finalize upload** — moves revision to `scanning`.
5. **Wait for intake** — WSL synthetic intake worker advances `scanning` → `extracting` → `awaiting_confirmation` (timer every 30s, or run once: `sudo systemctl start ato-synthetic-intake-worker.service` in WSL).
6. **Review proposals** — accept or reject each JSON-pointer fact proposal.
7. **Confirm revision** — when no pending proposals remain; status becomes `ready`.
8. **Start deterministic run** — on `ready`, click **Start deterministic run** (no LLM calls; exact matrix output).

## What the intake worker extracts

The worker reads all leaf JSON values from the uploaded file and creates deterministic `FactProposal` rows (for example `/system/name`, `/security_controls/AC-1/summary`, `/evidence/agency_security_plan/approver`).

## Expected profile alignment

| Field | Value |
| --- | --- |
| `profile_id` | `fisma_agency_security` (pinned in portal) |
| `data_origin` | `synthetic` |
| `impact_level` | `moderate` |
| Artifact kind | `manifest` (portal default) |
| Media type | `application/json` |

## OpenAI / GPT-4.1

After [`scripts/wsl-portal-enable.sh`](../../../scripts/wsl-portal-enable.sh), the WSL API uses [`deployment/config/runtime-config.wsl_portal.json`](../../../deployment/config/runtime-config.wsl_portal.json): OpenAI-compatible endpoint `https://api.openai.com/v1`, model `gpt-4.1`, API key from `config.local.env` (`ATO_TEXT_MODEL_API_KEY`). See [WSL local deploy — OpenAI API key](../../../docs/WSL_LOCAL_DEPLOY.md#openai-api-key-configlocalenv).

The portal **deterministic analysis run** does not call OpenAI today (`llm_call_count=0`). Model-backed runs remain future work; OpenAI config is for dev text-model paths and readiness of the stack, not this walkthrough step.

## Related contracts

- Analysis profile: [`docs/contracts/fixtures/analysis-profile.valid.fisma-synthetic.json`](../../docs/contracts/fixtures/analysis-profile.valid.fisma-synthetic.json)
- Domain fixture: [`docs/contracts/fixtures/domain.valid.fisma-package-revision.json`](../../docs/contracts/fixtures/domain.valid.fisma-package-revision.json)
