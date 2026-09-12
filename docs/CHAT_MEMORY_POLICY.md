# Chat Memory Policy

## Scope and status

This policy describes the bounded chatbot memory and operator-retention behavior
for one installation. One workspace identifies one system. The application may
keep one private history for each authenticated user and system, while shared
chat context is limited to explicitly authorized canonical system records.
User chats are never automatically promoted to canonical records.

The retention operator in this change is a manual, physical-expiry operation.
It does not create, install, enable, or claim a scheduled cleanup mechanism.
The backend uses PostgreSQL conversation, turn, and message records. It reuses
the configured chat limits and guarded PydanticAI model runtime; no vector
database or second model client is introduced.

## Primary retention

Completed turns expire seven calendar years after completion under the
normative HS-010 default. Message creation timestamps are set at completion;
reading or using a turn does not renew its expiry. Only completed private chat
turns in the configured expiry scope are eligible for this operator action.

The `purge-chat` command is the only supported physical expiry path in this
scope. There is no arbitrary user-delete operation and no command argument for
selecting an actor, conversation, or content. The command processes bounded
batches, uses the backend's indexed expiry path, never deletes an unexpired
turn, and excludes canonical system records. A per-user conversation row is
removed only after it has no remaining turn and its `updated_at` is at least
seven calendar years old. This removes the `actor_id` and historical
rate/daily usage fields after the final turn while preserving fresh metadata,
pending or leased turns, active sequence state, and idempotency state.

For calendar arithmetic, February 29 maps to February 28 when the target year
is not a leap year. A pending turn has a lease and no expiry; `purge-chat`
does not release, repair, or remove it. Crash recovery of expired pending
reservations is handled by the chat service on a subsequent request.

The separate legacy `package_revision_chat_usage` table contains only
revision-scoped rate/token counters and an actor key; it is not the unified
chat history and is intentionally not modified by `purge-chat`. Those minimal
operational counters remain under the existing chat-limit lifecycle. This is
an explicit scope boundary, not a claim that every actor-bearing row is
purged; changing that lifecycle requires a separately approved policy.

The default online-backup retention remains 90 days. Removing an expired row
from primary PostgreSQL does not remove its encrypted backup copy immediately.
This document therefore makes no claim of immediate backup removal.

Legal-hold handling and customer-specific retention overrides are not supported
by this scope. Until an approved customer policy is configured and validated,
HS-010 remains at its normative default: this feature must not be presented as a
customer compliance determination or as configured scheduled cleanup.

## Operator procedure

Run the explicit command with the existing runtime configuration and protected
credential references:

```text
ato-operator purge-chat --config /etc/ato-analyzer/runtime-config.json --json
```

The command reports bounded counts and timestamps only. Its audit event records
counts and operation metadata; it does not record prompts, responses, source
content, private history content, or user/actor identifiers. One invocation
processes at most the configured operator batch and reports whether the batch
limit was reached; repeat the command through the approved operations process
until it reports `batch_limit_reached: false`. The command must be run only
against the intended disposable or customer-approved database. It is not run
automatically by this change.

If an operator needs recurring execution, they must use the installation's
already-approved operations mechanism and explicitly configure, review, and
enable that invocation under the customer's change-control process. This
change does not add a cron entry, systemd timer, scheduler unit, or claim that
recurring cleanup is active.

## Bounded LLM step

### Purpose

Synthesize the current authorized system records for the user's question. The
step may use a small, explicitly bounded slice of the requesting user's own
private history as advisory context, but it must not turn that history into a
shared or canonical record.

### Upstream facts

The application supplies only deterministic, authorized facts:

- the current system/workspace context and the authenticated user's access
  decision;
- explicitly authorized canonical system-record identifiers and content;
- an optional bounded slice of the same user's private history, labeled as
  advisory and never as canonical evidence;
- a request-scoped allowlist mapping permitted `source_id` values to the
  supplied record excerpts; and
- the active `chat_limits` values and model-availability decision from runtime
  configuration.

Missing, stale, unauthorized, or unknown source mappings are not upstream
  facts. They are validation failures.

### Allowed

The model may produce a concise answer grounded in the supplied records, select
only supplied `source_id` values for citations, state uncertainty or that the
available records are insufficient, and use the bounded same-owner history as
advisory context to resolve continuity in the conversation.

### Not allowed

The model must never approve or determine compliance, authorization,
permissions, risk acceptance, or other official decisions. It must not invent
facts or source IDs, cite an unknown source, treat private history as a
canonical record, expose another user's history, autopromote a chat, write
application state, call tools, change retention, or override access decisions
or deterministic validation.

### Output schema

The application-owned response is strict JSON with no additional properties:

```json
{
  "answer": "string",
  "source_ids": ["source-id-from-request-allowlist"]
}
```

`answer` is bounded non-empty text. `source_ids` is a bounded, duplicate-free
array whose members must come from the request-scoped allowlist. Uncertainty
is expressed within the answer, not a separate output field. The
application owns identifiers, authorization, persistence, and all derived
relationships.

### Validation

The application must parse and schema-validate the response before exposing or
persisting it, reject extra fields and malformed JSON, enforce the configured
`chat_limits`, and resolve every returned `source_id` against the immutable
request allowlist. Unknown or duplicate citation identifiers and unbounded or
malformed outputs are rejected. An uncited answer must state an explicit
limitation. These checks cannot prove that every prose claim is factually
supported; the model's prohibition on unsupported decisions is not a semantic
proof. No model output can execute approvals or writes to canonical records.

When the model is disabled, the application may still read and return the
authorized private history within its normal access and pagination boundaries,
but it must not synthesize a new model answer. Exact model-call and context
limits are backend-owned and must remain aligned with `config.chat_limits`.

Quality qualification remains pending an approved live evaluation with
representative judgments. Synthetic regression tests demonstrate deterministic
authorization, schema, retention, and failure boundaries; they are not proof of
live-model quality, compliance, or production readiness.

### Fallback

On model disablement, unavailable dependencies, malformed output, validation
failure, or an unknown source, return an explicit bounded refusal or
dependency-degraded result without a generated answer. Preserve readable
authorized history when the request is a history read, and do not mutate
history or canonical records as a fallback. Any diagnostic record contains
redacted metadata only; raw prompts, responses, content, and actor identifiers
must not be written to logs or retention audit metadata.

### Prompt draft

```text
You are a bounded system-record assistant.

Answer only from the authorized records supplied in this request. Return strict
JSON using exactly these keys: answer and source_ids. Every factual statement about a
system record must be supported by a source_id from the supplied allowlist.
Never invent or guess a source_id. The same-owner history is advisory context,
not canonical evidence; do not cite it as a shared system record.

You cannot approve compliance, grant permissions, make authorization or risk
decisions, certify anything, write data, call tools, alter retention, or reveal
another user's history. If the records are incomplete, invalid, unauthorized,
or insufficient, say so in the answer and do not fill the gap by guessing.

Authorized records:
{{authorized_records}}

Same-owner advisory history, if present:
{{same_owner_history_advisory}}

Question:
{{question}}
```
