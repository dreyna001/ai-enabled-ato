# ATO Evidence Analysis

Standalone project for the ATO Evidence Analysis Portal. Sibling to `llm_notable_analysis` under `Desktop\Cursor`.

## Docs

| File | Purpose |
| --- | --- |
| [`ATO_BLOCK1_TECHNICAL_SPEC.md`](ATO_BLOCK1_TECHNICAL_SPEC.md) | **Normative** Block 1 implementation contract |
| [`ATO_AI_ACCELERATOR_PLAN.md`](ATO_AI_ACCELERATOR_PLAN.md) | End-state product plan |
| [`ATO_PORTAL_DEMO_TALKING_TRACK.md`](ATO_PORTAL_DEMO_TALKING_TRACK.md) | Demo script and glossary |

## Block 1 (current)

- `dev_local` — all paths under this repo
- OpenAI API for synthetic/redacted non-CUI prototyping only
- CLI: ingest -> normalize (if needed) -> validate -> sufficiency matrix -> reports + audit

Start implementation from [`ATO_BLOCK1_TECHNICAL_SPEC.md`](ATO_BLOCK1_TECHNICAL_SPEC.md).

## Setup (after code scaffold)

```bash
python -m venv .venv
# activate venv
pip install -e ".[dev]"
copy config.local.env.example config.local.env
# set OPENAI_API_KEY
python -m ato_analysis.cli.process_one --package-id golden_fisma_minimal --fixture
pytest tests/ -m "not integration"
```
