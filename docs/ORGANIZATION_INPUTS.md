# Organization Inputs Checklist

## Scope and status

This checklist is separate from the repository's code-ready deployment
artifacts. It records organization-owned decisions, evidence, and host
validation needed before production or customer claims. The repository does
not supply, approve, or infer any item below. An item is not complete because
the code, WSL smoke path, or a contract test exists.

The owner entries are required owner roles, not named approvals. Until the
organization assigns an owner and accepts the required evidence, the item is
`needs organization input` and its blocking behavior remains in force.

## Code-ready in this repository

- The existing `purge-chat` command performs one bounded, audited batch of
  eligible completed private chat expiry.
- On-prem and WSL systemd service/timer templates use the existing `ato` user
  and database/audit systemd credential mappings.
- Fresh `install.sh` staging leaves the production-shaped units disabled. An
  upgrade stops active retention work before reinstall or migration, fails
  closed on a stop failure, and restores an already-enabled timer only after
  replacement. The WSL `upgrade.sh` path stages its runtime-config override
  and restores the prior timer state only after the WSL unit is installed.
- Static unit contracts, shell syntax, and installer/upgrade dry-run checks can
  be verified without a database, model endpoint, customer files, or secrets.

## Needs organization input

| Input / hard stop | Required owner | Required evidence | Blocking behavior until accepted |
| --- | --- | --- | --- |
| Reviewed authority snapshot and digest (**HS-001**) | Qualified authority reviewer / authorizing official (organization assigns) | Reviewed authority snapshot, matching digest, manifest approval record, and source review status with qualified sign-off | Blocks authority-dependent implementation and release claims. Repository artifacts and tests do not close HS-001. |
| Model data approval and provenance (**HS-004**) | Customer security and data authority (organization assigns) | Approved endpoint and data-use policy, model/provider identity and provenance, boundary and route, retention/training/subprocessor terms, recorded policy review, and an explicit data-classification decision: accepted labels, who may assert or change each label, required provenance/evidence, and audit requirements. Governed classification implementation remains engineering work after these policy decisions. | Blocks any real customer model call and any governed classification claim until accepted. The retention service has no model credential and does not make model calls. |
| Bundle-signing trust inputs (pre-code decision) | Organization security and release authority (organization assigns) | Trusted issuer and key roots, key rotation and revocation procedure, publisher allowlist, bundle provenance/attestation requirements, and the approval record for those inputs | Blocks bundle-signing code and signed-bundle trust claims. This increment does not implement or approve issuer, key, rotation, or publisher policy. |
| SME dataset and evaluation guide (**HS-006**) | AI qualification lead and qualified SMEs (organization assigns) | Written label guide, representative dataset, independent SME labels, adjudicated sealed holdout, and immutable evaluation record | Blocks AI qualification or pilot claims. Synthetic tests are not SME evidence. |
| IdP issuer, client, and groups (**HS-003**) | Customer identity administrator (organization assigns) | Issuer and audience, client registration, group claim and group-to-role map, test tenant, and authenticated authorization matrix | Blocks production identity deployment. It does not authorize a local WSL identity configuration. |
| Live ClamAV and current signatures (**HS-005**) | Customer platform security owner (organization assigns) | Operational `clamd` endpoint, version, fresh signature database/version, socket ownership/path, clean-file and EICAR tests, timeout behavior, and fail-closed evidence | Blocks customer file extraction and template upload handling. Safe parser tests are not malware-scanner evidence. |
| Customer template acceptance (**HS-002**) | Customer agency template owner / ISSO (organization assigns) | Customer template digest, field mapping, rendered-output review, exception disposition, and written acceptance | Blocks agency field-parity, customer-ready FISMA export, and submission-ready agency-shaped DOCX claims. |
| Backup, restore, and encryption keys (**HS-008**) | Customer backup administrator and key owner (organization assigns) | Customer-owned target, WAL and hourly snapshot evidence, encryption-key custody/provisioning, isolated restore drill, artifact/audit verification, and achieved RPO/RTO record | Blocks production-readiness claims. Do not enable scheduled purge for customer data without an approved backup-expiry and restore procedure. |
| Retention and legal holds (**HS-010 input**) | Customer records/legal owner and retention-policy authority (organization assigns) | Approved legal-hold scope, hold/release authority, object inventory, purge-exemption requirements, tombstone/audit expectations, and tested operational procedure | The current bounded purge does not implement legal holds. Keep the timer disabled for customer data until hold behavior is implemented and accepted; never represent the schedule as hold-safe. |
| Customer retention and approval policy (**HS-010**) | Customer policy authority / records owner (organization assigns) | Signed retention and approval-expiry policy, scope, effective date, override values, and legal/backup interaction | Any non-default retention or approval behavior remains blocked and the command fails closed for unsupported overrides. The normative defaults are not customer approval. |
| FedRAMP assessor-owned inputs — later only (**HS-009**) | Independent assessor and customer package owner (organization assigns) | Assessor-owned inventory, boundary, package inputs, and any required review records supplied through the later FedRAMP workstream | Blocks claims of complete FedRAMP Class C package readiness. This increment does not implement or close the FedRAMP path. |

## Operator gate for the optional timer

Before enabling `ato-chat-retention.timer`, the operator must identify the
intended database and service owner, confirm the applicable normative or
approved customer policy, and record the required backup, legal-hold, and
change-control evidence. If those inputs are not accepted, leave the timer
disabled and use no automated purge invocation.

The checklist is an evidence request, not an approval record. Updating a row
requires organization evidence and review; it must not be changed merely to
make a release or hard-stop test pass.
