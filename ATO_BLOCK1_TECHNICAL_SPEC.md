# ATO Block 1 Technical Spec

**Project home:** `C:\Users\dreyn\OneDrive\Desktop\Cursor\ai-enabled-ato\` (sibling to `llm_notable_analysis`). Block 1 code is implemented in **this folder** — not inside the notable analysis monorepo.

Related docs in this folder: [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md), [`ATO_PORTAL_DEMO_TALKING_TRACK.md`](ATO_PORTAL_DEMO_TALKING_TRACK.md). **This spec is normative for Block 1.**

---

## Product context (Block 1 subset)

### What we are building

**ATO Evidence Analysis Portal** — eventually a full product. **Block 1** is the analysis engine slice only: ingest one evidence package, validate it, run an evidence sufficiency matrix via LLM, write reports and audit.

**Analogy:** Same pattern as SOC "notable analysis" — upstream tools produce artifacts; we analyze with AI; humans review; optional gated export later. For ATO: GRC/scanners/eMASS stay authoritative; we do not replace them.

### Posture (non-negotiable)

- **Assistive, evidence-bound, draft-only** — no official compliance, risk acceptance, or ATO grant decisions
- **Deterministic validation before and after every LLM step**
- LLM may summarize/compare provided evidence and flag gaps; LLM may **not** invent evidence or architecture
- Every report includes **AI disclosure** (fixed text below)
- **Human review** required before any future GRC publish

### End-state (later blocks — not Block 1)

Full product adds: OSCAL round-trip, document/diagram intake, full-draft SSP/SAR/POA&M, evidence portal SPA, package chat, ConMon feed to GRC, on-prem VM with local `gemma-4-31B-it`. See [Build phase map](#build-phase-map).

### Block 1 authorization path

**`fisma_agency` only** — agency ISSO/SCA, agency GRC as system of record. `fedramp` and `dod_rmf` come in Block 2.

### What is an evidence bundle?

One system's authorization working set for a single assessment cycle — a bounded snapshot, not live GRC sync.

| Layer | Examples (Block 1: embedded in JSON as text) |
| --- | --- |
| Metadata | System name, path, impact level, assessment date, boundary summary, classification |
| Controls | 800-53 Rev 5 IDs, implementation statements, linked evidence IDs |
| Evidence | Policies, SOPs, access reviews, log review records, configs — as text extracts in JSON |

Later blocks add: PDF/DOCX files, OSCAL files, scanner exports, architecture diagrams in sibling directories.

### AI disclosure (include verbatim in every report)

```text
AI Disclosure: This report was produced with machine assistance. All findings,
summaries, and status labels are draft inference bound to the evidence provided
in the package. They do not constitute an official compliance determination,
risk acceptance, or authorization decision. A qualified ISSO, SCA, or assessor
must review and approve before use in GRC, eMASS, or authorization packages.
```

---

## Status

Normative implementation contract for **Block 1**.

| Mode | Block 1 | Later |
| --- | --- | --- |
| **Runtime** | `dev_local` — all paths inside repo | `onprem_production` — VM, systemd, `/etc`, `/var`, `/opt` |
| **LLM** | OpenAI Chat Completions API for synthetic/redacted non-CUI data only | LiteLLM -> vLLM, `gemma-4-31B-it` in customer boundary |
| **UI** | None (CLI + report files) | Evidence portal SPA |

---

## Goal

One end-to-end loop:

```text
data/incoming/<package_id>.{json,txt}   # any shape; file type + size only
  -> LLM normalize (or deterministic parser when format known) -> canonical model
  -> deterministic validation + pre-flight
  -> evidence sufficiency matrix (bounded OpenAI calls)
  -> data/reports/<package_id>.{md,json}
  -> data/audit/<package_id>-<run_id>.json
  -> raw input preserved; package moved to data/processed/ or data/quarantine/
