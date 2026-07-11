"""Deterministic model-routing policy for data labels and endpoint profiles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataOrigin(StrEnum):
    SYNTHETIC = "synthetic"
    REDACTED_NONPRODUCTION = "redacted_nonproduction"
    CUSTOMER_PRODUCTION = "customer_production"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL_UNCLASSIFIED = "internal_unclassified"
    CUSTOMER_SENSITIVE = "customer_sensitive"
    CUI = "cui"
    CLASSIFIED = "classified"
    UNKNOWN = "unknown"


class EndpointProfile(StrEnum):
    MOCK = "mock"
    EXTERNAL_OPENAI = "external_openai"
    INTERNAL_OPENAI_COMPATIBLE = "internal_openai_compatible"


_SYNTHETIC_UNRESTRICTED_SENSITIVITIES = frozenset(
    {
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL_UNCLASSIFIED,
    }
)

_REDACTED_EXTERNAL_SENSITIVITIES = frozenset(
    {
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL_UNCLASSIFIED,
    }
)


@dataclass(frozen=True, slots=True)
class ModelRoutingDecision:
    allowed: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.allowed and self.error_code is not None:
            raise ValueError("allowed decisions must not carry an error_code")
        if not self.allowed and self.error_code is None:
            raise ValueError("denied decisions must carry an error_code")


def _deny(error_code: str) -> ModelRoutingDecision:
    return ModelRoutingDecision(allowed=False, error_code=error_code)


def _allow() -> ModelRoutingDecision:
    return ModelRoutingDecision(allowed=True)


def evaluate_model_routing(
    *,
    data_origin: DataOrigin,
    sensitivity: Sensitivity,
    endpoint_profile: EndpointProfile,
    endpoint_policy_approved: bool,
    cui_boundary_approved: bool,
) -> ModelRoutingDecision:
    """Evaluate Section 11 routing defaults for one endpoint profile."""
    if sensitivity is Sensitivity.CLASSIFIED:
        return _deny("classified_data_unsupported")
    if sensitivity is Sensitivity.UNKNOWN:
        return _deny("model_routing_denied")

    if (
        data_origin is DataOrigin.SYNTHETIC
        and sensitivity in _SYNTHETIC_UNRESTRICTED_SENSITIVITIES
    ):
        return _allow()

    if endpoint_profile is EndpointProfile.EXTERNAL_OPENAI:
        if data_origin is DataOrigin.CUSTOMER_PRODUCTION:
            return _deny("model_routing_denied")
        if sensitivity is Sensitivity.CUSTOMER_SENSITIVE:
            return _deny("model_routing_denied")
        if sensitivity is Sensitivity.CUI:
            return _deny("model_routing_denied")
        if data_origin is DataOrigin.REDACTED_NONPRODUCTION:
            if sensitivity not in _REDACTED_EXTERNAL_SENSITIVITIES:
                return _deny("model_routing_denied")
            if not endpoint_policy_approved:
                return _deny("model_policy_not_approved")
            return _allow()
        return _deny("model_routing_denied")

    if not endpoint_policy_approved:
        return _deny("model_policy_not_approved")

    if sensitivity is Sensitivity.CUI and not cui_boundary_approved:
        return _deny("model_policy_not_approved")

    if sensitivity is Sensitivity.CUI:
        return _allow()
    if sensitivity is Sensitivity.CUSTOMER_SENSITIVE:
        return _allow()
    if data_origin is DataOrigin.CUSTOMER_PRODUCTION:
        return _allow()
    if data_origin is DataOrigin.REDACTED_NONPRODUCTION:
        return _allow()

    return _deny("model_routing_denied")
