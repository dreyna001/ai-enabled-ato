"""Semantic authority validation for analysis profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ato_service.authority_catalog import (
    AuthorityCatalogError,
    authority_sources_by_id,
    load_json_authority_archive_member,
    load_json_authority_source,
    resolve_json_pointer,
)
from ato_service.authority_manifest import (
    AuthorityManifestVerificationError,
    verify_authority_manifest,
)


class AnalysisProfileSemanticError(ValueError):
    """Raised when profile authority semantics fail validation."""


def validate_analysis_profile_semantics(
    profile: dict[str, Any],
    *,
    manifest_path: Path,
    project_root: Path,
) -> None:
    """Validate profile authority references against a verified authority manifest."""
    try:
        manifest = verify_authority_manifest(manifest_path, project_root=project_root)
    except AuthorityManifestVerificationError as exc:
        raise AnalysisProfileSemanticError(str(exc)) from exc
    except AuthorityCatalogError as exc:
        raise AnalysisProfileSemanticError(str(exc)) from exc

    try:
        sources_by_id = authority_sources_by_id(manifest)
    except AuthorityCatalogError as exc:
        raise AnalysisProfileSemanticError(str(exc)) from exc

    if not isinstance(profile, dict):
        raise AnalysisProfileSemanticError("analysis profile must be a JSON object")

    profile_manifest_id = profile.get("authority_manifest_id")
    if not isinstance(profile_manifest_id, str) or not profile_manifest_id:
        raise AnalysisProfileSemanticError(
            "analysis profile must declare authority_manifest_id"
        )
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id:
        raise AnalysisProfileSemanticError(
            "verified authority manifest must declare manifest_id"
        )
    if profile_manifest_id != manifest_id:
        raise AnalysisProfileSemanticError(
            "analysis profile authority_manifest_id "
            f"{profile_manifest_id!r} does not match authority manifest "
            f"manifest_id {manifest_id!r}"
        )

    _validate_unique_string_ids(
        profile.get("assessment_items"),
        array_name="assessment_items",
        id_field="assessment_item_id",
    )
    _validate_unique_string_ids(
        profile.get("artifact_requirements"),
        array_name="artifact_requirements",
        id_field="artifact_id",
    )
    _validate_unique_string_ids(
        profile.get("cadence_rules"),
        array_name="cadence_rules",
        id_field="cadence_rule_id",
    )
    _validate_profile_authority_refs(
        profile,
        manifest=manifest,
        project_root=project_root,
        sources_by_id=sources_by_id,
    )
    _validate_official_schema_authority_ids(
        profile.get("artifact_requirements"),
        sources_by_id=sources_by_id,
    )
    return None


def _validate_unique_string_ids(
    items: Any,
    *,
    array_name: str,
    id_field: str,
) -> None:
    if not isinstance(items, list):
        raise AnalysisProfileSemanticError(f"{array_name} must be a list")

    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AnalysisProfileSemanticError(
                f"{array_name} entry at index {index} must be an object"
            )
        item_id = item.get(id_field)
        if not isinstance(item_id, str) or not item_id:
            raise AnalysisProfileSemanticError(
                f"{array_name} entry at index {index} must declare {id_field}"
            )
        if item_id in seen:
            raise AnalysisProfileSemanticError(
                f"duplicate {id_field} {item_id!r} in {array_name}"
            )
        seen.add(item_id)


def _validate_profile_authority_refs(
    profile: dict[str, Any],
    *,
    manifest: dict[str, Any],
    project_root: Path,
    sources_by_id: dict[str, dict[str, Any]],
) -> None:
    authority_cache: dict[tuple[str, str | None], dict[str, Any]] = {}

    assessment_items = profile.get("assessment_items")
    if not isinstance(assessment_items, list):
        raise AnalysisProfileSemanticError("assessment_items must be a list")
    for index, item in enumerate(assessment_items):
        if not isinstance(item, dict):
            raise AnalysisProfileSemanticError(
                f"assessment_items entry at index {index} must be an object"
            )
        item_id = item.get("assessment_item_id")
        context = (
            f"assessment_items[{item_id!r}]"
            if isinstance(item_id, str) and item_id
            else f"assessment_items entry at index {index}"
        )
        _validate_authority_refs_list(
            item.get("authority_refs"),
            context=context,
            manifest=manifest,
            project_root=project_root,
            sources_by_id=sources_by_id,
            authority_cache=authority_cache,
        )

    artifact_requirements = profile.get("artifact_requirements")
    if not isinstance(artifact_requirements, list):
        raise AnalysisProfileSemanticError("artifact_requirements must be a list")
    for index, artifact in enumerate(artifact_requirements):
        if not isinstance(artifact, dict):
            raise AnalysisProfileSemanticError(
                f"artifact_requirements entry at index {index} must be an object"
            )
        artifact_id = artifact.get("artifact_id")
        context = (
            f"artifact_requirements[{artifact_id!r}]"
            if isinstance(artifact_id, str) and artifact_id
            else f"artifact_requirements entry at index {index}"
        )
        _validate_authority_refs_list(
            artifact.get("authority_refs"),
            context=context,
            manifest=manifest,
            project_root=project_root,
            sources_by_id=sources_by_id,
            authority_cache=authority_cache,
        )

    cadence_rules = profile.get("cadence_rules")
    if not isinstance(cadence_rules, list):
        raise AnalysisProfileSemanticError("cadence_rules must be a list")
    for index, rule in enumerate(cadence_rules):
        if not isinstance(rule, dict):
            raise AnalysisProfileSemanticError(
                f"cadence_rules entry at index {index} must be an object"
            )
        rule_id = rule.get("cadence_rule_id")
        context = (
            f"cadence_rules[{rule_id!r}]"
            if isinstance(rule_id, str) and rule_id
            else f"cadence_rules entry at index {index}"
        )
        _validate_authority_refs_list(
            rule.get("authority_refs"),
            context=context,
            manifest=manifest,
            project_root=project_root,
            sources_by_id=sources_by_id,
            authority_cache=authority_cache,
        )


def _validate_authority_refs_list(
    refs: Any,
    *,
    context: str,
    manifest: dict[str, Any],
    project_root: Path,
    sources_by_id: dict[str, dict[str, Any]],
    authority_cache: dict[tuple[str, str | None], dict[str, Any]],
) -> None:
    if not isinstance(refs, list):
        raise AnalysisProfileSemanticError(f"{context} authority_refs must be a list")

    for index, ref in enumerate(refs):
        _validate_authority_ref(
            ref,
            context=f"{context} authority_refs[{index}]",
            manifest=manifest,
            project_root=project_root,
            sources_by_id=sources_by_id,
            authority_cache=authority_cache,
        )


def _validate_authority_ref(
    ref: Any,
    *,
    context: str,
    manifest: dict[str, Any],
    project_root: Path,
    sources_by_id: dict[str, dict[str, Any]],
    authority_cache: dict[tuple[str, str | None], dict[str, Any]],
) -> None:
    if not isinstance(ref, dict):
        raise AnalysisProfileSemanticError(
            f"{context} authority_refs entry must be an object"
        )

    authority_id = ref.get("authority_id")
    if not isinstance(authority_id, str) or not authority_id:
        raise AnalysisProfileSemanticError(
            f"{context} authority_refs entry must declare authority_id"
        )

    source_pointer = ref.get("source_pointer")
    if not isinstance(source_pointer, str) or not source_pointer:
        raise AnalysisProfileSemanticError(
            f"{context} authority_refs entry must declare source_pointer"
        )

    archive_member: str | None = None
    if "archive_member" in ref:
        raw_archive_member = ref.get("archive_member")
        if not isinstance(raw_archive_member, str) or not raw_archive_member:
            raise AnalysisProfileSemanticError(
                f"{context} authority_refs entry archive_member "
                "must be a non-empty string"
            )
        archive_member = raw_archive_member

    if authority_id not in sources_by_id:
        raise AnalysisProfileSemanticError(
            f"{context} references unknown authority_id {authority_id!r}"
        )

    cache_key = (authority_id, archive_member)
    if cache_key not in authority_cache:
        try:
            if archive_member is not None:
                _member_name, document = load_json_authority_archive_member(
                    manifest=manifest,
                    authority_id=authority_id,
                    project_root=project_root,
                    member_suffix=archive_member,
                )
            else:
                document = load_json_authority_source(
                    manifest=manifest,
                    authority_id=authority_id,
                    project_root=project_root,
                )
        except AuthorityCatalogError as exc:
            if archive_member is not None:
                raise AnalysisProfileSemanticError(
                    f"{context} authority_ref {authority_id!r} "
                    f"archive_member {archive_member!r}: {exc}"
                ) from exc
            raise AnalysisProfileSemanticError(str(exc)) from exc
        authority_cache[cache_key] = document

    document = authority_cache[cache_key]
    try:
        resolve_json_pointer(document, source_pointer)
    except AuthorityCatalogError as exc:
        if archive_member is not None:
            raise AnalysisProfileSemanticError(
                f"{context} authority_ref {authority_id!r} "
                f"archive_member {archive_member!r} {source_pointer!r}: {exc}"
            ) from exc
        raise AnalysisProfileSemanticError(
            f"{context} authority_ref {authority_id!r} {source_pointer!r}: {exc}"
        ) from exc


def _validate_official_schema_authority_ids(
    artifact_requirements: Any,
    *,
    sources_by_id: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(artifact_requirements, list):
        raise AnalysisProfileSemanticError("artifact_requirements must be a list")

    for index, artifact in enumerate(artifact_requirements):
        if not isinstance(artifact, dict):
            raise AnalysisProfileSemanticError(
                f"artifact_requirements entry at index {index} must be an object"
            )
        official_id = artifact.get("official_schema_authority_id")
        if official_id is None:
            continue
        if not isinstance(official_id, str) or not official_id:
            raise AnalysisProfileSemanticError(
                "artifact_requirements entry at index "
                f"{index} has malformed official_schema_authority_id"
            )
        if official_id not in sources_by_id:
            artifact_id = artifact.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                context = f"artifact_requirements[{artifact_id!r}]"
            else:
                context = f"artifact_requirements entry at index {index}"
            raise AnalysisProfileSemanticError(
                f"{context} references unknown official_schema_authority_id "
                f"{official_id!r}"
            )
