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
- Active SSP chat retrieval = bounded PostgreSQL canonical records, processed
  evidence snippets, pinned requirements, approvals, and relevant revision history.
  Legacy package chat uses full-text search over sealed revision chunks.

---

## Model Steps

| Step | Status | Role |
|------|--------|------|
| `normalize_proposal` | Live | Map extracted text → canonical draft fields |
| `intake_map` | Live | Per-artifact structured fact extraction |
| `sufficiency_matrix` | Live | Evidence sufficiency per assessment item |
| `package_chat` | Live | Q&A over one sealed revision |
| SSP generate/patch | Implemented | Sequential narrative/control passes + bounded contextual edits |
| Unified SSP chat | Implemented | Private persistent conversation with permission-aware canonical retrieval |
| `vision_extraction` | Live (optional) | Screenshot → bounded facts |
| `consistency_brief`, `narrative_flags`, `provider_draft`, `ksi_summary`, `ocr_summary` | Contract only | No runners yet |

Package step types are defined in `src/ato_service/model_gateway.py`
(`ModelStepType`). Active SSP contracts and adapters live in
`ssp_workspace/model_schemas.py` and `ssp_workspace/model_runtime.py`.
Implemented or live paths are not evidence of customer model qualification.

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
Evidence facts + confirmation status + pinned profile → narrative pass → validated handoff → control pass
→ Provenance.AGENT_GENERATED revision → human edit/approve → OSCAL export (deterministic)
```

**Unified SSP chat (active API)**

```
Authenticated actor + selected system → permission check → bounded canonical retrieval
→ private advisory history + source pack → guarded PydanticAI → validated citations
→ persistent private messages; changed canonical context marks old context stale
```

Other users' conversations are not shared memory. Canonical system records are
shared according to existing access checks. Retrieval is bounded, not a claim
that the model sees the entire database on every turn.

**Package chat (legacy API)**

```
Question → injection checks → FTS chunks (≤8) → context pack → gateway → {answer, citations}
→ citation validation → response
```

**Vision (optional)**

```
Screenshot → guarded asynchronous PydanticAI vision (if approved/enabled) → validated JSON → VisionFact records
```

---

## LLM Call Path

**Package-analysis steps** (normalize, sufficiency, chat):

```
Runner → model_gateway → routing policy → capability/budget checks → text_llm → callback
```

**Active SSP + vision:** policy checks → aggregate context preflight → asynchronous
PydanticAI with provider-native closed schemas → truncation and domain validation.
Slow model work occurs outside database transactions. Narrative and control
generation each have one repair budget; neither partial pass is saved if the
overall generation fails. These are bounded calls, not autonomous tool loops.

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
| `TEXT_MODEL_ENDPOINT_POLICY_APPROVED` | Required by the SSP policy gate, not sufficient to approve customer data |
| `CUI_MODEL_BOUNDARY_APPROVED` | Required before CUI revisions call models |
| `VISION_MODEL_ENABLED` | Only optional model capability flag |

Secrets stay outside JSON; runtime config via `ATO_RUNTIME_CONFIG_PATH`. See [`CONFIGURATION.md`](CONFIGURATION.md).
Active SSP production model calls remain fail-closed for unknown data
classification. Governed classification, endpoint provenance, and live model
qualification remain open; see [ORGANIZATION_INPUTS.md](ORGANIZATION_INPUTS.md).

---

## Process Split

```
┌─────────────┐     ┌────────────────┐     ┌─────────────────┐
│ intake_worker│     │ analysis_worker │     │ API (SSP, chat) │
└──────┬──────┘     └────────┬───────┘     └────────┬────────┘
       │                     │                       │
       └─────────────────────┼───────────────────────┘
                             ▼
               model_gateway (package path) / guarded PydanticAI (SSP path)
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
