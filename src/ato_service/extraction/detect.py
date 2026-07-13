"""Magic-byte format and MIME detection."""

from __future__ import annotations

import json
import re

_DETECTED_FORMATS = frozenset(
    {
        "json",
        "text",
        "markdown",
        "pdf",
        "docx",
        "xlsx",
        "zip",
        "png",
        "jpeg",
        "webp",
        "svg",
        "xml",
        "oscal_json",
        "oscal_xml",
        "nessus_xml",
        "sarif_json",
        "stig_json",
        "stig_xml",
    }
)

_FORMAT_TO_MIME = {
    "json": "application/json",
    "text": "text/plain",
    "markdown": "text/markdown",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "xml": "application/xml",
    "oscal_json": "application/json",
    "oscal_xml": "application/xml",
    "nessus_xml": "application/xml",
    "sarif_json": "application/json",
    "stig_json": "application/json",
    "stig_xml": "application/xml",
}


def detect_format(
    content: bytes,
    *,
    declared_media_type: str | None,
    declared_format: str | None,
    filename: str | None,
) -> str:
    """Detect supported format from magic bytes and declared hints."""
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"PK\x03\x04"):
        return _detect_zip_container(
            declared_format=declared_format,
            filename=filename,
        )
    if _looks_like_xml(content):
        return _detect_xml_family(content, declared_format=declared_format)
    if _looks_like_json(content):
        return _detect_json_family(
            content,
            declared_format=declared_format,
            declared_media_type=declared_media_type,
        )

    if filename:
        lower = filename.lower()
        if lower.endswith(".md") or lower.endswith(".markdown"):
            return "markdown"
        if lower.endswith(".txt"):
            return "text"

    if declared_media_type == "text/plain":
        return "text"
    if declared_media_type == "text/markdown":
        return "markdown"
    if declared_media_type == "application/json":
        return "json"

    if _is_probably_utf8_text(content):
        return "text"
    raise ValueError("unsupported content format")


def media_type_for_format(detected_format: str) -> str:
    """Return the canonical MIME type for one detected format."""
    return _FORMAT_TO_MIME.get(detected_format, "application/octet-stream")


def _detect_zip_container(
    *,
    declared_format: str | None,
    filename: str | None,
) -> str:
    """Classify ZIP containers without parsing their untrusted directory."""
    if declared_format == "docx":
        return "docx"
    if declared_format == "xlsx":
        return "xlsx"
    if filename:
        lower = filename.casefold()
        if lower.endswith(".docx"):
            return "docx"
        if lower.endswith(".xlsx"):
            return "xlsx"
    return "zip"


def _looks_like_xml(content: bytes) -> bool:
    stripped = content.lstrip()
    return stripped.startswith(b"<?xml") or stripped.startswith(b"<")


def _looks_like_json(content: bytes) -> bool:
    stripped = content.lstrip()
    return stripped.startswith(b"{") or stripped.startswith(b"[")


def _is_probably_utf8_text(content: bytes) -> bool:
    if not content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _detect_json_family(
    content: bytes,
    *,
    declared_format: str | None,
    declared_media_type: str | None,
) -> str:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "json"
    if isinstance(payload, dict):
        if payload.get("$schema", "").endswith("sarif-2.1.0.json"):
            return "sarif_json"
        if "oscal-version" in payload or "system-security-plan" in payload:
            return "oscal_json"
        if "stig" in payload or "benchmark" in payload:
            return "stig_json"
    if declared_media_type == "application/sarif+json" or declared_format == "sarif_json":
        return "sarif_json"
    if declared_format in {"oscal_json", "stig_json"}:
        return declared_format
    return "json"


def _detect_xml_family(content: bytes, *, declared_format: str | None) -> str:
    text = content[:65_536].decode("utf-8", errors="ignore").lower()
    if re.search(r"<(?:[a-z0-9_.-]+:)?svg(?:\s|>)", text):
        return "svg"
    if re.search(r"<(?:[a-z0-9_.-]+:)?nessusclientdata(?:_v2)?(?:\s|>)", text):
        return "nessus_xml"
    if re.search(r"<(?:[a-z0-9_.-]+:)?benchmark(?:\s|>)", text):
        return "stig_xml"
    if (
        "http://csrc.nist.gov/ns/oscal/" in text
        or re.search(
            r"<(?:[a-z0-9_.-]+:)?(?:system-security-plan|catalog|profile|component-definition)"
            r"(?:\s|>)",
            text,
        )
    ):
        return "oscal_xml"
    if declared_format in {"nessus_xml", "oscal_xml", "stig_xml", "svg"}:
        return declared_format
    return "xml"
