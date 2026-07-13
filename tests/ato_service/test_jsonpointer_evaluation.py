"""Semantic evaluation of jsonpointer consolidation (THIRD_PARTY_HARDENING_PLAN B1).

Verdict: NO-GO — retain custom RFC 6901 helpers. The jsonpointer library does not
implement intermediate dict auto-create required by normalize_proposal and
draft_builder set semantics.
"""

from __future__ import annotations

import jsonpointer
import pytest

from ato_service.normalize_proposal.json_utils import (
    is_valid_json_pointer,
    set_json_pointer,
    value_at_json_pointer,
)


def test_empty_pointer_read_returns_document() -> None:
    document = {"package": {"title": "demo"}}
    assert value_at_json_pointer(document, "") == document
    assert jsonpointer.resolve_pointer(document, "") == document


def test_escaping_round_trip() -> None:
    document: dict[str, object] = {"a/b": {"c~d": 42}}
    pointer = "/a~1b/c~0d"
    assert is_valid_json_pointer(pointer)
    assert value_at_json_pointer(document, pointer) == 42
    assert jsonpointer.resolve_pointer(document, pointer) == 42


def test_list_index_read_and_set() -> None:
    document: dict[str, object] = {"items": ["alpha", "beta"]}
    assert value_at_json_pointer(document, "/items/0") == "alpha"
    set_json_pointer(document, "/items/1", "gamma")
    assert document["items"] == ["alpha", "gamma"]
    assert jsonpointer.resolve_pointer(document, "/items/1") == "gamma"


def test_set_rejects_empty_pointer() -> None:
    document: dict[str, object] = {"package": {}}
    with pytest.raises(ValueError, match="empty JSON pointer"):
        set_json_pointer(document, "", "blocked")
    with pytest.raises(jsonpointer.JsonPointerException):
        jsonpointer.set_pointer(document, "", "blocked")


def test_custom_set_auto_creates_intermediate_dicts() -> None:
    document: dict[str, object] = {"package": {}}
    set_json_pointer(document, "/package/nested/value", "created")
    assert document == {"package": {"nested": {"value": "created"}}}


def test_jsonpointer_library_does_not_auto_create_intermediate_dicts() -> None:
    document: dict[str, object] = {"package": {}}
    with pytest.raises(jsonpointer.JsonPointerException):
        jsonpointer.set_pointer(document, "/package/nested/value", "created")


def test_domain_pointer_length_guard() -> None:
    long_pointer = "/" + "a" * 1999
    assert len(long_pointer) == 2000
    assert is_valid_json_pointer(long_pointer) is True
    assert is_valid_json_pointer(long_pointer + "a") is False


def test_verdict_record_no_go_consolidation() -> None:
    """Document the go/no-go gate required by THIRD_PARTY_HARDENING_PLAN section 5."""
    assert (
        jsonpointer.__version__  # noqa: SLF001 — evaluation fixture only
        is not None
    )
    # Custom auto-create semantics are incompatible with jsonpointer defaults.
    document: dict[str, object] = {}
    set_json_pointer(document, "/controls/AC-1/status", "implemented")
    assert document["controls"]["AC-1"]["status"] == "implemented"
    with pytest.raises(jsonpointer.JsonPointerException):
        jsonpointer.set_pointer(document, "/controls/AC-2/status", "planned")
