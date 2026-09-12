"""Closed API and native-model contracts for the persistent SSP chat."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictContract(BaseModel):
    """Base for closed SSP chat boundary contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatMessageRequest(_StrictContract):
    """Validated user input for one optimistic-concurrency chat turn."""

    message: str = Field(min_length=1, max_length=8_000)
    expected_revision_id: uuid.UUID
    request_id: uuid.UUID
    expected_sequence: int = Field(ge=0)
    focus: str | None = Field(default=None, max_length=500)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """Reject whitespace-only input while preserving meaningful text."""

        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("focus")
    @classmethod
    def normalize_focus(cls, value: str | None) -> str | None:
        """Treat an optional whitespace-only focus as absent."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChatSource(_StrictContract):
    """Canonical source descriptor attached to a validated assistant answer."""

    source_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=500)
    revision_id: uuid.UUID | None = None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: str = Field(min_length=1, max_length=64)
    target_id: str | None = Field(default=None, max_length=255)


class ChatMessage(_StrictContract):
    """One persisted user or assistant message."""

    message_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)
    created_at: datetime
    expires_at: datetime
    sources: list[ChatSource] = Field(max_length=20)
    context_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    stale: bool
    sequence: int = Field(ge=1)


class ChatHistory(_StrictContract):
    """Paged private history for one authorized actor and workspace."""

    workspace_id: uuid.UUID
    sequence: int = Field(ge=0)
    messages: list[ChatMessage]
    has_more: bool
    next_before_sequence: int | None = Field(default=None, ge=1)


class ChatModelOutput(_StrictContract):
    """Native closed output accepted from the guarded SSP model adapter."""

    answer: str = Field(min_length=1, max_length=12_000, pattern=r"\S")
    source_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(max_length=20)


__all__ = [
    "ChatHistory",
    "ChatMessage",
    "ChatMessageRequest",
    "ChatModelOutput",
    "ChatSource",
]
