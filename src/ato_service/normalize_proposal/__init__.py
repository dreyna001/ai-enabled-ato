"""Bounded normalize_proposal pure core for Component A Diff 4."""

from ato_service.normalize_proposal.client import (
    NormalizeModelCallError,
    NormalizeModelRoutingError,
    invoke_normalize_model,
    normalize_model_request,
)
from ato_service.normalize_proposal.constants import (
    MAX_LLM_CALLS,
    MAX_PROPOSALS,
    PROMPT_VERSION,
    PROHIBITED_TARGET_PREFIXES,
    RESPONSE_SCHEMA_VERSION,
)
from ato_service.normalize_proposal.fact_bundle import ContextLimitExceededError, build_fact_bundle
from ato_service.normalize_proposal.merge import merge_proposals, reject_cross_source_duplicates
from ato_service.normalize_proposal.parse import ResponseValidationError, validate_and_parse_response
from ato_service.normalize_proposal.prompt import (
    build_repair_prompt,
    build_system_prompt,
    build_user_prompt,
    frozen_prompt_sha256,
    prompt_contract_metadata,
)
from ato_service.normalize_proposal.runner import run_normalize_proposal
from ato_service.normalize_proposal.target_catalog import (
    allowed_target_set,
    catalog_for_profile,
    is_prohibited_target,
    is_target_allowed,
    is_target_empty,
    list_empty_targets,
)
from ato_service.normalize_proposal.types import (
    ArtifactFacts,
    FactBundle,
    ModelCallMetadata,
    NormalizeProposalResult,
    ParsedProposal,
    ParsedResponse,
    SegmentFact,
)

__all__ = [
    "ArtifactFacts",
    "ContextLimitExceededError",
    "FactBundle",
    "MAX_LLM_CALLS",
    "MAX_PROPOSALS",
    "ModelCallMetadata",
    "NormalizeModelCallError",
    "NormalizeModelRoutingError",
    "NormalizeProposalResult",
    "PROMPT_VERSION",
    "PROHIBITED_TARGET_PREFIXES",
    "ParsedProposal",
    "ParsedResponse",
    "RESPONSE_SCHEMA_VERSION",
    "ResponseValidationError",
    "SegmentFact",
    "allowed_target_set",
    "build_fact_bundle",
    "build_repair_prompt",
    "build_system_prompt",
    "build_user_prompt",
    "catalog_for_profile",
    "frozen_prompt_sha256",
    "invoke_normalize_model",
    "is_prohibited_target",
    "is_target_allowed",
    "is_target_empty",
    "list_empty_targets",
    "merge_proposals",
    "normalize_model_request",
    "prompt_contract_metadata",
    "reject_cross_source_duplicates",
    "run_normalize_proposal",
    "validate_and_parse_response",
]