```

---

## Non-goals (Block 1)

- Production VM layout (`/etc`, `/opt`, `/var`, systemd, nginx)
- Local vLLM / LiteLLM / on-prem model weights
- Evidence portal, Postgres, package chat
- OSCAL import/export; `fedramp` / `dod_rmf`
- PDF/DOCX/diagram/scanner file intake
- Full-draft SSP/SAR/POA&M catalog
- GRC/eMASS adapters, ConMon, gated writeback
- Real CUI or customer data in repo fixtures
- AI image or architecture diagram generation (read/extract customer uploads only in later blocks)

---

## Locked decisions

| Decision | Block 1 value |
| --- | --- |
| Repository | **New standalone project** (this spec at repo root) |
| Runtime profile | `dev_local` |
| Python | 3.12 |
| Authorization path | `fisma_agency` |
| Baseline | NIST SP 800-53 Rev 5 |
| LLM backend | OpenAI API |
| Default model | `gpt-4.1-mini` (use `gpt-4.1` for eval quality) |
| Structured output | JSON in message content; parse + validate + one repair call |
| Storage | Filesystem under `data/` at repo root |
| Config | `config.local.env` (gitignored); template `config.local.env.example` |
| Secrets | `OPENAI_API_KEY` only via env or config file — never committed |
| Ingest | CLI `process_one` (polling daemon deferred) |
| Golden fixture | **Required deliverable** — see [Synthetic golden fixture](#synthetic-golden-fixture-required) |

---

## Repository layout

Repo root **is** the project root. Suggested repo name: `ai-enabled-ato` (your choice).

```text
<repo-root>/
  ATO_BLOCK1_TECHNICAL_SPEC.md    # this file
  README.md
  pyproject.toml                  # Python 3.12; package name e.g. ato_analysis
  config.local.env.example
  config.local.env                # gitignored
  .gitignore
  src/ato_analysis/
    __init__.py
    config.py                     # PROJECT_ROOT = repo root; relative data paths
    models/
      package_schema.py
      report_schema.py
    ingest/
      read_package.py
    normalize/
      normalize_llm.py            # arbitrary JSON/txt -> canonical model
      normalize_deterministic.py  # stub for known formats (Block 3)
    validate/
      package_validate.py
      preflight.py
    llm/
      client.py                   # LLMClient protocol
      openai_client.py
      local_client.py             # NotImplemented until on-prem migration
      prompts.py
      structured_output.py
    analysis/
      sufficiency_matrix.py
    report/
      markdown_generator.py
      json_report.py
    audit/
      audit_log.py
    cli/
      process_one.py
  data/
    incoming/                     # runtime drops; gitignore contents
    processed/
    quarantine/
    reports/
    audit/
    fixtures/                     # tracked in git
      golden_fisma_minimal.json           # canonical (matrix path tests)
      messy_grc_export.json               # non-canonical customer shape (normalize tests)
      golden_fisma_minimal.expected.json
      malformed_missing_control_id.json   # quarantine after normalize/validate
      malformed_broken_evidence_link.json
  tests/
    test_package_validate.py
    test_preflight.py
    test_normalize.py             # mocked LLM; messy -> canonical
    test_sufficiency_matrix.py    # mocked LLM
    test_golden_fixture.py        # deterministic tests against fixture
    test_process_one_e2e.py       # @pytest.mark.integration; live OpenAI optional
```

**Rule:** Block 1 code must not read or write `/etc`, `/opt`, `/var`, or fixed OS paths. `PROJECT_ROOT` = directory containing `pyproject.toml`.

---

## Configuration (`config.local.env.example`)

```bash
ATO_RUNTIME_PROFILE=dev_local

INCOMING_DIR=data/incoming
PROCESSED_DIR=data/processed
QUARANTINE_DIR=data/quarantine
REPORT_DIR=data/reports
AUDIT_DIR=data/audit

OPENAI_API_KEY=
OPENAI_API_URL=https://api.openai.com/v1/chat/completions
OPENAI_MODEL=gpt-4.1-mini
OPENAI_MAX_TOKENS=4096
OPENAI_TIMEOUT=120
OPENAI_MAX_RETRIES=2
ALLOW_SENSITIVE_OPENAI=false

MAX_INPUT_FILE_BYTES=10485760
MAX_CONTROLS_PER_PACKAGE=50
MAX_PARALLEL_LLM_CALLS=1
PREFLIGHT_BLOCK_THRESHOLD=0.6
DRY_RUN=false

# Future onprem_production (ignored in Block 1):
# LLM_BACKEND=local
# LLM_API_URL=http://127.0.0.1:4000/v1/chat/completions
# LLM_MODEL_NAME=gemma-4-31B-it
```

Load order: `config.local.env` -> environment overrides -> fail fast if `OPENAI_API_KEY` missing and not `DRY_RUN`.

If `ALLOW_SENSITIVE_OPENAI=false`, the runner must reject packages whose raw text or normalized `data_classification` indicates CUI, classified, customer-sensitive, or production data. Block 1 fixtures must be synthetic or explicitly redacted.

---

## `.gitignore` (required)

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
config.local.env
.env
data/incoming/*
!data/incoming/.gitkeep
data/processed/*
!data/processed/.gitkeep
data/quarantine/*
!data/quarantine/.gitkeep
data/reports/*
!data/reports/.gitkeep
data/audit/*
!data/audit/.gitkeep
```

