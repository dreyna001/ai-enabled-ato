"""Tests for single-customer installation boundary resolution."""

from __future__ import annotations

import pytest

from ato_service.installation_boundary import (
    CustomerEnterpriseMismatchError,
    InstallationBoundaryError,
    require_matching_customer_enterprise,
    resolve_installation_customer_enterprise_id,
)


def test_dev_local_defaults_installation_customer_enterprise_id() -> None:
    assert (
        resolve_installation_customer_enterprise_id(
            {"schema_version": "1.0.0", "runtime_profile": "dev_local"}
        )
        == "dev-local-enterprise"
    )


def test_onprem_requires_explicit_installation_customer_enterprise_id() -> None:
    with pytest.raises(InstallationBoundaryError):
        resolve_installation_customer_enterprise_id(
            {"schema_version": "1.0.0", "runtime_profile": "onprem_production"}
        )


def test_customer_enterprise_mismatch_is_rejected() -> None:
    with pytest.raises(CustomerEnterpriseMismatchError) as exc_info:
        require_matching_customer_enterprise(
            configured_enterprise_id="customer-a",
            observed_enterprise_id="customer-b",
        )
    assert exc_info.value.error_code == "customer_enterprise_mismatch"
