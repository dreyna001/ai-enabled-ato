#!/usr/bin/env python3
"""Compile deterministic draft analysis profile artifacts from pinned authorities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

QualificationStatus = Literal["draft", "qualified"]

from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.fedramp_profile import compile_fedramp_20x_class_c_profile
from ato_service.rev5_profile import compile_fedramp_rev5_transition_profile

DEFAULT_PROFILE_VERSION = "1.0.0"
DEFAULT_MANIFEST_RELATIVE_PATH = Path("docs/contracts/authority-manifest.json")
DEFAULT_OUTPUT_RELATIVE_DIR = Path("reference/profiles")

BUNDLED_OUTPUT_FILENAMES: tuple[str, ...] = (
    "fedramp-20x-program-class-c.json",
    "fedramp-rev5-transition-low.json",
    "fedramp-rev5-transition-moderate.json",
    "fedramp-rev5-transition-high.json",
)


class CompileAnalysisProfilesError(RuntimeError):
    """Raised when profile artifact compilation or verification fails."""


@dataclass(frozen=True, slots=True)
class GeneratedProfileArtifact:
    filename: str
    payload: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


def find_project_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file():
            return path
    raise CompileAnalysisProfilesError(
        "Could not locate project root (pyproject.toml not found)"
    )


def default_manifest_path(*, project_root: Path) -> Path:
    return (project_root / DEFAULT_MANIFEST_RELATIVE_PATH).resolve()


def default_output_dir(*, project_root: Path) -> Path:
    return (project_root / DEFAULT_OUTPUT_RELATIVE_DIR).resolve()


def _verify_manifest_for_qualification(
    manifest_path: Path,
    *,
    project_root: Path,
    qualification_status: QualificationStatus,
) -> dict[str, Any]:
    try:
        manifest = verify_authority_manifest(
            manifest_path.resolve(),
            project_root=project_root.resolve(),
        )
    except AuthorityManifestVerificationError as exc:
        raise CompileAnalysisProfilesError(str(exc)) from exc

    if qualification_status == "qualified" and manifest.get("status") != "approved":
        raise CompileAnalysisProfilesError(
            "qualified bundled profiles require an approved authority manifest; "
            f"verified manifest status is {manifest.get('status')!r}"
        )
    return manifest


def parse_manifest_generated_at(
    manifest_path: Path,
    *,
    project_root: Path,
    qualification_status: QualificationStatus = "draft",
) -> datetime:
    manifest = _verify_manifest_for_qualification(
        manifest_path,
        project_root=project_root,
        qualification_status=qualification_status,
    )

    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise CompileAnalysisProfilesError(
            "verified authority manifest must declare created_at"
        )

    normalized = created_at.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CompileAnalysisProfilesError(
            f"authority manifest created_at is not parseable: {created_at!r}"
        ) from exc

    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise CompileAnalysisProfilesError(
            "authority manifest created_at must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def serialize_profile_document(profile: Mapping[str, Any]) -> bytes:
    """Return canonical pretty JSON bytes with LF line endings and trailing newline."""
    text = json.dumps(profile, indent=2, ensure_ascii=False) + "\n"
    return text.replace("\r\n", "\n").encode("utf-8")


def compile_bundled_profile_artifacts(
    *,
    manifest_path: Path,
    project_root: Path,
    profile_version: str = DEFAULT_PROFILE_VERSION,
    qualification_status: QualificationStatus = "draft",
) -> list[GeneratedProfileArtifact]:
    """Compile the default bundled analysis profile artifacts."""
    if qualification_status not in {"draft", "qualified"}:
        raise CompileAnalysisProfilesError(
            f"unsupported qualification_status: {qualification_status!r}"
        )

    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()
    generated_at = parse_manifest_generated_at(
        resolved_manifest_path,
        project_root=root,
        qualification_status=qualification_status,
    )

    builders: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        (
            "fedramp-20x-program-class-c.json",
            lambda: compile_fedramp_20x_class_c_profile(
                manifest_path=resolved_manifest_path,
                project_root=root,
                generated_at=generated_at,
                profile_version=profile_version,
            ),
        ),
        (
            "fedramp-rev5-transition-low.json",
            lambda: compile_fedramp_rev5_transition_profile(
                impact_level="low",
                manifest_path=resolved_manifest_path,
                project_root=root,
                generated_at=generated_at,
                profile_version=profile_version,
            ),
        ),
        (
            "fedramp-rev5-transition-moderate.json",
            lambda: compile_fedramp_rev5_transition_profile(
                impact_level="moderate",
                manifest_path=resolved_manifest_path,
                project_root=root,
                generated_at=generated_at,
                profile_version=profile_version,
            ),
        ),
        (
            "fedramp-rev5-transition-high.json",
            lambda: compile_fedramp_rev5_transition_profile(
                impact_level="high",
                manifest_path=resolved_manifest_path,
                project_root=root,
                generated_at=generated_at,
                profile_version=profile_version,
            ),
        ),
    ]

    artifacts: list[GeneratedProfileArtifact] = []
    for filename, builder in builders:
        profile = builder()
        if profile.get("qualification_status") != "draft":
            raise CompileAnalysisProfilesError(
                f"{filename} must compile as qualification_status draft before promotion"
            )
        if qualification_status == "qualified":
            profile = dict(profile)
            profile["qualification_status"] = "qualified"
        artifacts.append(
            GeneratedProfileArtifact(
                filename=filename,
                payload=serialize_profile_document(profile),
            )
        )
    return artifacts


def resolve_output_path(*, output_dir: Path, filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise CompileAnalysisProfilesError(
            f"unsafe profile output filename: {filename!r}"
        )
    if ".." in Path(filename).parts:
        raise CompileAnalysisProfilesError(
            f"unsafe profile output filename: {filename!r}"
        )

    resolved_output_dir = output_dir.resolve()
    candidate = (resolved_output_dir / filename).resolve()
    try:
        candidate.relative_to(resolved_output_dir)
    except ValueError as exc:
        raise CompileAnalysisProfilesError(
            f"profile output path escapes output directory: {filename!r}"
        ) from exc
    return candidate


def write_profile_artifact(*, output_dir: Path, artifact: GeneratedProfileArtifact) -> Path:
    final_path = resolve_output_path(output_dir=output_dir, filename=artifact.filename)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(
        f".{final_path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    )

    try:
        with temp_path.open("xb") as handle:
            handle.write(artifact.payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
        temp_path = None
        _fsync_directory(final_path.parent)
    except OSError as exc:
        raise CompileAnalysisProfilesError(
            f"failed to write profile artifact {artifact.filename}"
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return final_path


def write_profile_artifacts(
    *,
    output_dir: Path,
    artifacts: list[GeneratedProfileArtifact],
) -> list[Path]:
    return [
        write_profile_artifact(output_dir=output_dir, artifact=artifact)
        for artifact in artifacts
    ]


def check_profile_artifacts(
    *,
    output_dir: Path,
    artifacts: list[GeneratedProfileArtifact],
) -> None:
    expected_names = {artifact.filename for artifact in artifacts}
    if len(expected_names) != len(artifacts):
        raise CompileAnalysisProfilesError("duplicate bundled profile filenames")

    for artifact in artifacts:
        path = resolve_output_path(output_dir=output_dir, filename=artifact.filename)
        if not path.is_file():
            raise CompileAnalysisProfilesError(
                f"missing committed profile artifact: {artifact.filename}"
            )
        existing = path.read_bytes()
        if existing != artifact.payload:
            raise CompileAnalysisProfilesError(
                f"committed profile artifact differs from generation: {artifact.filename}"
            )

    resolved_output_dir = output_dir.resolve()
    for path in sorted(resolved_output_dir.glob("*.json")):
        if path.name not in expected_names:
            raise CompileAnalysisProfilesError(
                f"unexpected profile artifact in output directory: {path.name}"
            )


def compile_and_write_profiles(
    *,
    manifest_path: Path,
    project_root: Path,
    output_dir: Path,
    check_only: bool = False,
    qualification_status: QualificationStatus = "draft",
) -> list[GeneratedProfileArtifact]:
    artifacts = compile_bundled_profile_artifacts(
        manifest_path=manifest_path,
        project_root=project_root,
        qualification_status=qualification_status,
    )
    if check_only:
        check_profile_artifacts(output_dir=output_dir, artifacts=artifacts)
        return artifacts
    write_profile_artifacts(output_dir=output_dir, artifacts=artifacts)
    return artifacts


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    directory_fd = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile deterministic draft analysis profile artifacts from "
            "the verified authority manifest."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify committed profile artifacts match generation without writing files."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated profile artifacts (default: reference/profiles).",
    )
    parser.add_argument(
        "--qualification-status",
        choices=("draft", "qualified"),
        default="draft",
        help=(
            "Requested bundled profile qualification_status. "
            "qualified requires an approved authority manifest."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        project_root = find_project_root()
        manifest_path = default_manifest_path(project_root=project_root)
        output_dir = (
            args.output_dir.resolve()
            if args.output_dir is not None
            else default_output_dir(project_root=project_root)
        )
        artifacts = compile_and_write_profiles(
            manifest_path=manifest_path,
            project_root=project_root,
            output_dir=output_dir,
            check_only=args.check,
            qualification_status=args.qualification_status,
        )
    except CompileAnalysisProfilesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    action = "checked" if args.check else "wrote"
    print(f"{action} {len(artifacts)} profile artifact(s) under {output_dir}")
    for artifact in artifacts:
        print(
            f"  {artifact.filename}: "
            f"{artifact.size_bytes} bytes sha256={artifact.sha256}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
