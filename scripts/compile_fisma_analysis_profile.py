#!/usr/bin/env python3
"""Compile one customer FISMA agency security analysis profile artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ato_service.analysis_profile import analysis_profile_sha256
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)
from ato_service.fisma_control_inventory import (
    FismaControlInventory,
    FismaControlInventoryError,
    load_fisma_control_inventory,
)
from ato_service.fisma_profile import FismaProfileError, compile_fisma_agency_security_profile

DEFAULT_PROFILE_VERSION = "1.0.0"
QualificationStatus = Literal["draft", "qualified"]


class CompileFismaAnalysisProfileError(RuntimeError):
    """Raised when customer FISMA profile compilation or verification fails."""


@dataclass(frozen=True, slots=True)
class CompiledFismaProfileArtifact:
    profile: dict[str, Any]
    payload: bytes
    inventory: FismaControlInventory
    output_path: Path

    @property
    def output_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def canonical_profile_digest(self) -> str:
        return analysis_profile_sha256(self.profile)


def _load_compile_analysis_profiles_module():
    script_path = Path(__file__).resolve().parent / "compile_analysis_profiles.py"
    module_name = "compile_analysis_profiles"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise CompileFismaAnalysisProfileError(
            "failed to load compile_analysis_profiles helpers"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_compile_helpers = _load_compile_analysis_profiles_module()


def find_project_root(start: Path | None = None) -> Path:
    return _compile_helpers.find_project_root(start)


def default_manifest_path(*, project_root: Path) -> Path:
    return _compile_helpers.default_manifest_path(project_root=project_root)


def serialize_profile_document(profile: dict[str, Any]) -> bytes:
    return _compile_helpers.serialize_profile_document(profile)


def parse_iso_timestamp(value: str, *, field_name: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise CompileFismaAnalysisProfileError(f"{field_name} must not be empty")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CompileFismaAnalysisProfileError(
            f"{field_name} is not parseable: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise CompileFismaAnalysisProfileError(
            f"{field_name} must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def assert_qualified_compilation_prerequisites(
    *,
    inventory: FismaControlInventory,
    manifest_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Fail closed unless qualified compilation prerequisites are satisfied."""
    if inventory.status != "approved":
        raise CompileFismaAnalysisProfileError(
            "inventory status "
            f"{inventory.status!r} is not approved; "
            "qualified FISMA profiles require an approved customer inventory"
        )

    try:
        manifest = verify_authority_manifest(
            manifest_path.resolve(),
            project_root=project_root.resolve(),
        )
    except AuthorityManifestVerificationError as exc:
        raise CompileFismaAnalysisProfileError(str(exc)) from exc

    manifest_status = manifest.get("status")
    if manifest_status != "approved":
        raise CompileFismaAnalysisProfileError(
            "verified authority manifest status "
            f"{manifest_status!r} is not approved; "
            "qualified FISMA profiles require HS-001 authority approval"
        )
    return manifest


def apply_qualification_status(
    profile: dict[str, Any],
    *,
    qualification_status: QualificationStatus,
) -> dict[str, Any]:
    """Return the profile with the requested qualification status applied."""
    if qualification_status == "draft":
        if profile.get("qualification_status") != "draft":
            raise CompileFismaAnalysisProfileError(
                "compiled profile must remain qualification_status draft"
            )
        return profile

    if profile.get("qualification_status") != "draft":
        raise CompileFismaAnalysisProfileError(
            "compiled profile must remain qualification_status draft before promotion"
        )

    qualified_profile = dict(profile)
    qualified_profile["qualification_status"] = "qualified"
    return qualified_profile


def resolve_generated_at(
    *,
    inventory: FismaControlInventory,
    manifest_path: Path,
    project_root: Path,
) -> datetime:
    if inventory.status == "approved":
        if inventory.approved_at is None:
            raise CompileFismaAnalysisProfileError(
                "approved inventory must declare approved_at"
            )
        return parse_iso_timestamp(inventory.approved_at, field_name="approved_at")
    return _compile_helpers.parse_manifest_generated_at(
        manifest_path,
        project_root=project_root,
    )


