"""Bounded operator CLI for on-prem configuration, preflight, and lifecycle actions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from ato_operator.approval_expiry import process_approval_expiry_sync
from ato_operator.audit_verify import verify_audit_chain_sync
from ato_operator.auth_purge import purge_expired_auth_artifacts_sync
from ato_operator.checklist import build_operator_checklist, format_checklist
from ato_operator.drill_handlers import (
    command_list_drill_records,
    command_list_drills,
    command_run_drill,
    command_validate_drill_record,
    command_write_drill_record,
)
from ato_operator.preflight import run_operator_preflight_sync
from ato_operator.search_index import rebuild_package_search_index_sync
from ato_service.db.dsn import require_database_dsn_from_env
from ato_service.process_capabilities import resolve_process_capabilities
from ato_service.runtime_config import (
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
    resolve_runtime_database_dsn,
)


def _find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise RuntimeConfigError("Could not locate project root (pyproject.toml not found)")


def _resolve_config_path(args: argparse.Namespace) -> Path:
    if args.config is not None:
        return Path(args.config).resolve()
    env_path = os.environ.get("ATO_RUNTIME_CONFIG_PATH")
    if env_path:
        return Path(env_path).resolve()
    raise RuntimeConfigError("config path required via --config or ATO_RUNTIME_CONFIG_PATH")


def _load_config(args: argparse.Namespace) -> RuntimeConfig:
    config_path = _resolve_config_path(args)
    project_root = _find_project_root(config_path.parent)
    base_dir = project_root if config_path.name.startswith("runtime-config.dev") else None
    return load_runtime_config(config_path, base_dir=base_dir)


def _resolve_dsn(config: RuntimeConfig) -> str:
    reference = config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        return resolve_runtime_database_dsn(config)
    return require_database_dsn_from_env()


def _alembic_config(project_root: Path) -> Config:
    return Config(str(project_root / "alembic.ini"))


def _command_validate_config(args: argparse.Namespace) -> int:
    config = _load_config(args)
    capabilities = resolve_process_capabilities(config.document)
    payload = {
        "runtime_profile": config.runtime_profile,
        "storage_data_path": str(config.storage_data_path),
        "process_capabilities": None if capabilities is None else capabilities.__dict__,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Runtime configuration is valid.")
        print(f"  profile: {config.runtime_profile}")
        print(f"  storage: {config.storage_data_path}")
        if capabilities is not None:
            active = [name for name, value in capabilities.__dict__.items() if value]
            print(f"  active capabilities: {', '.join(active) if active else '(none)'}")
    return 0


def _command_validate_credentials(args: argparse.Namespace) -> int:
    config = _load_config(args)
    report = run_operator_preflight_sync(config, project_root=_find_project_root())
    credential_checks = [
        item
        for item in report.checks
        if item.name
        in {
            "database_dsn",
            "audit_hmac_key",
            "oidc_client_secret",
            "text_model_api_key",
            "vision_model_api_key",
            "backup_encryption_key",
        }
    ]
    if args.json:
        print(
            json.dumps(
                {
                    "passed": all(item.status in {"ok", "skip"} for item in credential_checks),
                    "checks": [item.__dict__ for item in credential_checks],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for item in credential_checks:
            print(f"{item.name}: {item.status} ({item.detail})")
    return 0 if all(item.status in {"ok", "skip"} for item in credential_checks) else 1


def _command_preflight(args: argparse.Namespace) -> int:
    config = _load_config(args)
    project_root = _find_project_root(_resolve_config_path(args).parent)
    report = run_operator_preflight_sync(config, project_root=project_root)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        for item in report.checks:
            print(f"{item.name}: {item.status} ({item.detail})")
        print("preflight passed" if report.passed else "preflight failed")
    return 0 if report.passed else 1


def _resolve_dsn_file_for_migrate(config: RuntimeConfig) -> str:
    reference = config.document.get("DATABASE_DSN_CREDENTIAL_REFERENCE")
    if isinstance(reference, dict):
        if reference.get("source") == "root_owned_file":
            path = reference.get("path")
            if isinstance(path, str) and path.strip():
                return path.strip()
    env_path = os.environ.get("ATO_DATABASE_DSN_FILE")
    if env_path and env_path.strip():
        return env_path.strip()
    raise RuntimeConfigError(
        "Set ATO_DATABASE_DSN_FILE or configure root_owned_file DATABASE_DSN_CREDENTIAL_REFERENCE"
    )


def _command_migrate_db(args: argparse.Namespace) -> int:
    config = _load_config(args)
    project_root = _find_project_root()
    dsn_file = _resolve_dsn_file_for_migrate(config)
    alembic_cfg = _alembic_config(project_root)
    os.environ["ATO_DATABASE_DSN_FILE"] = dsn_file
    command.upgrade(alembic_cfg, "head")
    print("database migrations applied to head")
    return 0


def _command_verify_migrations(args: argparse.Namespace) -> int:
    config = _load_config(args)
    project_root = _find_project_root()
    alembic_cfg = _alembic_config(project_root)
    script = ScriptDirectory.from_config(alembic_cfg)
    head = script.get_current_head()
    if args.dry_run:
        print(f"migration head: {head}")
        return 0 if head else 1
    try:
        dsn = _resolve_dsn(config)
        sync_dsn = dsn.replace("+asyncpg", "")
        engine = create_engine(sync_dsn)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current = context.get_current_revision()
    except Exception as exc:
        print(f"migration verification failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    passed = current == head
    if args.json:
        print(json.dumps({"head": head, "current": current, "passed": passed}, indent=2))
    else:
        print(f"head={head} current={current} passed={passed}")
    return 0 if passed else 1


def _command_smoke(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    smoke_script = project_root / "scripts" / "smoke_service_chain.sh"
    if not smoke_script.is_file():
        print(f"missing smoke script: {smoke_script}", file=sys.stderr)
        return 1
    env = os.environ.copy()
    if args.allow_degraded_ready:
        env["ALLOW_DEGRADED_READY"] = "true"
    if args.base_url:
        env["SMOKE_BASE_URL"] = args.base_url
    result = subprocess.run(["bash", str(smoke_script)], env=env, check=False)
    return result.returncode


def _command_verify_audit(args: argparse.Namespace) -> int:
    from ato_operator.audit_verify import format_verify_audit_report

    config = _load_config(args)
    report = verify_audit_chain_sync(config)
    if args.json:
        print(json.dumps(report.to_redacted_dict(), indent=2, sort_keys=True))
    else:
        print(format_verify_audit_report(report))
    return 0 if report.passed else 1


def _command_expire_approvals(args: argparse.Namespace) -> int:
    config = _load_config(args)
    report = process_approval_expiry_sync(config)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            "expire-approvals "
            f"pending_expired={report.pending_expired} "
            f"approved_expired={report.approved_expired} "
            f"now={report.now}"
        )
    return 0


def _command_purge_auth(args: argparse.Namespace) -> int:
    config = _load_config(args)
    report = purge_expired_auth_artifacts_sync(config)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            "purge-auth "
            f"sessions_purged={report.sessions_purged} "
            f"login_states_purged={report.login_states_purged} "
            f"now={report.now}"
        )
    return 0


def _command_qualification_check(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    qualification_dir = project_root / "data" / "qualification"
    required_paths = [
        qualification_dir / "hostile-inputs" / "nessus-xxe.xml",
        qualification_dir / "hostile-inputs" / "prompt-injection-fixtures.json",
        qualification_dir / "assessor-import" / "sar-excerpt.json",
    ]
    missing = [str(path.relative_to(project_root)) for path in required_paths if not path.is_file()]
    passed = not missing
    payload: dict[str, Any] = {
        "passed": passed,
        "missing_paths": missing,
        "note": "Corpus presence only; HS-001..009 remain governed by hard-stops.yaml",
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if missing:
            print("qualification corpus incomplete:")
            for path in missing:
                print(f"  missing: {path}")
        else:
            print("qualification corpus fixtures present (presence check only)")
    return 0 if passed else 1


def _command_rebuild_search_index(args: argparse.Namespace) -> int:
    config = _load_config(args)
    revision_id = uuid.UUID(args.package_revision_id)
    dsn = _resolve_dsn(config)
    report = rebuild_package_search_index_sync(
        config=config,
        dsn=dsn,
        package_revision_id=revision_id,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            "rebuild-search-index "
            f"package_revision_id={report.package_revision_id} "
            f"chunk_count={report.chunk_count}"
        )
    return 0


def _command_print_checklist(args: argparse.Namespace) -> int:
    project_root = _find_project_root()
    items = build_operator_checklist(project_root=project_root)
    if args.json:
        print(
            json.dumps(
                [item.__dict__ for item in items],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(format_checklist(items), end="")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--config",
        help="Path to runtime JSON (overrides ATO_RUNTIME_CONFIG_PATH)",
    )
    parent.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON where supported",
    )

    parser = argparse.ArgumentParser(
        prog="ato-operator",
        description="Bounded operator CLI for ATO on-prem configuration and lifecycle actions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("validate-config", "Validate runtime JSON schema and semantics"),
        ("validate-credentials", "Validate active capability credential references"),
        ("preflight", "Run capability-aware dependency preflight checks"),
        ("migrate-db", "Apply alembic migrations to head"),
        ("verify-audit", "Verify audit hash chain integrity"),
        ("expire-approvals", "Expire pending and approved export drafts past configured deadlines"),
        ("purge-auth", "Delete expired OIDC login states and auth sessions"),
        (
            "qualification-check",
            "Verify qualification fixture corpus presence (does not close hard stops)",
        ),
        ("print-checklist", "Print airgapped onboarding checklist"),
    ):
        subparsers.add_parser(name, parents=[parent], help=help_text)

    verify_migrations = subparsers.add_parser(
        "verify-migrations",
        parents=[parent],
        help="Verify alembic head and optional live database revision",
    )
    verify_migrations.add_argument(
        "--dry-run",
        action="store_true",
        help="Report repository head without connecting to PostgreSQL",
    )

    smoke = subparsers.add_parser(
        "smoke",
        parents=[parent],
        help="Run scripts/smoke_service_chain.sh",
    )
    smoke.add_argument("--base-url", help="Override smoke base URL")
    smoke.add_argument(
        "--allow-degraded-ready",
        action="store_true",
        help="Set ALLOW_DEGRADED_READY=true for temporary operator checks",
    )

    rebuild_search = subparsers.add_parser(
        "rebuild-search-index",
        parents=[parent],
        help="Rebuild PostgreSQL full-text search index for one ready package revision",
    )
    rebuild_search.add_argument(
        "package_revision_id",
        help="Package revision UUID to rebuild",
    )

    list_drills = subparsers.add_parser(
        "list-drills",
        help="List published customer validation drills",
    )
    list_drills.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    list_records = subparsers.add_parser(
        "list-drill-records",
        parents=[parent],
        help="List persisted validation drill records under --records-root",
    )
    list_records.add_argument(
        "--records-root",
        help="Safe writable root for immutable drill records",
    )
    list_records.add_argument(
        "--drill-id",
        help="Optional drill_id filter",
    )

    validate_record = subparsers.add_parser(
        "validate-drill-record",
        help="Validate one immutable drill record file",
    )
    validate_record.add_argument("record_path", help="Path to drill record JSON")
    validate_record.add_argument("--json", action="store_true")

    write_record = subparsers.add_parser(
        "write-drill-record",
        parents=[parent],
        help="Append one validated drill record using write-once semantics",
    )
    write_record.add_argument("record_path", help="Path to drill record JSON source")
    write_record.add_argument(
        "--records-root",
        help="Safe writable root for immutable drill records",
    )

    run_drill = subparsers.add_parser(
        "run-drill",
        parents=[parent],
        help="Execute one published validation drill (default dry_run)",
    )
    run_drill.add_argument("drill_id", help="Published drill identifier")
    run_drill.add_argument(
        "--live",
        action="store_true",
        help="Run live-safe probes where supported (default is dry_run/read-only)",
    )
    run_drill.add_argument(
        "--write-record",
        action="store_true",
        help="Persist immutable drill record after execution",
    )
    run_drill.add_argument(
        "--records-root",
        help="Safe writable root for immutable drill records",
    )
    run_drill.add_argument(
        "--operator-id",
        default="operator@local",
        help="Operator identifier stored in drill record",
    )
    run_drill.add_argument(
        "--approver-id",
        help="Optional approver identifier stored in drill record",
    )
    run_drill.add_argument(
        "--isolated-target",
        action="store_true",
        help="Confirm destructive drills target an isolated host",
    )
    run_drill.add_argument("--smoke-base-url", help="Override smoke base URL for live smoke drill")
    run_drill.add_argument(
        "--allow-degraded-ready",
        action="store_true",
        help="Allow degraded readiness during live smoke drill",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    commands = {
        "validate-config": _command_validate_config,
        "validate-credentials": _command_validate_credentials,
        "preflight": _command_preflight,
        "migrate-db": _command_migrate_db,
        "verify-migrations": _command_verify_migrations,
        "smoke": _command_smoke,
        "verify-audit": _command_verify_audit,
        "expire-approvals": _command_expire_approvals,
        "purge-auth": _command_purge_auth,
        "qualification-check": _command_qualification_check,
        "print-checklist": _command_print_checklist,
        "rebuild-search-index": _command_rebuild_search_index,
        "list-drills": command_list_drills,
        "list-drill-records": command_list_drill_records,
        "validate-drill-record": command_validate_drill_record,
        "write-drill-record": command_write_drill_record,
        "run-drill": command_run_drill,
    }
    handler = commands[args.command]
    try:
        return handler(args)
    except RuntimeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
