"""CLI handlers for customer validation drill commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ato_operator.drill_catalog import list_drill_definitions
from ato_operator.drill_records import (
    DrillRecordError,
    build_drill_record,
    compute_application_digest,
    compute_config_digest,
    list_drill_record_paths,
    read_drill_record,
    validate_drill_record_semantics,
    write_drill_record,
)
from ato_operator.drills.dispatch import map_environment_type, new_record_id, run_validation_drill
from ato_operator.drills.types import DrillRunRequest
from ato_service.runtime_config import RuntimeConfigError


def _resolve_project_root(args: argparse.Namespace) -> Path:
    from ato_operator.cli import _find_project_root, _resolve_config_path

    if getattr(args, "config", None) is not None or os.environ.get("ATO_RUNTIME_CONFIG_PATH"):
        try:
            return _find_project_root(_resolve_config_path(args).parent)
        except RuntimeConfigError:
            pass
    return _find_project_root()


def _load_config_from_args(args: argparse.Namespace):
    from ato_operator.cli import _load_config

    return _load_config(args)


def _project_root_from_args(args: argparse.Namespace) -> Path:
    return _resolve_project_root(args)


def _resolve_records_root(args: argparse.Namespace, project_root: Path) -> Path:
    if args.records_root is not None:
        return Path(args.records_root).resolve()
    return (project_root / "data" / "validation-drill-records").resolve()


def command_list_drills(args: argparse.Namespace) -> int:
    payload = [
        {
            "drill_id": item.drill_id,
            "version": item.version,
            "title": item.title,
            "description": item.description,
            "live_required": item.live_required,
            "destructive": item.destructive,
            "related_hard_stops": list(item.related_hard_stops),
        }
        for item in list_drill_definitions()
    ]
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for item in payload:
            print(
                f"{item['drill_id']} ({item['version']}) "
                f"live={'yes' if item['live_required'] else 'no'} "
                f"destructive={'yes' if item['destructive'] else 'no'}"
            )
            print(f"  {item['title']}: {item['description']}")
    return 0


def command_validate_drill_record(args: argparse.Namespace) -> int:
    project_root = _project_root_from_args(args)
    record_path = Path(args.record_path).resolve()
    try:
        document = read_drill_record(record_path, project_root=project_root)
    except (DrillRecordError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"valid": True, "record_id": document["record_id"]}, indent=2))
    else:
        print(f"valid drill record: {record_path.name}")
    return 0


def command_write_drill_record(args: argparse.Namespace) -> int:
    project_root = _project_root_from_args(args)
    records_root = _resolve_records_root(args, project_root)
    source_path = Path(args.record_path).resolve()
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
        validate_drill_record_semantics(document, project_root=project_root)
        final_path = write_drill_record(records_root, document, project_root=project_root)
    except (DrillRecordError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"written": str(final_path)}, indent=2))
    else:
        print(f"wrote drill record: {final_path}")
    return 0


def command_run_drill(args: argparse.Namespace) -> int:
    try:
        config = _load_config_from_args(args)
    except RuntimeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    project_root = _project_root_from_args(args)
    execution_mode = "live" if args.live else "dry_run"
    request = DrillRunRequest(
        drill_id=args.drill_id,
        config=config,
        project_root=project_root,
        execution_mode=execution_mode,
        operator_identifier=args.operator_id,
        approver_identifier=args.approver_id,
        isolated_target_confirmed=args.isolated_target,
        smoke_base_url=args.smoke_base_url,
        allow_degraded_ready=args.allow_degraded_ready,
    )

    try:
        result = run_validation_drill(request)
    except KeyError:
        print(f"error: unsupported drill_id: {args.drill_id}", file=sys.stderr)
        return 2

    record = build_drill_record(
        record_id=new_record_id(),
        drill_id=result.drill_id,
        drill_version=result.drill_version,
        environment_type=map_environment_type(config),
        execution_mode=execution_mode,
        started_at=result.started_at,
        completed_at=result.completed_at,
        application_digest=compute_application_digest(project_root=project_root),
        config_digest=compute_config_digest(config),
        fixture_digest=result.fixture_digest,
        operator_identifier=args.operator_id,
        approver_identifier=args.approver_id,
        outcome=result.outcome,
        hard_stop_claims=result.hard_stop_claims,
        results=result.results,
    )

    written_path: Path | None = None
    if args.write_record:
        records_root = _resolve_records_root(args, project_root)
        try:
            written_path = write_drill_record(records_root, record, project_root=project_root)
        except DrillRecordError as exc:
            print(f"error: failed to write drill record: {exc}", file=sys.stderr)
            return 1

    payload: dict[str, Any] = {
        "drill_id": result.drill_id,
        "outcome": result.outcome,
        "exit_code": result.exit_code,
        "record_id": record["record_id"],
        "written_path": None if written_path is None else str(written_path),
        "results": result.results,
        "hard_stop_claims": [claim.to_dict() for claim in result.hard_stop_claims],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"run-drill {result.drill_id} outcome={result.outcome} exit_code={result.exit_code}"
        )
        if written_path is not None:
            print(f"  record: {written_path}")
    return result.exit_code


def command_list_drill_records(args: argparse.Namespace) -> int:
    project_root = _project_root_from_args(args)
    records_root = _resolve_records_root(args, project_root)
    paths = list_drill_record_paths(records_root, drill_id=args.drill_id)
    entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            document = read_drill_record(path, project_root=project_root)
        except DrillRecordError:
            entries.append({"path": str(path), "valid": False})
            continue
        entries.append(
            {
                "path": str(path),
                "valid": True,
                "record_id": document["record_id"],
                "drill_id": document["drill_id"],
                "outcome": document["outcome"],
                "completed_at_utc": document["completed_at_utc"],
            }
        )
    if args.json:
        print(json.dumps(entries, indent=2, sort_keys=True))
    else:
        for entry in entries:
            if entry.get("valid"):
                print(
                    f"{entry['drill_id']} {entry['record_id']} "
                    f"outcome={entry['outcome']} path={entry['path']}"
                )
            else:
                print(f"invalid record path={entry['path']}")
    return 0