def resolve_explicit_output_path(output_path: Path) -> Path:
    if "\0" in str(output_path):
        raise CompileFismaAnalysisProfileError("output path is malformed")

    expanded = output_path.expanduser()
    if ".." in expanded.parts:
        raise CompileFismaAnalysisProfileError(
            f"output path must not contain parent traversal: {output_path}"
        )
    if expanded.is_symlink():
        raise CompileFismaAnalysisProfileError("output path must not be a symlink")
    if expanded.exists() and expanded.is_dir():
        raise CompileFismaAnalysisProfileError("output path must not be a directory")

    resolved = expanded.resolve()
    if resolved.is_symlink():
        raise CompileFismaAnalysisProfileError("output path must not be a symlink")

    for component in resolved.parents:
        if component.is_symlink():
            raise CompileFismaAnalysisProfileError(
                "output path must not traverse a symlink component"
            )
        if component == component.anchor:
            break
    return resolved


def compile_fisma_profile_artifact(
    *,
    inventory_path: Path,
    output_path: Path,
    project_root: Path,
    manifest_path: Path,
    require_approved_inventory: bool = False,
    qualification_status: QualificationStatus = "draft",
    profile_version: str = DEFAULT_PROFILE_VERSION,
) -> CompiledFismaProfileArtifact:
    root = project_root.resolve()
    resolved_manifest_path = manifest_path.resolve()
    resolved_output_path = resolve_explicit_output_path(output_path)

    try:
        inventory = load_fisma_control_inventory(
            inventory_path,
            project_root=root,
        )
    except FismaControlInventoryError as exc:
        raise CompileFismaAnalysisProfileError(str(exc)) from exc

    effective_require_approved_inventory = (
        require_approved_inventory or qualification_status == "qualified"
    )
    if effective_require_approved_inventory and inventory.status != "approved":
        raise CompileFismaAnalysisProfileError(
            "inventory status "
            f"{inventory.status!r} is not approved; "
            "approved inventory is required for qualified output or "
            "--require-approved-inventory production preparation"
        )

    if qualification_status == "qualified":
        assert_qualified_compilation_prerequisites(
            inventory=inventory,
            manifest_path=resolved_manifest_path,
            project_root=root,
        )

    generated_at = resolve_generated_at(
        inventory=inventory,
        manifest_path=resolved_manifest_path,
        project_root=root,
    )

    try:
        profile = compile_fisma_agency_security_profile(
            inventory=inventory,
            manifest_path=resolved_manifest_path,
            project_root=root,
            generated_at=generated_at,
            profile_version=profile_version,
        )
    except FismaProfileError as exc:
        raise CompileFismaAnalysisProfileError(str(exc)) from exc

    profile = apply_qualification_status(
        profile,
        qualification_status=qualification_status,
    )

    payload = serialize_profile_document(profile)
    return CompiledFismaProfileArtifact(
        profile=profile,
        payload=payload,
        inventory=inventory,
        output_path=resolved_output_path,
    )


