"""Tests for unsupported authorization path and classified intake rejection."""

from __future__ import annotations

import pytest

from ato_service.authorization_boundary import (
    ClassifiedAuthorizationInputError,
    UnsupportedAuthorizationPathError,
    is_supported_authorization_path,
    require_supported_authorization_path,
    require_unclassified_sensitivity,
    validate_system_context_authorization_path,
)


@pytest.mark.parametrize(
    "authorization_path",
    [
        "dod",
        "dod_rmf",
        "emass",
        "ccri",
        "ic",
        "intelligence_community",
        "classified",
        "fedramp_agency_certification",
    ],
)
def test_unsupported_authorization_paths_are_rejected(authorization_path: str) -> None:
    assert not is_supported_authorization_path(authorization_path)
    with pytest.raises(UnsupportedAuthorizationPathError) as exc_info:
        require_supported_authorization_path(authorization_path)
    assert exc_info.value.error_code == "unsupported_authorization_path"


@pytest.mark.parametrize("authorization_path", ["agency", "fedramp"])
def test_supported_authorization_paths_are_allowed(authorization_path: str) -> None:
    assert is_supported_authorization_path(authorization_path)


def test_classified_sensitivity_is_rejected() -> None:
    with pytest.raises(ClassifiedAuthorizationInputError) as exc_info:
        require_unclassified_sensitivity("classified")
    assert exc_info.value.error_code == "classified_data_unsupported"


def test_draft_document_with_dod_path_is_rejected() -> None:
    with pytest.raises(UnsupportedAuthorizationPathError):
        validate_system_context_authorization_path(
            {"system": {"authorization_path": "dod_rmf"}}
        )
