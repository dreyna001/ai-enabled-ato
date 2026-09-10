# AI Architecture (One Page)

**Purpose:** AI assists ATO/SSP work — extract evidence, normalize fields, assess sufficiency, draft SSP text, answer bounded questions. Humans review; models never auto-approve.

**Normative contract:** [`../ATO_TECHNICAL_SPEC.md`](../ATO_TECHNICAL_SPEC.md) Sections 17–19  
**Qualification:** [`AI_EVALUATION_GUIDE.md`](AI_EVALUATION_GUIDE.md)

---

## Core Principle

```
Deterministic rules first → bounded fact bundles → LLM → JSON schema + domain validation → persisted artifacts
```

- No vector RAG today — grounding = sealed package text + evidence facts + citations
- Embeddings disabled at gateway
- Chat retrieval = PostgreSQL full-text search over revision chunks

---

## Model Steps

| Step | Status | Role |
|------|--------|------|
| `normalize_proposal` | Live | Map extracted text → canonical draft fields |
| `intake_map` | Live | Per-artifact structured fact extraction |
| `sufficiency_matrix` | Live | Evidence sufficiency per assessment item |
| `package_chat` | Live | Q&A over one sealed revision |
| SSP generate/patch | Live | Draft + contextual edits to workspace |
| `vision_extraction` | Live (optional) | Screenshot → bounded facts |
| `consistency_brief`, `narrative_flags`, `provider_draft`, `ksi_summary`, `ocr_summary` | Contract only | No runners yet |

Step types are defined in `src/ato_service/model_gateway.py` (`ModelStepType`).

---

## End-to-End Flows

**Intake worker**

```
Upload → OCR/extract segments → fact bundle (context budget) → normalize_proposal / intake_map
→ JSON parse → schema + citation binding → merge into draft document
```

**Analysis worker**

```
Sealed revision → deterministic rule/inventory selection (no model)
→ sufficiency_matrix batches (≤10 items) → gateway → validated matrix rows → machine/matrix.json
```

**SSP workspace (API)**

```
Evidence facts + pinned profile → generation prompt → text_llm → parse/validate patches
→ Provenance.AGENT_GENERATED revision → human edit/approve → OSCAL export (deterministic)
```

**Package chat (API)**

```
Question → injection checks → FTS chunks (≤8) → context pack → gateway → {answer, citations}
→ citation validation → response
```

**Vision (optional)**

```
Screenshot → vision HTTP (if enabled) → validated JSON → VisionFact records
```

---

## LLM Call Path

**Package-analysis steps** (normalize, sufficiency, chat):

```
Runner → model_gateway → routing policy → capability/budget checks → text_llm → callback
```

**SSP + vision:** call `text_llm` / vision HTTP directly (bypass gateway)

**Transport:** OpenAI-compatible `/chat/completions` or AWS Bedrock Converse  
**Catalog:** `src/ato_service/text_model_catalog.json` — profiles, token limits, timeouts

---

## Guardrails

| Layer | What |
|-------|------|
| Routing | Endpoint profile, CUI boundary approval, operator policy approval |
| Budget | `MAX_MODEL_CALLS_PER_RUN` (default 120); per-step caps |
| Context | `context_budget.py` — token packing, output reserve |
| Output | JSON Schema → domain rules → one repair attempt → fail closed |
| Citations | Must reference supplied `source_id`/offsets; validated post-hoc |
| Policy block | Denied before any call → `policy_blocked`, `llm_call_count=0` |
| Prompt contract | `prompt_version`, `prompt_sha256`, `response_schema_version` logged |

---

## Capability Flags (AI)

| Flag | Effect |
|------|--------|
| `PROCESS_CAPABILITIES.text_model_calls` | Enables text-model processes |
| `PROCESS_CAPABILITIES.vision_model_calls` | Requires `VISION_MODEL_ENABLED` |
| `PROCESS_CAPABILITIES.package_chat` / `package_search` | Gates assistant routes |
| `TEXT_MODEL_ENDPOINT_POLICY_APPROVED` | Required for prod text calls |
| `CUI_MODEL_BOUNDARY_APPROVED` | Required before CUI revisions call models |
| `VISION_MODEL_ENABLED` | Only optional model capability flag |

Secrets stay outside JSON; runtime config via `ATO_RUNTIME_CONFIG_PATH`. See [`CONFIGURATION.md`](CONFIGURATION.md).

---

## Process Split

```
┌─────────────┐     ┌────────────────┐     ┌─────────────────┐
│ intake_worker│     │ analysis_worker │     │ API (SSP, chat) │
└──────┬──────┘     └────────┬───────┘     └────────┬────────┘
       │                     │                       │
       └─────────────────────┼───────────────────────┘
                             ▼
               model_gateway (package path) / text_llm (SSP path)
                             ▼
                    External text/vision endpoint
```

---

## What AI Does NOT Do

- Select applicability rules or assessment inventory (deterministic)
- Auto-pass evaluation or close hard stops
- Run on `/health/ready`
- Replace human ISSO approval
- Use embeddings or open-ended retrieval