Keep `data/fixtures/**` tracked.

---

## Package intake and canonical model

### Customer intake (flexible — like notable alerts)

**Do not require a fixed customer schema.** Each agency/GRC export differs.

**Block 1 boundary checks only:**

| Check | Rule |
| --- | --- |
| File type | `.json`, `.txt` (Block 1); `.pdf`, `.xml`, `.oscal` later |
| Size | `MAX_INPUT_FILE_BYTES` |
| Safety | No path traversal; quarantine unreadable files |

Accepted shapes: flat GRC export, nested OSCAL-like JSON, hand-built lab JSON, plain-text evidence dump. Optional manifest `package_id.json` + sibling files in later blocks.

**Preserve raw input** in `data/processed/<package_id>/raw/` for audit (same as notable keeps full alert payload).

### Normalize step (LLM when shape unknown)

Before validation/analysis, run **normalize** to internal canonical model:

```text
raw upload(s) -> [deterministic parser if format known] OR [LLM normalize pass] -> canonical package JSON
```

- LLM normalize: structured JSON output + schema validation + one repair call
- Deterministic parser: use when format is stable (SARIF, Nessus XML, OSCAL) — skip LLM for that file
- If normalize fails -> quarantine; no matrix run

**Block 1:** support arbitrary `.json` / `.txt` via LLM normalize; add deterministic parsers in Block 3.

### Canonical model (strict — internal only)

Validated **after** normalize. This is what matrix and reports consume — not what customers must upload.

| Field | Type | Notes |
| --- | --- | --- |
| `package_id` | string | Must match filename stem |
| `authorization_path` | string | Block 1: `fisma_agency` only |
| `baseline` | string | `NIST-SP-800-53-R5` |
| `impact_level` | string | e.g. `Moderate` |
| `data_classification` | string | e.g. `Unclassified` (use Unclassified in fixtures) |
| `system_name` | string | |
| `authorization_boundary` | string | Plain text; `TBD — input missing` allowed |
| `assessment_date` | string | ISO 8601 date |
| `controls` | array | |
| `evidence_items` | array | |

### Control object

`control_id`, `control_title`, `control_requirement`, `implementation_statement`, `linked_evidence_ids[]`

Control ID regex: `^[A-Z]{2,3}-\d+(\(\d+\))?$` (e.g. `AC-2`, `AC-2(1)`)

### Evidence object

`evidence_id`, `title`, `source_type`, `source_owner`, `collected_at` (ISO date), `text`

Optional: `freshness_threshold_days` at package level (default `365`)

### Quarantine

Validation failures -> `data/quarantine/<package_id>.json` + `data/quarantine/<package_id>.reason.json`

---

## Synthetic golden fixture (required)

**Block 1 must ship this.** Fake lab data only — no real agency names, systems, or CUI.

### Files to create

| File | Purpose |
| --- | --- |
| `golden_fisma_minimal.json` | Canonical model — direct matrix/report tests (skip normalize in unit tests) |
| `messy_grc_export.json` | **Non-canonical** nested export (fake Archer/CSAM-like keys) — normalize LLM integration test |
| `golden_fisma_minimal.expected.json` | Deterministic expectations (pre-flight, stale flags, min matrix shape) |
| `malformed_missing_control_id.json` | Canonical with bad control ID — quarantine at validate |
| `malformed_broken_evidence_link.json` | Canonical with broken link — quarantine at validate |

### `golden_fisma_minimal.json` content spec

Fictional system: e.g. **"Lab Scheduling System"** / **"LSS-001"** — clearly synthetic.

| Control | Scenario | Evidence setup |
| --- | --- | --- |
| **AC-2** | `partial` target | Policy text supports account management; access review dated **>365 days ago** (stale) |
| **AU-6** | `supported` target | Log review SOP + sample review record, recent dates, text supports claims |
| **CM-6** | `partial` target | Config policy exists; implementation statement claims STIG alignment but evidence is generic (gap) |
| **IR-4** | `supported` target | IR plan excerpt + tabletop exercise summary, recent |
| **RA-5** | `partial` target | Vuln scan summary text; missing asset inventory linkage in evidence |

Minimum **5 controls**, **8–10 evidence items**, all cross-linked. Include one **orphan** evidence item (linked to no control) to test warning path.

### `golden_fisma_minimal.expected.json` content spec

