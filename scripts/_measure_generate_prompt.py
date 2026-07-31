#!/usr/bin/env python3
from pathlib import Path

from ato_service.ssp_workspace.generation import InitialGenerationRequest, _initial_user_prompt
from ato_service.ssp_workspace.profile_bundles import load_profile_bundle, resolve_profile

ROOT = Path(__file__).resolve().parents[1]
bundle = load_profile_bundle(ROOT / "reference/ssp_profiles/agency-fisma-nist-sp800-53-rev5-1.2.0")
profile = resolve_profile(bundle, "moderate")
req = InitialGenerationRequest(
    system_name="test",
    profile=profile,
    source_ids=("artifact-1",),
    facts=(),
    categorization_confirmed=False,
)
prompt = _initial_user_prompt(req)
print("prompt_chars", len(prompt))
print("controls_in_profile", len(profile.controls))
print("approx_tokens_chars_div4", len(prompt) // 4)
