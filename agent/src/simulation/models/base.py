"""Shared pydantic base for all domain models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ChatMessage(BaseModel):
    """One chat turn passed to an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    role: str  # "system" | "user" | "assistant"
    content: str
