"""Minimal factories for profile-parametrized workflow integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_service.auth_context import AuthenticatedPrincipal
from ato_service.package_revisions import CreatePackageRevisionInput
from tests.integration_support.postgres import CUSTOMER_ENTERPRISE_ID, ORIGIN

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "profile_artifacts"
DRAFT_FIXTURE = ROOT / "docs" / "contracts" / "fixtures" / "package-draft-document.valid.fisma-minimal.json"

PROFILE_FIXTURE_FILES: dict[str, Path] = {
    "fisma_agency_security": DRAFT_FIXTURE,
    "fedramp_20x_program": FIXTURES_DIR / "fedramp-20x-class-c-sealed.json",
    "fedramp_rev5_transition": FIXTURES_DIR / "fedramp-rev5-transition-sealed.json",
}

PROFILE_CASES = [
    pytest.param("fisma_agency_security", None, "moderate", id="fisma-agency-security"),
    pytest.param("fedramp_20x_program", "C", None, id="fedramp-20x-class-c"),
    pytest.param("fedramp_rev5_transition", None, "moderate", id="fedramp-rev5-transition"),
]


def make_principal(
    *,
    actor_id: str,
    groups: tuple[str, ...],
    csrf_token: str = "c" * 32,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        actor_id=actor_id,
        groups=groups,
        csrf_token=csrf_token,
        allowed_origins=(ORIGIN,),
    )


OWNER = make_principal(actor_id="owner@example.test", groups=("owners",))
REVIEWER = make_principal(actor_id="reviewer@example.test", groups=("owners", "reviewers"))
ASSESSOR = make_principal(actor_id="assessor@example.test", groups=("assessors",))
APPROVER = make_principal(actor_id="approver@example.test", groups=("approvers",))
OUTSIDER = make_principal(actor_id="outsider@example.test", groups=("public",))


def profile_fixture_bytes(profile_id: str) -> bytes:
    path = PROFILE_FIXTURE_FILES[profile_id]
    return path.read_bytes()


def profile_revision_input(
    *,
    profile_id: str,
    certification_class: str | None,
    impact_level: str | None,
) -> CreatePackageRevisionInput:
    return CreatePackageRevisionInput(
        parent_revision_id=None,
        profile_id=profile_id,
        certification_class=certification_class,
        impact_level=impact_level,
        data_origin="synthetic",
        sensitivity="internal_unclassified",
    )


def system_create_kwargs(*, display_name: str) -> dict[str, object]:
    return {
        "display_name": display_name,
        "external_system_id": None,
        "owner_group": "owners",
        "viewer_groups": ["viewers", "approvers"],
        "customer_enterprise_id": CUSTOMER_ENTERPRISE_ID,
    }


def minimal_synthetic_manifest(profile_id: str) -> bytes:
    document = json.loads(profile_fixture_bytes(profile_id).decode("utf-8"))
    document.setdefault("package", {})["profile_id"] = profile_id
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
