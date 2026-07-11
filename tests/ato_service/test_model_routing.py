"""Tests for deterministic model-routing policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ato_service.model_routing import (
    DataOrigin,
    EndpointProfile,
    ModelRoutingDecision,
    Sensitivity,
    evaluate_model_routing,
)


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = ROOT / "docs" / "contracts" / "domain.schema.json"


def _decision(
    *,
    data_origin: DataOrigin,
    sensitivity: Sensitivity,
    endpoint_profile: EndpointProfile,
    endpoint_policy_approved: bool = False,
    cui_boundary_approved: bool = False,
) -> ModelRoutingDecision:
    return evaluate_model_routing(
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=endpoint_policy_approved,
        cui_boundary_approved=cui_boundary_approved,
    )


@pytest.mark.parametrize("endpoint_profile", list(EndpointProfile))
def test_synthetic_non_classified_allowed_without_approval(
    endpoint_profile: EndpointProfile,
) -> None:
    for sensitivity in (Sensitivity.PUBLIC, Sensitivity.INTERNAL_UNCLASSIFIED):
        decision = _decision(
            data_origin=DataOrigin.SYNTHETIC,
            sensitivity=sensitivity,
            endpoint_profile=endpoint_profile,
        )
        assert decision.allowed is True
        assert decision.error_code is None


@pytest.mark.parametrize("endpoint_profile", list(EndpointProfile))
@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.CUSTOMER_SENSITIVE, Sensitivity.CUI],
)
def test_synthetic_sensitive_labels_require_internal_policy(
    endpoint_profile: EndpointProfile,
    sensitivity: Sensitivity,
) -> None:
    denied = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
    )
    if endpoint_profile is EndpointProfile.EXTERNAL_OPENAI:
        assert denied.allowed is False
        assert denied.error_code == "model_routing_denied"
        return

    assert denied.allowed is False
    assert denied.error_code == "model_policy_not_approved"

    approved = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=sensitivity is Sensitivity.CUI,
    )
    assert approved.allowed is True


@pytest.mark.parametrize("endpoint_profile", list(EndpointProfile))
@pytest.mark.parametrize("data_origin", list(DataOrigin))
def test_classified_is_always_denied(
    endpoint_profile: EndpointProfile,
    data_origin: DataOrigin,
) -> None:
    decision = _decision(
        data_origin=data_origin,
        sensitivity=Sensitivity.CLASSIFIED,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert decision.allowed is False
    assert decision.error_code == "classified_data_unsupported"


@pytest.mark.parametrize("endpoint_profile", list(EndpointProfile))
@pytest.mark.parametrize("data_origin", list(DataOrigin))
def test_unknown_is_always_denied(
    endpoint_profile: EndpointProfile,
    data_origin: DataOrigin,
) -> None:
    decision = _decision(
        data_origin=data_origin,
        sensitivity=Sensitivity.UNKNOWN,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert decision.allowed is False
    assert decision.error_code == "model_routing_denied"


@pytest.mark.parametrize(
    "sensitivity",
    [
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL_UNCLASSIFIED,
        Sensitivity.CUSTOMER_SENSITIVE,
        Sensitivity.CUI,
    ],
)
def test_external_customer_production_is_always_denied(
    sensitivity: Sensitivity,
) -> None:
    decision = _decision(
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert decision.allowed is False
    assert decision.error_code == "model_routing_denied"


@pytest.mark.parametrize("data_origin", list(DataOrigin))
@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.CUSTOMER_SENSITIVE, Sensitivity.CUI],
)
def test_external_customer_sensitive_and_cui_are_denied(
    data_origin: DataOrigin,
    sensitivity: Sensitivity,
) -> None:
    decision = _decision(
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert decision.allowed is False
    assert decision.error_code == "model_routing_denied"


@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.PUBLIC, Sensitivity.INTERNAL_UNCLASSIFIED],
)
def test_external_redacted_non_cui_requires_endpoint_policy(
    sensitivity: Sensitivity,
) -> None:
    denied = _decision(
        data_origin=DataOrigin.REDACTED_NONPRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
    )
    assert denied.allowed is False
    assert denied.error_code == "model_policy_not_approved"

    allowed = _decision(
        data_origin=DataOrigin.REDACTED_NONPRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
    )
    assert allowed.allowed is True


@pytest.mark.parametrize(
    "sensitivity",
    [Sensitivity.CUSTOMER_SENSITIVE, Sensitivity.CUI],
)
def test_external_redacted_cui_or_customer_sensitive_is_denied(
    sensitivity: Sensitivity,
) -> None:
    decision = _decision(
        data_origin=DataOrigin.REDACTED_NONPRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=EndpointProfile.EXTERNAL_OPENAI,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert decision.allowed is False
    assert decision.error_code == "model_routing_denied"


@pytest.mark.parametrize("endpoint_profile", list(EndpointProfile))
def test_internal_customer_production_requires_policy(endpoint_profile: EndpointProfile) -> None:
    if endpoint_profile is EndpointProfile.EXTERNAL_OPENAI:
        pytest.skip("customer production external path is always denied")

    denied = _decision(
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        sensitivity=Sensitivity.PUBLIC,
        endpoint_profile=endpoint_profile,
    )
    assert denied.allowed is False
    assert denied.error_code == "model_policy_not_approved"

    allowed = _decision(
        data_origin=DataOrigin.CUSTOMER_PRODUCTION,
        sensitivity=Sensitivity.PUBLIC,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
    )
    assert allowed.allowed is True


@pytest.mark.parametrize(
    "endpoint_profile",
    [EndpointProfile.INTERNAL_OPENAI_COMPATIBLE, EndpointProfile.MOCK],
)
def test_internal_customer_sensitive_requires_policy(
    endpoint_profile: EndpointProfile,
) -> None:
    denied = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.CUSTOMER_SENSITIVE,
        endpoint_profile=endpoint_profile,
    )
    assert denied.allowed is False
    assert denied.error_code == "model_policy_not_approved"

    allowed = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.CUSTOMER_SENSITIVE,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
    )
    assert allowed.allowed is True


@pytest.mark.parametrize(
    "endpoint_profile",
    [EndpointProfile.INTERNAL_OPENAI_COMPATIBLE, EndpointProfile.MOCK],
)
def test_internal_cui_requires_policy_and_boundary(
    endpoint_profile: EndpointProfile,
) -> None:
    policy_only = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.CUI,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=False,
    )
    assert policy_only.allowed is False
    assert policy_only.error_code == "model_policy_not_approved"

    allowed = _decision(
        data_origin=DataOrigin.SYNTHETIC,
        sensitivity=Sensitivity.CUI,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=True,
    )
    assert allowed.allowed is True


@pytest.mark.parametrize(
    "endpoint_profile",
    [EndpointProfile.INTERNAL_OPENAI_COMPATIBLE, EndpointProfile.MOCK],
)
@pytest.mark.parametrize(
    "sensitivity",
    [
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL_UNCLASSIFIED,
        Sensitivity.CUSTOMER_SENSITIVE,
        Sensitivity.CUI,
    ],
)
def test_internal_redacted_requires_policy(
    endpoint_profile: EndpointProfile,
    sensitivity: Sensitivity,
) -> None:
    denied = _decision(
        data_origin=DataOrigin.REDACTED_NONPRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
    )
    assert denied.allowed is False
    assert denied.error_code == "model_policy_not_approved"

    allowed = _decision(
        data_origin=DataOrigin.REDACTED_NONPRODUCTION,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=True,
        cui_boundary_approved=sensitivity is Sensitivity.CUI,
    )
    assert allowed.allowed is True


def test_model_routing_decision_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="allowed decisions must not carry an error_code"):
        ModelRoutingDecision(allowed=True, error_code="model_routing_denied")
    with pytest.raises(ValueError, match="denied decisions must carry an error_code"):
        ModelRoutingDecision(allowed=False)


def test_domain_schema_enum_values_match_python_strenum() -> None:
    schema = json.loads(DOMAIN_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]

    assert [member.value for member in DataOrigin] == defs["DataOrigin"]["enum"]
    assert [member.value for member in Sensitivity] == defs["Sensitivity"]["enum"]
    assert [member.value for member in EndpointProfile] == defs["ModelStep"]["properties"][
        "endpoint_profile"
    ]["enum"]


@pytest.mark.parametrize(
    ("data_origin", "sensitivity", "endpoint_profile", "policy", "cui", "allowed", "error_code"),
    [
        (
            DataOrigin.SYNTHETIC,
            Sensitivity.PUBLIC,
            EndpointProfile.EXTERNAL_OPENAI,
            False,
            False,
            True,
            None,
        ),
        (
            DataOrigin.CUSTOMER_PRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.EXTERNAL_OPENAI,
            True,
            True,
            False,
            "model_routing_denied",
        ),
        (
            DataOrigin.CUSTOMER_PRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
            True,
            False,
            True,
            None,
        ),
        (
            DataOrigin.REDACTED_NONPRODUCTION,
            Sensitivity.PUBLIC,
            EndpointProfile.EXTERNAL_OPENAI,
            True,
            False,
            True,
            None,
        ),
        (
            DataOrigin.REDACTED_NONPRODUCTION,
            Sensitivity.CUI,
            EndpointProfile.INTERNAL_OPENAI_COMPATIBLE,
            True,
            True,
            True,
            None,
        ),
        (
            DataOrigin.SYNTHETIC,
            Sensitivity.CLASSIFIED,
            EndpointProfile.MOCK,
            True,
            True,
            False,
            "classified_data_unsupported",
        ),
        (
            DataOrigin.SYNTHETIC,
            Sensitivity.UNKNOWN,
            EndpointProfile.MOCK,
            True,
            True,
            False,
            "model_routing_denied",
        ),
    ],
)
def test_routing_table_representative_rows(
    data_origin: DataOrigin,
    sensitivity: Sensitivity,
    endpoint_profile: EndpointProfile,
    policy: bool,
    cui: bool,
    allowed: bool,
    error_code: str | None,
) -> None:
    decision = evaluate_model_routing(
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=policy,
        cui_boundary_approved=cui,
    )
    assert decision.allowed is allowed
    assert decision.error_code == error_code
