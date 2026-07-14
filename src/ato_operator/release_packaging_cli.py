"""CLI entrypoints for release packaging scripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ato_operator.release_allowlist import ReleaseBuildOptions
from ato_operator.release_packaging import (
    ReleasePackagingError,
    build_release_archive,
    verify_release_archive,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-packaging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build deterministic release archive")
    build.add_argument("--project-root", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--source-date-epoch", type=int, default=1_700_000_000)
    build.add_argument("--require-portal-dist", action="store_true")
    build.add_argument("--skip-portal-dist", action="store_true")
    build.add_argument("--require-airgap", action="store_true")
    build.add_argument("--git-revision")
    build.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify release archive offline")
    verify.add_argument("--project-root", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--signature", type=Path)
    verify.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            require_portal = True
            if args.skip_portal_dist:
                require_portal = False
            if args.require_portal_dist:
                require_portal = True
            report = build_release_archive(
                ReleaseBuildOptions(
                    project_root=args.project_root,
                    output_dir=args.output_dir,
                    require_portal_dist=require_portal,
                    require_airgap=args.require_airgap,
                    source_date_epoch=args.source_date_epoch,
                    git_revision=args.git_revision,
                )
            )
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"archive: {report.archive_path}")
                print(f"version: {report.package_version}")
                print(f"files: {report.file_count}")
                print(f"sha256: {report.archive_sha256}")
                print(f"migration_head: {report.migration_head}")
            return 0

        report = verify_release_archive(
            args.archive,
            signature_path=args.signature,
            project_root=args.project_root,
        )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            for warning in report.warnings:
                print(f"warning: {warning}")
            for error in report.errors:
                print(f"error: {error}", file=sys.stderr)
            print(
                "release verification passed"
                if report.passed
                else "release verification failed"
            )
            print(f"signature_status: {report.signature_status}")
        return 0 if report.passed else 1
    except ReleasePackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
