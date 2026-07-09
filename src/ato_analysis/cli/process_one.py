"""CLI entrypoint for processing one evidence package."""

from __future__ import annotations

import argparse
import logging
import sys

from ato_analysis.runner import ProcessOutcome, process_one_package

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process one ATO evidence package through the Block 1 pipeline.",
    )
    parser.add_argument(
        "--package-id",
        required=True,
        help="Package identifier (must match incoming filename stem).",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        metavar="NAME",
        help="Copy data/fixtures/NAME.json (or .txt) to incoming before processing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Set DRY_RUN=true; skip LLM matrix and block non-canonical normalize.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)

    outcome = process_one_package(
        args.package_id,
        fixture=args.fixture,
        dry_run=args.dry_run,
    )
    _print_outcome(outcome)
    return 0 if outcome.status == "completed" else 1


def _print_outcome(outcome: ProcessOutcome) -> None:
    print(f"package_id: {outcome.package_id}")
    print(f"status: {outcome.status}")
    print(f"llm_call_count: {outcome.llm_call_count}")
    print(f"message: {outcome.message}")
    if outcome.report_json_path:
        print(f"report_json: {outcome.report_json_path}")
    if outcome.report_md_path:
        print(f"report_md: {outcome.report_md_path}")
    if outcome.audit_path:
        print(f"audit: {outcome.audit_path}")


if __name__ == "__main__":
    sys.exit(main())
