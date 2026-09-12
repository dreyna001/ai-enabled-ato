# Chat Memory Policy

## Scope and status

This policy describes the bounded chatbot memory and operator-retention behavior
for one installation. One workspace identifies one system. The application may
keep one private history for each authenticated user and system, while shared
chat context is limited to explicitly authorized canonical system records.
User chats are never automatically promoted to canonical records.

The retention operator performs explicit physical expiry. The existing bounded
`purge-chat` command remains the only purge implementation; this change adds
opt-in systemd scheduling artifacts around it. Install and upgrade scripts only
stage those artifacts and explicitly leave them disabled. They do not invoke
the purge or claim that recurring cleanup is active. The backend uses
PostgreSQL conversation, turn, and message records. It reuses the configured
chat limits and guarded PydanticAI model runtime; no vector database or second
model client is introduced.

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
customer compliance determination or as enabled scheduled cleanup. Organization
evidence and ownership inputs are tracked separately in
[`ORGANIZATION_INPUTS.md`](ORGANIZATION_INPUTS.md); the checklist does not close
any hard stop.

## Optional daily scheduling

The deployment package contains a production-shaped pair and a WSL override:

| Environment | Service configuration | Timer | Runtime config |
| --- | --- | --- | --- |
| On-prem | `ato-chat-retention.service` | `ato-chat-retention.timer` | `/etc/ato-analyzer/runtime-config.json` |
| WSL local | `ato-chat-retention.wsl-local.service` restored as `ato-chat-retention.service` | `ato-chat-retention.timer` | `/opt/ato-analyzer/runtime-config.json` |

The daily timer invokes one `Type=oneshot` service instance. The timer targets
that same unit, so systemd does not start a second instance while a bounded
batch is active. The service has no automatic restart: a non-zero `purge-chat`
exit is a failed systemd invocation and remains visible in the journal and
unit status. A successful invocation still processes only one bounded batch;
`batch_limit_reached: true` means the next scheduled or approved manual run is
needed.

Fresh on-prem `install.sh` staging disables both units; an upgrade preserves an
already-enabled retention timer and otherwise leaves it disabled. Before any
upgrade reinstall, backup checks, worker drain, or migrations, `upgrade.sh`
stops an active retention timer, service, or pending retention job. A stop
failure aborts the upgrade before package or unit replacement. For WSL, the
upgrade restores the WSL service override and common timer, reloads systemd,
and only then restores the timer's prior enabled/active state. A previously
disabled timer is not enabled. An interrupted in-flight purge is not restarted
automatically; its operator must inspect the resulting unit/journal status.
Neither path directly invokes `purge-chat` or newly opts in a previously
disabled timer. After an upgrade restores the correct unit and configuration,
a previously approved `Persistent=true` timer may resume overdue work.
Only the database DSN and audit HMAC credentials are mapped through systemd;
the purge has no model, OIDC, backup, or shell-sourced secret configuration.
Output is limited to the command's redacted counts and timestamp in journald.

After the organization has reviewed the [organization-input checklist](ORGANIZATION_INPUTS.md)
and approved the applicable default-policy operation, an operator may opt in
on the intended host with:

```bash
sudo systemctl enable --now ato-chat-retention.timer
systemctl status ato-chat-retention.timer --no-pager
```

These commands are documentation only and were not executed as part of this
change. Do not enable the timer for customer data when legal-hold behavior,
customer retention policy, backup expiry, or required ownership evidence is
unresolved.

## Delivery boundary

### Code-ready in this repository

- Completed private turns have seven-calendar-year expiry under the normative
  default, with calendar-safe February 29 handling.
- `purge-chat` removes only eligible completed turns and idle conversations in
  one bounded batch and records a non-sensitive audit event transactionally.
- On-prem and WSL systemd service/timer artifacts are hardened, daily, and
  opt-in. Fresh staging is disabled; upgrades safely preserve an explicitly
  enabled timer only after the replacement is complete.

### Needs organization input or host validation

- Legal-hold semantics and any customer-specific retention or approval override.
- Owner approval to schedule the command, plus backup expiry and restore
  evidence for the intended database and storage.
- Production systemd, PostgreSQL, credential metadata, journald, and failure
  behavior on the target host.

The code-ready items do not close HS-001 through HS-006, HS-008, HS-009, or
HS-010, and do not substitute for the organization evidence listed in
[`ORGANIZATION_INPUTS.md`](ORGANIZATION_INPUTS.md).

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
against the intended disposable or customer-approved database. The optional
timer is not enabled automatically by this change.

If an operator needs recurring execution, they must use the supplied systemd
timer only after explicitly configuring, reviewing, and enabling that
invocation under the customer's change-control process. A timer failure must
be investigated before a later run is treated as successful; recurring cleanup
is not active unless the operator has enabled the timer.

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
