"""Bounded package chat with injection defenses (Component G)."""

from __future__ import annotations

import re
from typing import Any

_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior)\s+instructions\b"),
    re.compile(r"(?i)\bsystem\s+prompt\b"),
    re.compile(r"(?i)\bgrant\s+(me\s+)?ato\b"),
    re.compile(r"(?i)\bapprove\s+(this\s+)?package\b"),
    re.compile(r"(?i)\brun\s+(shell|sql|command)\b"),
)
_AUTHORIZATION_REFUSAL = "authorization_decision"
_OUT_OF_PACKAGE_REFUSAL = "out_of_package"
_UNSAFE_REFUSAL = "unsafe_instruction"


def chat_with_package(
    *,
    question: str,
    sealed_document: dict[str, Any] | None,
    search_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Answer bounded questions with citations and deterministic refusal rules."""
    normalized = question.strip()
    if not normalized:
        return {
            "answer": "",
            "citations": [],
            "refused": True,
            "refusal_code": _OUT_OF_PACKAGE_REFUSAL,
        }
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(normalized):
            if _looks_like_authorization_request(normalized):
                return {
                    "answer": "This assistant cannot perform authorization, approval, or unsafe actions.",
                    "citations": [],
                    "refused": True,
                    "refusal_code": _AUTHORIZATION_REFUSAL,
                }
            return {
                "answer": "This assistant cannot perform authorization, approval, or unsafe actions.",
                "citations": [],
                "refused": True,
                "refusal_code": _UNSAFE_REFUSAL,
            }

    citations = [
        {
            "artifact_id": hit.get("reference_id", "unknown"),
            "sha256": hit.get("sha256", "0" * 64),
            "locator": {"kind": "search_hit", "reference_id": hit.get("reference_id")},
            "excerpt": hit.get("excerpt", "")[:500],
        }
        for hit in search_hits[:3]
    ]
    if not citations:
        return {
            "answer": "No authorized package content matched the question.",
            "citations": [],
            "refused": True,
            "refusal_code": _OUT_OF_PACKAGE_REFUSAL,
        }

    system_name = ""
    if isinstance(sealed_document, dict):
        system = sealed_document.get("system")
        if isinstance(system, dict):
            system_name = str(system.get("display_name") or "")
    answer = (
        f"Based on the authorized package{f' for {system_name}' if system_name else ''}, "
        f"the closest matching content is: {citations[0]['excerpt']}"
    )
    return {
        "answer": answer[:12000],
        "citations": citations,
        "refused": False,
        "refusal_code": None,
    }


def _looks_like_authorization_request(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "grant ato",
            "approve this package",
            "authorization decision",
            "risk acceptance",
            "official compliance",
        )
    )