Deterministic checks only (no LLM output verbatim — LLM varies):

```json
{
  "package_id": "golden_fisma_minimal",
  "preflight_min_score": 0.85,
  "stale_evidence_ids": ["EV-AC2-REVIEW"],
  "controls_requiring_citations": ["AC-2", "AU-6", "CM-6", "IR-4", "RA-5"],
  "integration_expectations": {
    "AC-2": { "sufficiency_status_in": ["partial", "unsupported"] },
    "AU-6": { "sufficiency_status_in": ["supported", "partial"] }
  }
}
```

Adjust IDs to match fixture. Integration tests assert pre-flight, stale detection, and status **families** — not exact LLM prose.

### Negative fixtures

- `malformed_missing_control_id.json` — e.g. control_id `ac-2` (lowercase) or `AC2`
- `malformed_broken_evidence_link.json` — control links `EV-DOES-NOT-EXIST`

Both canonical negative fixtures must quarantine with **zero matrix calls**. If a messy customer-shaped fixture requires normalization first, it may use the normalize call but must still stop before the sufficiency matrix.

### CLI usage with fixture

```bash
cp data/fixtures/golden_fisma_minimal.json data/incoming/golden_fisma_minimal.json
python -m ato_analysis.cli.process_one --package-id golden_fisma_minimal
```

Or add `--fixture golden_fisma_minimal` flag that copies from `data/fixtures/` to `data/incoming/` before run.

---

## Deterministic logic (after normalize, before matrix LLM)

1. JSON parse + canonical schema validation
2. `package_id` matches filename
3. `authorization_path == fisma_agency`
4. Control ID format regex
5. Dedupe `control_id`, `evidence_id`
6. All `linked_evidence_ids` resolve
7. Orphan evidence -> warning
8. Stale evidence flags from `collected_at` vs threshold
9. Pre-flight score (weighted): metadata complete, controls non-empty, each control has >=1 linked evidence, no broken links

If pre-flight score < `PREFLIGHT_BLOCK_THRESHOLD` -> quarantine, no matrix LLM.

---

## LLM layer (OpenAI)

**Input to every LLM step:** canonical facts only — control row + linked evidence fact records + stale flags. Not raw package blobs, full policies, or infra source files. See product plan: extraction vs reasoning.

### Client abstraction

```python
class LLMClient(Protocol):
    def complete_json(
        self, *, system: str, user: str, schema_hint: str
    ) -> dict[str, Any]: ...
```

Block 1: `OpenAILLMClient`. Later: `LocalLLMClient` (OpenAI-compatible URL to LiteLLM/vLLM) — same interface.

Implement transport with `httpx` or `requests`; handle 429/5xx retries; never log API key.

### Sufficiency matrix

Batch controls per call (default 10). **Prompt input:** pre-digested fact records per control — not full evidence corpora.

One control per LLM call is allowed for debugging only; not the production default.

**Per-control output schema:**

| Field | Type |
| --- | --- |
| `control_id` | string |
| `sufficiency_status` | `supported` \| `partial` \| `unsupported` \| `insufficient_evidence` |
| `finding_summary` | string (cite `[EV-xxx]`) |
| `gaps` | string[] |
| `stale_evidence_ids` | string[] |
| `assessor_questions` | string[] |
| `citations` | `{ evidence_id, excerpt }[]` |

**Post-LLM validation:**

- `citations[].excerpt` must appear in source evidence text (normalize whitespace)
- `supported` requires non-empty `citations`
- One repair call on failure; else quarantine entire run

---

## Report outputs

**JSON** `data/reports/<package_id>.json`: `summary`, `ai_disclosure`, `preflight`, `evidence_matrix[]`, `validation_warnings[]`, `package_metadata`

**Markdown** `data/reports/<package_id>.md`: same sections, ISSO-readable

**Audit** `data/audit/<package_id>-<run_id>.json`: `package_id`, `run_id`, `timestamp`, `runtime_profile`, `model`, `input_hash`, `report_paths`, `llm_call_count`, `preflight_score`, `status`

### `package_run_summary` (Block 5 — additive)

Block 1 emits a text `summary` with status counts. Block 5 adds structured rollups for the portal analysis header (see Evidence portal UI in [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md)). Deterministic — no LLM.

```json
{
  "control_count": 296,
  "evidence_count": 142,
  "sufficiency_counts": {
    "supported": 31,
    "partial": 12,
    "unsupported": 2,
    "insufficient_evidence": 5
  },
  "needs_attention_count": 19,
  "stale_evidence_count": 8,
  "validation_warning_count": 3,
  "run_id": "<uuid>",
  "analysis_timestamp": "<iso8601>"
}
```

