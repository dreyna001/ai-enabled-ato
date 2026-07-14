# Credential Index

**Purpose:** Document credential identifiers referenced by runtime JSON. Secret bytes are never bundled in release archives.

## API and worker credentials (current consumers)

| Identifier | Typical path | Consumed by |
| --- | --- | --- |
| `database-dsn` | `/etc/ato-analyzer/credentials/database-dsn` | API, intake worker, analyzer worker, migrations |
| `audit-hmac-key` | `/etc/ato-analyzer/credentials/audit-hmac-key` | API, intake worker, analyzer worker |
| `oidc-client-secret` | `/etc/ato-analyzer/credentials/oidc-client-secret` | API when OIDC authentication is enabled |

## Declared but inactive until a process exists

Runtime JSON may declare additional credential references (text/vision model keys, backup encryption keys). Add matching `LoadCredential` mappings in systemd only when the consuming process is enabled.

## Bootstrap exception

`ATO_DATABASE_DSN_FILE` is permitted for development and migration bootstrap. Production installs use root-owned credential files referenced from JSON.

## Provisioning (out of band)

```bash
sudo install -o root -g root -m 600 /path/to/dsn.txt /etc/ato-analyzer/credentials/database-dsn
sudo install -o root -g root -m 600 /path/to/audit-hmac-key /etc/ato-analyzer/credentials/audit-hmac-key
```

Installer and upgrade scripts **never** overwrite existing credential files.

## Validation

```bash
ato-operator validate-credentials --config /etc/ato-analyzer/runtime-config.json
```

Release verification rejects archive members under `credentials/`, live DSN filenames, `.env`, and other secret-like paths.