def write_profile_payload(*, output_path: Path, payload: bytes) -> Path:
    final_path = resolve_explicit_output_path(output_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(
        f".{final_path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    )

    try:
        with temp_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, final_path)
        temp_path = None
        _fsync_directory(final_path.parent)
    except OSError as exc:
        raise CompileFismaAnalysisProfileError(
            f"failed to write profile artifact {final_path}"
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return final_path


def check_profile_payload(*, output_path: Path, payload: bytes) -> Path:
    final_path = resolve_explicit_output_path(output_path)
    if not final_path.is_file():
        raise CompileFismaAnalysisProfileError(
            f"missing profile artifact: {final_path}"
        )
    existing = final_path.read_bytes()
    if existing != payload:
        raise CompileFismaAnalysisProfileError(
            f"profile artifact differs from generation: {final_path}"
        )
    return final_path


def runtime_config_snippet(*, output_path: Path, expected_sha256: str) -> dict[str, Any]:
    return {
        "FISMA_ANALYSIS_PROFILE_FILE_REFERENCE": {
            "path": str(output_path.resolve()),
            "expected_sha256": expected_sha256,
        }
    }


def format_runtime_config_snippet(snippet: dict[str, Any]) -> str:
    return json.dumps(snippet, indent=2, ensure_ascii=False)


def report_compilation(artifact: CompiledFismaProfileArtifact) -> None:
    profile = artifact.profile
    print(f"inventory_status: {artifact.inventory.status}")
    print(f"profile_id: {profile['profile_id']}")
    print(f"profile_version: {profile['profile_version']}")
    print(f"impact_level: {profile['impact_level']}")
    print(f"qualification_status: {profile['qualification_status']}")
    print(f"output_path: {artifact.output_path}")
    print(f"output_byte_sha256: {artifact.output_sha256}")
    print(f"canonical_profile_digest: {artifact.canonical_profile_digest}")
    print("runtime_config_snippet:")
    print(
        format_runtime_config_snippet(
            runtime_config_snippet(
                output_path=artifact.output_path,
                expected_sha256=artifact.output_sha256,
            )
        )
    )


def compile_and_write_fisma_profile(
    *,
    inventory_path: Path,
    output_path: Path,
    project_root: Path,
    manifest_path: Path,
    check_only: bool = False,
    require_approved_inventory: bool = False,
    qualification_status: QualificationStatus = "draft",
) -> CompiledFismaProfileArtifact:
    artifact = compile_fisma_profile_artifact(
        inventory_path=inventory_path,
        output_path=output_path,
        project_root=project_root,
        manifest_path=manifest_path,
        require_approved_inventory=require_approved_inventory,
        qualification_status=qualification_status,
    )
    if check_only:
        check_profile_payload(
            output_path=artifact.output_path,
            payload=artifact.payload,
        )
    else:
        write_profile_payload(
            output_path=artifact.output_path,
            payload=artifact.payload,
        )
    return artifact


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
            "Compile one customer FISMA agency security analysis profile from a "
            "control inventory and the verified project authority manifest. "
            "Default compiles emit qualification_status draft. Selecting qualified "
            "asserts completed human review and is blocked until HS-001 authority "
            "approval; it does not claim automatic regulatory approval. "
            "onprem_production runtime rejects draft profiles even when the "
            "inventory is approved."
        )
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
        help="Path to the customer FISMA control inventory JSON document.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Destination path for the compiled profile JSON. May be outside the "
            "project root; symlinks, directories, and parent traversal are rejected."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify the output file matches generation without writing or replacing "
            "bytes."
        ),
    )
    parser.add_argument(
        "--require-approved-inventory",
        action="store_true",
        help=(
            "Production preparation path: fail unless inventory status is approved. "
            "Implied when --qualification-status qualified is selected."
        ),
    )
    parser.add_argument(
        "--qualification-status",
        choices=("draft", "qualified"),
        default="draft",
        help=(
            "Profile qualification status to emit. draft is the default candidate "
            "compile. qualified requires approved inventory and a verified authority "
            "manifest with status approved; it asserts completed human review and "
            "remains blocked until HS-001 authority approval without claiming "
            "automatic regulatory approval."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        project_root = find_project_root()
        manifest_path = default_manifest_path(project_root=project_root)
        artifact = compile_and_write_fisma_profile(
            inventory_path=args.inventory,
            output_path=args.output,
            project_root=project_root,
            manifest_path=manifest_path,
            check_only=args.check,
            require_approved_inventory=args.require_approved_inventory,
            qualification_status=args.qualification_status,
        )
    except CompileFismaAnalysisProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    action = "checked" if args.check else "wrote"
    print(f"{action} FISMA analysis profile")
    report_compilation(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
