"""Deterministic policy gate for every SSP model invocation."""

from __future__ import annotations

from typing import Any

from ato_service.model_routing import (
    DataOrigin,
    EndpointProfile,
    Sensitivity,
    evaluate_model_routing,
)
from ato_service.process_capabilities import resolve_process_capabilities
from ato_service.runtime_config import RuntimeConfigError


class SspModelPolicyError(RuntimeConfigError):
    """Raised before an SSP model client or credential can be resolved."""

    def __init__(self, error_code: str) -> None:
        if error_code not in {
            "model_policy_not_approved",
            "model_routing_denied",
            "prohibited_model_action",
        }:
            raise ValueError("unsupported SSP model policy error code")
        self.error_code = error_code
        super().__init__(
            {
                "model_policy_not_approved": (
                    "The configured model route is not approved by policy."
                ),
                "model_routing_denied": (
                    "The requested model capability is denied by routing policy."
                ),
                "prohibited_model_action": (
                    "The requested model action is prohibited by product policy."
                ),
            }[error_code]
        )


def require_ssp_model_allowed(config: Any, vision: bool = False) -> None:
    """Require an explicitly enabled and policy-approved SSP model route.

    This gate intentionally accepts only the runtime configuration. Workspace
    revisions do not currently carry the human-set data-origin and sensitivity
    labels used by the general model router. ``dev_local`` is the documented
    synthetic path; production is treated as an unknown customer boundary and
    therefore fails closed instead of receiving an invented classification.
    """
    if not isinstance(vision, bool):
        raise SspModelPolicyError("prohibited_model_action")

    runtime_profile = getattr(config, "runtime_profile", None)
    document = getattr(config, "document", None)
    if not isinstance(runtime_profile, str) or runtime_profile not in {
        "dev_local",
        "onprem_production",
    }:
        raise SspModelPolicyError("model_routing_denied")
    if not isinstance(document, dict):
        raise SspModelPolicyError("model_routing_denied")

    try:
        capabilities = resolve_process_capabilities(document)
    except RuntimeConfigError as exc:
        raise SspModelPolicyError("model_policy_not_approved") from exc

    capability_name = "vision_model_calls" if vision else "text_model_calls"
    if capabilities is None or getattr(capabilities, capability_name) is not True:
        raise SspModelPolicyError("prohibited_model_action")
    if vision and document.get("VISION_MODEL_ENABLED") is not True:
        raise SspModelPolicyError("prohibited_model_action")

    endpoint_field = (
        "VISION_MODEL_ENDPOINT_PROFILE"
        if vision
        else "TEXT_MODEL_ENDPOINT_PROFILE"
    )
    endpoint_value = document.get(endpoint_field)
    try:
        endpoint_profile = EndpointProfile(endpoint_value)
    except (TypeError, ValueError) as exc:
        raise SspModelPolicyError("model_routing_denied") from exc

    # ``evaluate_model_routing`` permits synthetic data before endpoint checks.
    # SSP still requires an explicit operator approval for every configured
    # endpoint, including the development synthetic path.
    if document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is not True:
        raise SspModelPolicyError("model_policy_not_approved")

    if runtime_profile == "dev_local":
        data_origin = DataOrigin.SYNTHETIC
        sensitivity = Sensitivity.INTERNAL_UNCLASSIFIED
    else:
        # SSP workspace revisions do not yet expose an approved production
        # sensitivity label. Never treat that boundary as public or synthetic.
        data_origin = DataOrigin.CUSTOMER_PRODUCTION
        sensitivity = Sensitivity.UNKNOWN

    decision = evaluate_model_routing(
        data_origin=data_origin,
        sensitivity=sensitivity,
        endpoint_profile=endpoint_profile,
        endpoint_policy_approved=(
            document.get("TEXT_MODEL_ENDPOINT_POLICY_APPROVED") is True
        ),
        cui_boundary_approved=(
            document.get("CUI_MODEL_BOUNDARY_APPROVED") is True
        ),
    )
    if not decision.allowed:
        raise SspModelPolicyError(decision.error_code or "model_routing_denied")


__all__ = ["SspModelPolicyError", "require_ssp_model_allowed"]
