"""Focused tests for profile-driven control_response on workspace envelopes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ato_service.ssp_workspace.profile_bundles import (
    ControlResponsePolicy,
    default_control_response_policy,
    load_profile_bundle,
    resolve_profile,
)
from ato_service.ssp_workspace.profiles import (
    deserialize_profile_bundle,
    resolve_stored_profile,
    serialize_profile_bundle,
)
from ato_service.ssp_workspace.service import _control_response_envelope

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_PROFILE = (
    PROJECT_ROOT / "reference" / "ssp_profiles" / "synthetic-fisma-rev5-1.0.0"
)


def test_control_response_envelope_serializes_sorted_profile_values() -> None:
    policy = ControlResponsePolicy(
        implementation_statuses=("planned", "implemented", "unknown"),
        responsibilities=("hybrid", "system_specific", "unknown"),
        question_owner_types=("technical", "isso"),
        evidence_required_for_agent_statement=False,
    )

    document = _control_response_envelope(policy)

    assert document == {
        "implementation_statuses": ["implemented", "planned", "unknown"],
        "responsibilities": ["hybrid", "system_specific", "unknown"],
        "question_owner_types": ["isso", "technical"],
        "evidence_required_for_agent_statement": False,
    }


def test_stored_profile_control_response_matches_bundle_defaults() -> None:
    bundle = load_profile_bundle(SYNTHETIC_PROFILE)
    stored = deserialize_profile_bundle(serialize_profile_bundle(bundle))
    profile_row = SimpleNamespace(bundle=serialize_profile_bundle(stored))
    resolved = resolve_stored_profile(profile_row, "low")
    defaults = default_control_response_policy()

    envelope = _control_response_envelope(resolved.control_response)

    assert envelope["implementation_statuses"] == sorted(
        defaults.implementation_statuses
    )
    assert envelope["responsibilities"] == sorted(defaults.responsibilities)
    assert envelope["question_owner_types"] == sorted(defaults.question_owner_types)
    assert envelope["evidence_required_for_agent_statement"] is True
    assert resolve_profile(bundle, "low").control_response == resolved.control_response