`needs_attention_count` = `partial` + `unsupported` + `insufficient_evidence`. Portal must show the draft-readiness banner; never use Passing/Gaps/Attestations labels.

---

## CLI

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix: source .venv/bin/activate
pip install -e ".[dev]"
copy config.local.env.example config.local.env   # or cp on Unix
# Set OPENAI_API_KEY in config.local.env

python -m ato_analysis.cli.process_one --package-id golden_fisma_minimal
python -m ato_analysis.cli.process_one --package-id golden_fisma_minimal --dry-run
pytest tests/ -m "not integration"
pytest tests/ -m integration   # optional; needs OPENAI_API_KEY
```

---

## Acceptance criteria (Block 1 done)

- [ ] Fixtures committed: canonical, messy GRC export, expected, malformed
- [ ] Arbitrary `.json` / `.txt` ingests — file type + size boundary only
- [ ] `messy_grc_export.json` normalizes to valid canonical model (integration)
- [ ] `golden_fisma_minimal.json` runs matrix E2E -> reports + audit
- [ ] Malformed fixtures quarantine with zero matrix calls
- [ ] Pre-flight blocks sub-threshold packages
- [ ] Unit tests pass mocked (no network)
- [ ] Raw input preserved under `data/processed/<package_id>/raw/`
- [ ] No secrets in git

---

## Migration to on-prem production (later)

When Block 1 logic is stable, add production profile without rewriting analysis modules:

| dev_local (Block 1) | onprem_production (later) |
| --- | --- |
| `data/*` under repo | `/var/ato-packages/{incoming,processed,quarantine,reports,audit}` |
| `config.local.env` | `/etc/ato-analyzer/config.env` |
| OpenAI API | LiteLLM `:4000` -> vLLM `:8000`, `gemma-4-31B-it` at `/opt/models/` |
| CLI | `systemd` `ato-analyzer.service` poll loop |
| No portal | nginx + FastAPI + React evidence portal |
| — | Multimodal diagram intake via same local model |

**Migration rule:** `validate`, `analysis`, `report`, `models` stay path-agnostic; swap `config.py` + LLM backend + deploy assets only.

Production pattern reference (separate codebase): file-drop notable analyzer with vLLM/LiteLLM, systemd units, analyst portal — reuse ideas, do not depend on that repo at runtime.

Target on-prem stack (end-state):

```text
vLLM (127.0.0.1:8000) -> LiteLLM (4000) -> analyzer -> portal/nginx/Postgres
Model: gemma-4-31B-it (text + image for diagrams in later blocks)
Small prod: 1x 96 GB GPU integrated host
```

OpenAI dev mode may remain for local dev via `LLM_BACKEND=openai|local`.

---

## Build phase map

| Block | Focus |
| --- | --- |
| **1** | This spec — dev_local, OpenAI, fixtures, sufficiency matrix, report, audit |
| 2 | OSCAL + `fedramp` / `dod_rmf`; path-aware upload checklist; optional KSI catalog intake |
| 3 | PDF/DOCX/diagram/scanner/attestation-export intake; evidence link suggestions |
| 4 | Full-draft SSP/SAR/POA&M/SAP catalog; paired OSCAL + markdown export |
| 5 | Evidence portal SPA — run summary, matrix filters, evidence search, targeted re-analysis |
| 6 | Consistency, gap clusters, assessor checklist/walkthrough, narrative flags, chat, RAG |
| 7 | GRC import, package delta, OSCAL validate-before-export, ConMon Option 1, gated writeback |

---

## Block 1 implementation checklist (first PR)

1. Initialize repo + `pyproject.toml` + `.gitignore`
2. Copy this spec to repo root
3. **Create all fixture files** per [Synthetic golden fixture](#synthetic-golden-fixture-required)
4. Implement `config.py`, schemas, validation, pre-flight
5. Implement OpenAI normalize + matrix client + reports + audit
6. Implement `process_one` CLI
7. Tests: validation, pre-flight, mocked matrix, golden deterministic checks
8. README with setup steps above

---

## Prerequisites

| Item | Notes |
| --- | --- |
| OpenAI API key | `config.local.env`; set billing/spend cap in OpenAI dashboard |
| Python 3.12 | |
| New empty git repo | Not `llm_notable_analysis` |
| This spec | Self-contained; no other docs required to start |

No VM, systemd, or GPU required for Block 1.
