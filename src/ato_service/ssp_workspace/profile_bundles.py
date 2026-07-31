"""Validated, immutable, offline SSP profile bundles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from jsonschema import Draft202012Validator

from ato_service.oscal_catalog import (
    OscalCatalogError,
    index_oscal_catalog_controls,
)

ImpactLevel = Literal["low", "moderate", "high"]

_MANIFEST_NAME = "manifest.json"
_SCHEMA_DIR = Path(__file__).with_name("schemas")
_REQUIRED_ROLES = frozenset({"catalog", "baselines", "ssp_requirements"})
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_MEMBER_BYTES = 10_485_760
_MAX_ARCHIVE_BYTES = 52_428_800
_MAX_ARCHIVE_MEMBERS = 64
_MAX_COMPRESSION_RATIO = 200
_NIST_RELEASE_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ProfileBundleError(ValueError):
    """Raised when a local SSP profile bundle is invalid."""


@dataclass(frozen=True, slots=True)
class ProfileSource:
    source_id: str
    title: str
    version: str
    reference: str


@dataclass(frozen=True, slots=True)
class ProfileBundleFile:
    role: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileManifest:
    schema_version: str
    profile_id: str
    profile_version: str
    nist_control_catalog_release: str
    display_name: str
    sources: tuple[ProfileSource, ...]
    files: tuple[ProfileBundleFile, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileControl:
    control_id: str
    title: str
    requirement_text: str
    catalog_pointer: str


_DEFAULT_IMPLEMENTATION_STATUSES: tuple[str, ...] = (
    "implemented",
    "partially_implemented",
    "planned",
    "not_implemented",
    "not_applicable",
    "unknown",
)
_DEFAULT_RESPONSIBILITIES: tuple[str, ...] = (
    "system_specific",
    "hybrid",
    "inherited",
    "unknown",
)
_DEFAULT_QUESTION_OWNER_TYPES: tuple[str, ...] = (
    "isso",
    "agency",
    "technical",
    "system_owner",
)

CoverageKind = Literal["ssp_item", "controls"]


@dataclass(frozen=True, slots=True)
class ControlResponsePolicy:
    """Profile-defined control response enums and agent evidence rules."""

    implementation_statuses: tuple[str, ...]
    responsibilities: tuple[str, ...]
    question_owner_types: tuple[str, ...]
    evidence_required_for_agent_statement: bool


@dataclass(frozen=True, slots=True)
class ImplementationStatementDeterministicPolicy:
    """Profile-owned deterministic statement enforcement flags."""

    reject_oscal_parameter_insert_syntax: bool
    require_question_for_unresolved_parameterized_controls: bool
    require_evidence_for_agent_non_unknown_claims: bool
    require_statement_gap_or_question_before_approval: bool
    semantic_quality_findings_are_advisory: bool


@dataclass(frozen=True, slots=True)
class ImplementationStatementAgentInstructions:
    """Profile-owned agent drafting and review instructions."""

    statement_content: tuple[str, ...]
    organization_defined_parameters: tuple[str, ...]
    inherited_and_hybrid_responsibility: tuple[str, ...]
    semantic_review: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImplementationStatementAuthorityRef:
    source_id: str
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImplementationStatementPolicy:
    """Versioned control implementation statement policy bound to the profile."""

    policy_version: str
    deterministic: ImplementationStatementDeterministicPolicy
    agent_instructions: ImplementationStatementAgentInstructions
    authority_refs: tuple[ImplementationStatementAuthorityRef, ...]


@dataclass(frozen=True, slots=True)
class StandardCoverage:
    source_id: str
    requirement_id: str
    title: str
    coverage_kind: CoverageKind
    item_ids: tuple[str, ...]
    required: bool


@dataclass(frozen=True, slots=True)
class SspRequiredItem:
    """SSP outline item policy.

    ``min_length`` applies to ``value_type='string'`` as minimum trimmed character
    count, and to ``value_type='string_list'`` as the minimum number of list entries.
    """

    item_id: str
    title: str
    value_type: Literal["string", "string_list"]
    min_length: int | None
    allowed_values: tuple[str, ...]
    evidence_required_for_agent: bool
    required: bool = True
    standard_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileBundle:
    """Validated bundle content detached from mutable JSON documents."""

    manifest: ProfileManifest
    catalog_controls: tuple[ProfileControl, ...]
    low_control_ids: tuple[str, ...]
    moderate_control_ids: tuple[str, ...]
    high_control_ids: tuple[str, ...]
    ssp_required_items: tuple[SspRequiredItem, ...]
    control_response: ControlResponsePolicy
    standard_coverage: tuple[StandardCoverage, ...]
    implementation_statement_policy: ImplementationStatementPolicy

    def control_ids_for(self, impact_level: ImpactLevel) -> tuple[str, ...]:
        if impact_level == "low":
            return self.low_control_ids
        if impact_level == "moderate":
            return self.moderate_control_ids
        if impact_level == "high":
            return self.high_control_ids
        raise ProfileBundleError(
            f"unsupported impact level {impact_level!r}; expected low, moderate, or high"
        )


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile_id: str
    profile_version: str
    nist_control_catalog_release: str
    manifest_sha256: str
    impact_level: ImpactLevel
    sources: tuple[ProfileSource, ...]
    controls: tuple[ProfileControl, ...]
    ssp_required_items: tuple[SspRequiredItem, ...]
    control_response: ControlResponsePolicy
    standard_coverage: tuple[StandardCoverage, ...]
    implementation_statement_policy: ImplementationStatementPolicy


@dataclass(frozen=True, slots=True)
class SourceVersionChange:
    source_id: str
    old_version: str | None
    new_version: str | None


@dataclass(frozen=True, slots=True)
class ProfileDiff:
    old_profile_version: str
    new_profile_version: str
    impact_level: ImpactLevel
    added_control_ids: tuple[str, ...]
    removed_control_ids: tuple[str, ...]
    changed_control_ids: tuple[str, ...]
    added_ssp_item_ids: tuple[str, ...]
    removed_ssp_item_ids: tuple[str, ...]
    changed_ssp_item_ids: tuple[str, ...]
    source_version_changes: tuple[SourceVersionChange, ...]
    implementation_statement_policy_changed: bool = False
    old_implementation_statement_policy_version: str | None = None
    new_implementation_statement_policy_version: str | None = None


def load_profile_bundle(path: Path) -> ProfileBundle:
    """Load and validate a local directory or ZIP profile bundle.

    The function performs no network access and does not extract archives.
    """
    bundle_path = _resolve_local_bundle_path(path)
    if bundle_path.is_dir():
        manifest_bytes, read_member = _open_directory_bundle(bundle_path)
    else:
        manifest_bytes, archive_members = _open_zip_bundle(bundle_path)

        def read_member(member_path: str) -> bytes:
            try:
                return archive_members[member_path]
            except KeyError as exc:
                raise ProfileBundleError(
                    f"profile bundle file is missing: {member_path}"
                ) from exc

    manifest_document = _load_json_object(
        manifest_bytes,
        label="profile bundle manifest",
    )
    _validate_schema(
        manifest_document,
        schema_name="profile-bundle-manifest.schema.json",
        label="profile bundle manifest",
    )
    manifest = _build_manifest(manifest_document, manifest_bytes=manifest_bytes)

    documents: dict[str, dict[str, Any]] = {}
    for entry in manifest.files:
        member_bytes = read_member(entry.path)
        actual_digest = hashlib.sha256(member_bytes).hexdigest()
        if actual_digest != entry.sha256:
            raise ProfileBundleError(
                f"checksum mismatch for profile bundle file {entry.path!r}"
            )
        if entry.role in _REQUIRED_ROLES:
            documents[entry.role] = _load_json_object(
                member_bytes,
                label=f"profile bundle file {entry.path!r}",
            )

    catalog_controls = _load_catalog_controls(documents["catalog"])
    control_ids_by_level = _load_baselines(
        documents["baselines"],
        catalog_controls=catalog_controls,
    )
    (
        ssp_required_items,
        control_response,
        standard_coverage,
        implementation_statement_policy,
    ) = _load_ssp_requirements(
        documents["ssp_requirements"],
        manifest_sources=manifest.sources,
    )

    return ProfileBundle(
        manifest=manifest,
        catalog_controls=catalog_controls,
        low_control_ids=control_ids_by_level["low"],
        moderate_control_ids=control_ids_by_level["moderate"],
        high_control_ids=control_ids_by_level["high"],
        ssp_required_items=ssp_required_items,
        control_response=control_response,
        standard_coverage=standard_coverage,
        implementation_statement_policy=implementation_statement_policy,
    )


def resolve_profile(
    bundle: ProfileBundle,
    impact_level: ImpactLevel,
) -> ResolvedProfile:
    """Resolve one immutable Low, Moderate, or High profile."""
    selected_ids = bundle.control_ids_for(impact_level)
    controls_by_id = {
        control.control_id: control for control in bundle.catalog_controls
    }
    return ResolvedProfile(
        profile_id=bundle.manifest.profile_id,
        profile_version=bundle.manifest.profile_version,
        nist_control_catalog_release=bundle.manifest.nist_control_catalog_release,
        manifest_sha256=bundle.manifest.sha256,
        impact_level=impact_level,
        sources=bundle.manifest.sources,
        controls=tuple(controls_by_id[control_id] for control_id in selected_ids),
        ssp_required_items=bundle.ssp_required_items,
        control_response=bundle.control_response,
        standard_coverage=bundle.standard_coverage,
        implementation_statement_policy=bundle.implementation_statement_policy,
    )


def diff_profiles(old: ResolvedProfile, new: ResolvedProfile) -> ProfileDiff:
    """Return a deterministic semantic diff between two resolved versions."""
    if old.profile_id != new.profile_id:
        raise ProfileBundleError("profile diff requires matching profile_id values")
    if old.impact_level != new.impact_level:
        raise ProfileBundleError("profile diff requires matching impact levels")

    old_controls = {control.control_id: control for control in old.controls}
    new_controls = {control.control_id: control for control in new.controls}
    old_control_ids = set(old_controls)
    new_control_ids = set(new_controls)

    old_items = {item.item_id: item for item in old.ssp_required_items}
    new_items = {item.item_id: item for item in new.ssp_required_items}
    old_item_ids = set(old_items)
    new_item_ids = set(new_items)

    old_sources = {source.source_id: source.version for source in old.sources}
    new_sources = {source.source_id: source.version for source in new.sources}

    policy_changed = _statement_policy_semantics(
        old.implementation_statement_policy
    ) != _statement_policy_semantics(new.implementation_statement_policy)

    return ProfileDiff(
        old_profile_version=old.profile_version,
        new_profile_version=new.profile_version,
        impact_level=old.impact_level,
        added_control_ids=tuple(sorted(new_control_ids - old_control_ids)),
        removed_control_ids=tuple(sorted(old_control_ids - new_control_ids)),
        changed_control_ids=tuple(
            sorted(
                control_id
                for control_id in old_control_ids & new_control_ids
                if _control_semantics(old_controls[control_id])
                != _control_semantics(new_controls[control_id])
            )
        ),
        added_ssp_item_ids=tuple(sorted(new_item_ids - old_item_ids)),
        removed_ssp_item_ids=tuple(sorted(old_item_ids - new_item_ids)),
        changed_ssp_item_ids=tuple(
            sorted(
                item_id
                for item_id in old_item_ids & new_item_ids
                if old_items[item_id] != new_items[item_id]
            )
        ),
        source_version_changes=tuple(
            SourceVersionChange(
                source_id=source_id,
                old_version=old_sources.get(source_id),
                new_version=new_sources.get(source_id),
            )
            for source_id in sorted(set(old_sources) | set(new_sources))
            if old_sources.get(source_id) != new_sources.get(source_id)
        ),
        implementation_statement_policy_changed=policy_changed,
        old_implementation_statement_policy_version=(
            old.implementation_statement_policy.policy_version
            if policy_changed
            else None
        ),
        new_implementation_statement_policy_version=(
            new.implementation_statement_policy.policy_version
            if policy_changed
            else None
        ),
    )


def _control_semantics(control: ProfileControl) -> tuple[str, str]:
    return control.title, control.requirement_text


def _statement_policy_semantics(
    policy: ImplementationStatementPolicy,
) -> tuple[Any, ...]:
    deterministic = policy.deterministic
    instructions = policy.agent_instructions
    return (
        policy.policy_version,
        deterministic.reject_oscal_parameter_insert_syntax,
        deterministic.require_question_for_unresolved_parameterized_controls,
        deterministic.require_evidence_for_agent_non_unknown_claims,
        deterministic.require_statement_gap_or_question_before_approval,
        deterministic.semantic_quality_findings_are_advisory,
        instructions.statement_content,
        instructions.organization_defined_parameters,
        instructions.inherited_and_hybrid_responsibility,
        instructions.semantic_review,
        tuple((ref.source_id, ref.requirement_ids) for ref in policy.authority_refs),
    )


def _resolve_local_bundle_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise ProfileBundleError("profile bundle path must be a pathlib.Path")
    if "\0" in str(path):
        raise ProfileBundleError("profile bundle path is malformed")
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ProfileBundleError("profile bundle path must not be a symlink")
    resolved = expanded.resolve()
    if not resolved.exists():
        raise ProfileBundleError("profile bundle path does not exist")
    if not resolved.is_dir() and not resolved.is_file():
        raise ProfileBundleError(
            "profile bundle path must be a directory or ZIP archive"
        )
    return resolved


def _open_directory_bundle(
    root: Path,
) -> tuple[bytes, Callable[[str], bytes]]:
    manifest_path = root / _MANIFEST_NAME
    manifest_bytes = _read_safe_directory_member(
        root,
        manifest_path,
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )

    def read_member(member_path: str) -> bytes:
        normalized = _validated_member_path(member_path)
        return _read_safe_directory_member(
            root,
            root.joinpath(*normalized.parts),
            maximum_bytes=_MAX_MEMBER_BYTES,
        )

    return manifest_bytes, read_member


def _read_safe_directory_member(
    root: Path,
    path: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    if path.is_symlink():
        raise ProfileBundleError(
            f"profile bundle file must not be a symlink: {path.name}"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProfileBundleError(
            f"profile bundle file is missing or unreadable: {path.name}"
        ) from exc
    if root not in resolved.parents:
        raise ProfileBundleError("profile bundle file path escapes bundle directory")
    if not resolved.is_file():
        raise ProfileBundleError(
            f"profile bundle member must be a regular file: {path.name}"
        )
    try:
        size = resolved.stat().st_size
        if size > maximum_bytes:
            raise ProfileBundleError(
                f"profile bundle file exceeds {maximum_bytes} bytes: {path.name}"
            )
        return resolved.read_bytes()
    except OSError as exc:
        raise ProfileBundleError(
            f"profile bundle file is unreadable: {path.name}"
        ) from exc


def _open_zip_bundle(path: Path) -> tuple[bytes, dict[str, bytes]]:
    try:
        archive_size = path.stat().st_size
        if archive_size > _MAX_ARCHIVE_BYTES:
            raise ProfileBundleError(
                f"profile bundle archive exceeds {_MAX_ARCHIVE_BYTES} bytes"
            )
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise ProfileBundleError(
                    f"profile bundle archive exceeds {_MAX_ARCHIVE_MEMBERS} members"
                )
            total_uncompressed_bytes = sum(
                info.file_size for info in infos if not info.is_dir()
            )
            if total_uncompressed_bytes > _MAX_ARCHIVE_BYTES:
                raise ProfileBundleError(
                    "profile bundle archive has excessive uncompressed content"
                )
            members: dict[str, bytes] = {}
            for info in infos:
                if info.is_dir():
                    continue
                normalized = _validated_member_path(info.filename).as_posix()
                if normalized in members:
                    raise ProfileBundleError(
                        f"profile bundle archive has duplicate member {normalized!r}"
                    )
                _validate_zip_member(info)
                members[normalized] = archive.read(info)
    except ProfileBundleError:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise ProfileBundleError(
            "profile bundle archive is unreadable or is not a valid ZIP archive"
        ) from exc

    manifest_bytes = members.get(_MANIFEST_NAME)
    if manifest_bytes is None:
        raise ProfileBundleError("profile bundle manifest.json is missing")
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise ProfileBundleError(
            f"profile bundle manifest exceeds {_MAX_MANIFEST_BYTES} bytes"
        )
    return manifest_bytes, members


def _validate_zip_member(info: ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ProfileBundleError(
            f"encrypted profile bundle member is not supported: {info.filename!r}"
        )
    if info.file_size > _MAX_MEMBER_BYTES:
        raise ProfileBundleError(
            f"profile bundle member exceeds {_MAX_MEMBER_BYTES} bytes: {info.filename!r}"
        )
    if (
        info.compress_size > 0
        and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO
    ):
        raise ProfileBundleError(
            f"profile bundle member has an unsafe compression ratio: {info.filename!r}"
        )
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode == 0o120000:
        raise ProfileBundleError(
            f"profile bundle archive must not contain symlinks: {info.filename!r}"
        )


def _validated_member_path(raw_path: str) -> PurePosixPath:
    if not isinstance(raw_path, str) or not raw_path or "\0" in raw_path:
        raise ProfileBundleError("profile bundle member path is malformed")
    if "\\" in raw_path:
        raise ProfileBundleError(
            f"profile bundle member path must use forward slashes: {raw_path!r}"
        )
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ProfileBundleError(f"profile bundle member path is unsafe: {raw_path!r}")
    if path.as_posix() != raw_path:
        raise ProfileBundleError(
            f"profile bundle member path is not canonical: {raw_path!r}"
        )
    return path


def _build_manifest(
    document: dict[str, Any],
    *,
    manifest_bytes: bytes,
) -> ProfileManifest:
    sources = tuple(
        ProfileSource(
            source_id=source["source_id"],
            title=source["title"],
            version=source["version"],
            reference=source["reference"],
        )
        for source in document["sources"]
    )
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ProfileBundleError("profile bundle source_id values must be unique")

    files = tuple(
        ProfileBundleFile(
            role=file_entry["role"],
            path=_validated_member_path(file_entry["path"]).as_posix(),
            sha256=file_entry["sha256"],
        )
        for file_entry in document["files"]
    )
    roles = [entry.role for entry in files]
    if len(roles) != len(set(roles)):
        raise ProfileBundleError("profile bundle file roles must be unique")
    if not _REQUIRED_ROLES.issubset(roles):
        raise ProfileBundleError(
            "profile bundle must declare exactly one catalog, baselines, "
            "and ssp_requirements file"
        )
    paths = [entry.path for entry in files]
    if len(paths) != len(set(paths)):
        raise ProfileBundleError("profile bundle file paths must be unique")
    if _MANIFEST_NAME in paths:
        raise ProfileBundleError("manifest.json cannot checksum itself")

    nist_control_catalog_release = document["nist_control_catalog_release"]
    for source in sources:
        if source.source_id != "nist-sp-800-53":
            continue
        if not (
            _NIST_RELEASE_PATTERN.fullmatch(nist_control_catalog_release)
            and _NIST_RELEASE_PATTERN.fullmatch(source.version)
        ):
            continue
        if source.version != nist_control_catalog_release:
            raise ProfileBundleError(
                "nist_control_catalog_release must match the nist-sp-800-53 "
                f"source version; expected {source.version!r}, got "
                f"{nist_control_catalog_release!r}"
            )

    return ProfileManifest(
        schema_version=document["schema_version"],
        profile_id=document["profile_id"],
        profile_version=document["profile_version"],
        nist_control_catalog_release=nist_control_catalog_release,
        display_name=document["display_name"],
        sources=tuple(sorted(sources, key=lambda source: source.source_id)),
        files=tuple(sorted(files, key=lambda entry: entry.role)),
        sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _load_catalog_controls(
    document: dict[str, Any],
) -> tuple[ProfileControl, ...]:
    try:
        indexed = index_oscal_catalog_controls(document)
    except OscalCatalogError as exc:
        raise ProfileBundleError(f"profile catalog is invalid: {exc}") from exc
    return tuple(
        ProfileControl(
            control_id=record.normalized_id,
            title=record.title.strip(),
            requirement_text=record.requirement_text.strip(),
            catalog_pointer=record.catalog_pointer,
        )
        for _, record in sorted(indexed.items())
    )


def _load_baselines(
    document: dict[str, Any],
    *,
    catalog_controls: tuple[ProfileControl, ...],
) -> dict[ImpactLevel, tuple[str, ...]]:
    _validate_schema(
        document,
        schema_name="profile-baselines.schema.json",
        label="profile baselines",
    )
    catalog_by_id = {control.control_id: control for control in catalog_controls}
    catalog_ids = set(catalog_by_id)
    resolved: dict[ImpactLevel, tuple[str, ...]] = {}
    for impact_level in ("low", "moderate", "high"):
        control_ids = tuple(sorted(document[impact_level]))
        missing_ids = tuple(sorted(set(control_ids) - catalog_ids))
        if missing_ids:
            raise ProfileBundleError(
                f"{impact_level} baseline references controls missing from catalog: "
                + ", ".join(missing_ids)
            )
        missing_statements = tuple(
            control_id
            for control_id in control_ids
            if not catalog_by_id[control_id].requirement_text
        )
        if missing_statements:
            raise ProfileBundleError(
                f"{impact_level} baseline controls have missing statement prose: "
                + ", ".join(missing_statements)
            )
        resolved[impact_level] = control_ids
    return resolved


def default_implementation_statement_deterministic_policy() -> (
    ImplementationStatementDeterministicPolicy
):
    return ImplementationStatementDeterministicPolicy(
        reject_oscal_parameter_insert_syntax=True,
        require_question_for_unresolved_parameterized_controls=True,
        require_evidence_for_agent_non_unknown_claims=True,
        require_statement_gap_or_question_before_approval=True,
        semantic_quality_findings_are_advisory=True,
    )


def default_implementation_statement_agent_instructions() -> (
    ImplementationStatementAgentInstructions
):
    return ImplementationStatementAgentInstructions(
        statement_content=(),
        organization_defined_parameters=(
            "Never invent values for organization-defined parameters "
            "referenced in control requirement_text placeholders.",
            "Use direct evidence only when it explicitly supports a parameter "
            "value and cite supporting_fact_ids.",
            "When evidence does not support a parameter value, leave "
            "implementation_statement empty, keep status and responsibility "
            "unknown, and ask one concise control-targeted question for the "
            "missing agency or organization value.",
            "Never emit literal OSCAL placeholder syntax in "
            "implementation_statement text.",
        ),
        inherited_and_hybrid_responsibility=(
            "Use inherited or hybrid responsibility only when direct evidence "
            "supports that split.",
            "When evidence supports inherited or hybrid responsibility, "
            "describe the known provider or common portion and the "
            "system-specific portion separately.",
            "Never invent provider scope, inheritance boundaries, or "
            "shared-service details.",
            "When evidence does not support inheritance details, keep "
            "responsibility unknown and ask a targeted question.",
        ),
        semantic_review=(),
    )


def default_implementation_statement_policy() -> ImplementationStatementPolicy:
    return ImplementationStatementPolicy(
        policy_version="1.0.0",
        deterministic=default_implementation_statement_deterministic_policy(),
        agent_instructions=default_implementation_statement_agent_instructions(),
        authority_refs=(),
    )


def default_control_response_policy() -> ControlResponsePolicy:
    return ControlResponsePolicy(
        implementation_statuses=_DEFAULT_IMPLEMENTATION_STATUSES,
        responsibilities=_DEFAULT_RESPONSIBILITIES,
        question_owner_types=_DEFAULT_QUESTION_OWNER_TYPES,
        evidence_required_for_agent_statement=True,
    )


def _load_ssp_requirements(
    document: dict[str, Any],
    *,
    manifest_sources: tuple[ProfileSource, ...],
) -> tuple[
    tuple[SspRequiredItem, ...],
    ControlResponsePolicy,
    tuple[StandardCoverage, ...],
    ImplementationStatementPolicy,
]:
    _validate_schema(
        document,
        schema_name="profile-ssp-requirements.schema.json",
        label="SSP requirements",
    )
    items = tuple(
        SspRequiredItem(
            item_id=item["item_id"],
            title=item["title"].strip(),
            value_type=item["value_type"],
            min_length=item.get("min_length"),
            allowed_values=tuple(item.get("allowed_values", ())),
            evidence_required_for_agent=item["evidence_required_for_agent"],
            required=item.get("required", True),
            standard_refs=tuple(item.get("standard_refs", ())),
        )
        for item in document["items"]
    )
    item_ids = [item.item_id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ProfileBundleError("SSP requirement item_id values must be unique")

    control_response = _load_control_response(document.get("control_response"))
    standard_coverage = _load_standard_coverage(
        document.get("standard_coverage"),
        manifest_sources=manifest_sources,
        known_item_ids=set(item_ids),
    )
    _validate_ssp_requirement_semantics(
        items,
        standard_coverage=standard_coverage,
    )
    implementation_statement_policy = _load_implementation_statement_policy(
        document.get("implementation_statement_policy"),
        manifest_sources=manifest_sources,
        standard_coverage=standard_coverage,
    )
    return (
        tuple(sorted(items, key=lambda item: item.item_id)),
        control_response,
        standard_coverage,
        implementation_statement_policy,
    )


def _load_instruction_list(raw: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ProfileBundleError(f"{label} must be a non-empty array")
    values = tuple(str(value).strip() for value in raw)
    if any(not value for value in values):
        raise ProfileBundleError(f"{label} entries must be non-empty strings")
    if len(values) != len(set(values)):
        raise ProfileBundleError(f"{label} entries must be unique")
    return values


def _load_implementation_statement_policy(
    raw: dict[str, Any] | None,
    *,
    manifest_sources: tuple[ProfileSource, ...],
    standard_coverage: tuple[StandardCoverage, ...],
) -> ImplementationStatementPolicy:
    if raw is None:
        return default_implementation_statement_policy()
    if not isinstance(raw, dict):
        raise ProfileBundleError("implementation_statement_policy must be an object")
    policy_version = raw.get("policy_version")
    if policy_version != "1.0.0":
        raise ProfileBundleError(
            "implementation_statement_policy.policy_version is not supported: "
            f"{policy_version!r}"
        )
    deterministic_raw = raw.get("deterministic")
    if not isinstance(deterministic_raw, dict):
        raise ProfileBundleError(
            "implementation_statement_policy.deterministic must be an object"
        )

    def _bool_field(name: str) -> bool:
        value = deterministic_raw.get(name)
        if not isinstance(value, bool):
            raise ProfileBundleError(
                f"implementation_statement_policy.deterministic.{name} must be a boolean"
            )
        return value

    deterministic = ImplementationStatementDeterministicPolicy(
        reject_oscal_parameter_insert_syntax=_bool_field(
            "reject_oscal_parameter_insert_syntax"
        ),
        require_question_for_unresolved_parameterized_controls=_bool_field(
            "require_question_for_unresolved_parameterized_controls"
        ),
        require_evidence_for_agent_non_unknown_claims=_bool_field(
            "require_evidence_for_agent_non_unknown_claims"
        ),
        require_statement_gap_or_question_before_approval=_bool_field(
            "require_statement_gap_or_question_before_approval"
        ),
        semantic_quality_findings_are_advisory=_bool_field(
            "semantic_quality_findings_are_advisory"
        ),
    )
    instructions_raw = raw.get("agent_instructions")
    if not isinstance(instructions_raw, dict):
        raise ProfileBundleError(
            "implementation_statement_policy.agent_instructions must be an object"
        )
    agent_instructions = ImplementationStatementAgentInstructions(
        statement_content=_load_instruction_list(
            instructions_raw.get("statement_content"),
            label="implementation_statement_policy.agent_instructions.statement_content",
        ),
        organization_defined_parameters=_load_instruction_list(
            instructions_raw.get("organization_defined_parameters"),
            label=(
                "implementation_statement_policy.agent_instructions."
                "organization_defined_parameters"
            ),
        ),
        inherited_and_hybrid_responsibility=_load_instruction_list(
            instructions_raw.get("inherited_and_hybrid_responsibility"),
            label=(
                "implementation_statement_policy.agent_instructions."
                "inherited_and_hybrid_responsibility"
            ),
        ),
        semantic_review=_load_instruction_list(
            instructions_raw.get("semantic_review"),
            label="implementation_statement_policy.agent_instructions.semantic_review",
        ),
    )
    authority_refs_raw = raw.get("authority_refs")
    if not isinstance(authority_refs_raw, list):
        raise ProfileBundleError(
            "implementation_statement_policy.authority_refs must be an array"
        )
    manifest_source_ids = {source.source_id for source in manifest_sources}
    coverage_by_source: dict[str, set[str]] = {}
    for entry in standard_coverage:
        coverage_by_source.setdefault(entry.source_id, set()).add(entry.requirement_id)
    authority_refs: list[ImplementationStatementAuthorityRef] = []
    for index, entry in enumerate(authority_refs_raw):
        if not isinstance(entry, dict):
            raise ProfileBundleError(
                f"implementation_statement_policy.authority_refs[{index}] must be an object"
            )
        source_id = entry.get("source_id")
        requirement_ids_raw = entry.get("requirement_ids")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ProfileBundleError(
                f"implementation_statement_policy.authority_refs[{index}] must declare source_id"
            )
        source_id = source_id.strip()
        if source_id not in manifest_source_ids:
            raise ProfileBundleError(
                "implementation_statement_policy authority ref references unknown "
                f"source_id {source_id!r}"
            )
        if not isinstance(requirement_ids_raw, list):
            raise ProfileBundleError(
                f"implementation_statement_policy.authority_refs[{index}].requirement_ids "
                "must be an array"
            )
        requirement_ids = tuple(str(item).strip() for item in requirement_ids_raw)
        if requirement_ids and source_id not in coverage_by_source:
            raise ProfileBundleError(
                "implementation_statement_policy authority ref "
                f"{source_id!r} has requirement_ids but no standard_coverage entries"
            )
        unknown_ids = sorted(
            set(requirement_ids) - coverage_by_source.get(source_id, set())
        )
        if unknown_ids:
            raise ProfileBundleError(
                "implementation_statement_policy authority ref references unknown "
                f"requirement_ids for {source_id!r}: {', '.join(unknown_ids)}"
            )
        authority_refs.append(
            ImplementationStatementAuthorityRef(
                source_id=source_id,
                requirement_ids=requirement_ids,
            )
        )
    return ImplementationStatementPolicy(
        policy_version=policy_version,
        deterministic=deterministic,
        agent_instructions=agent_instructions,
        authority_refs=tuple(
            sorted(authority_refs, key=lambda ref: (ref.source_id, ref.requirement_ids))
        ),
    )


def _load_control_response(raw: dict[str, Any] | None) -> ControlResponsePolicy:
    if raw is None:
        return default_control_response_policy()
    implementation_statuses = _validated_enum_collection(
        raw.get("implementation_statuses"),
        label="control_response.implementation_statuses",
        require_unknown=True,
    )
    responsibilities = _validated_enum_collection(
        raw.get("responsibilities"),
        label="control_response.responsibilities",
        require_unknown=True,
    )
    question_owner_types = _validated_enum_collection(
        raw.get("question_owner_types"),
        label="control_response.question_owner_types",
        require_unknown=False,
    )
    evidence_required = raw.get("evidence_required_for_agent_statement")
    if not isinstance(evidence_required, bool):
        raise ProfileBundleError(
            "control_response.evidence_required_for_agent_statement must be a boolean"
        )
    return ControlResponsePolicy(
        implementation_statuses=implementation_statuses,
        responsibilities=responsibilities,
        question_owner_types=question_owner_types,
        evidence_required_for_agent_statement=evidence_required,
    )


def _load_standard_coverage(
    raw: list[Any] | None,
    *,
    manifest_sources: tuple[ProfileSource, ...],
    known_item_ids: set[str],
) -> tuple[StandardCoverage, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProfileBundleError("standard_coverage must be an array")
    manifest_source_ids = {source.source_id for source in manifest_sources}
    entries: list[StandardCoverage] = []
    requirement_ids: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ProfileBundleError(f"standard_coverage[{index}] must be an object")
        source_id = entry.get("source_id")
        requirement_id = entry.get("requirement_id")
        title = entry.get("title")
        coverage_kind = entry.get("coverage_kind")
        required = entry.get("required")
        item_ids_raw = entry.get("item_ids", [])
        if not isinstance(source_id, str) or not source_id.strip():
            raise ProfileBundleError(
                f"standard_coverage[{index}] must declare source_id"
            )
        if source_id not in manifest_source_ids:
            raise ProfileBundleError(
                f"standard_coverage requirement {requirement_id!r} references "
                f"unknown source_id {source_id!r}"
            )
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise ProfileBundleError(
                f"standard_coverage[{index}] must declare requirement_id"
            )
        if not isinstance(title, str) or not title.strip():
            raise ProfileBundleError(f"standard_coverage[{index}] must declare title")
        if coverage_kind not in {"ssp_item", "controls"}:
            raise ProfileBundleError(
                f"standard_coverage[{index}] has unsupported coverage_kind "
                f"{coverage_kind!r}"
            )
        if not isinstance(required, bool):
            raise ProfileBundleError(
                f"standard_coverage[{index}].required must be a boolean"
            )
        if coverage_kind == "controls":
            if item_ids_raw:
                raise ProfileBundleError(
                    f"standard_coverage {requirement_id!r} with coverage_kind "
                    "'controls' must not declare item_ids"
                )
            item_ids: tuple[str, ...] = ()
        else:
            if not isinstance(item_ids_raw, list) or not item_ids_raw:
                raise ProfileBundleError(
                    f"standard_coverage {requirement_id!r} with coverage_kind "
                    "'ssp_item' must declare item_ids"
                )
            item_ids = tuple(str(item_id) for item_id in item_ids_raw)
            unknown_ids = sorted(set(item_ids) - known_item_ids)
            if unknown_ids:
                raise ProfileBundleError(
                    f"standard_coverage {requirement_id!r} references unknown "
                    f"SSP item_ids: {', '.join(unknown_ids)}"
                )
        entries.append(
            StandardCoverage(
                source_id=source_id.strip(),
                requirement_id=requirement_id.strip(),
                title=title.strip(),
                coverage_kind=coverage_kind,  # type: ignore[arg-type]
                item_ids=item_ids,
                required=required,
            )
        )
        requirement_ids.append(requirement_id.strip())
    if len(requirement_ids) != len(set(requirement_ids)):
        raise ProfileBundleError(
            "standard_coverage requirement_id values must be unique"
        )
    return tuple(sorted(entries, key=lambda entry: entry.requirement_id))


def _validate_ssp_requirement_semantics(
    items: tuple[SspRequiredItem, ...],
    *,
    standard_coverage: tuple[StandardCoverage, ...],
) -> None:
    if not standard_coverage:
        return
    coverage_ids = {entry.requirement_id for entry in standard_coverage}
    for item in items:
        unknown_refs = sorted(set(item.standard_refs) - coverage_ids)
        if unknown_refs:
            raise ProfileBundleError(
                f"SSP requirement {item.item_id!r} has unknown standard_refs: "
                + ", ".join(unknown_refs)
            )
        if item.required and not item.standard_refs:
            raise ProfileBundleError(
                f"required SSP requirement {item.item_id!r} must declare "
                "at least one standard_ref when standard_coverage is present"
            )


def _validated_enum_collection(
    raw: Any,
    *,
    label: str,
    require_unknown: bool,
) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ProfileBundleError(f"{label} must be a non-empty array")
    values = tuple(str(value) for value in raw)
    if len(values) != len(set(values)):
        raise ProfileBundleError(f"{label} values must be unique")
    if require_unknown and "unknown" not in values:
        raise ProfileBundleError(f"{label} must include 'unknown'")
    return values


def _load_json_object(raw_bytes: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileBundleError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ProfileBundleError(f"{label} must be a JSON object")
    return document


@cache
def _schema_validator(schema_name: str) -> Draft202012Validator:
    schema_path = _SCHEMA_DIR / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileBundleError(
            f"profile bundle validator schema is unavailable: {schema_name}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_schema(
    document: dict[str, Any],
    *,
    schema_name: str,
    label: str,
) -> None:
    validator = _schema_validator(schema_name)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if not errors:
        return
    error = errors[0]
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    raise ProfileBundleError(
        f"{label} failed schema validation at {location}: {error.message}"
    )


__all__ = [
    "ControlResponsePolicy",
    "CoverageKind",
    "ImpactLevel",
    "ImplementationStatementAgentInstructions",
    "ImplementationStatementAuthorityRef",
    "ImplementationStatementDeterministicPolicy",
    "ImplementationStatementPolicy",
    "ProfileBundle",
    "ProfileBundleError",
    "ProfileBundleFile",
    "ProfileControl",
    "ProfileDiff",
    "ProfileManifest",
    "ProfileSource",
    "ResolvedProfile",
    "SourceVersionChange",
    "SspRequiredItem",
    "StandardCoverage",
    "default_control_response_policy",
    "default_implementation_statement_policy",
    "diff_profiles",
    "load_profile_bundle",
    "resolve_profile",
]
