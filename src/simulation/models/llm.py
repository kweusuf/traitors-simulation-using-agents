"""LLM gateway types (spec section 17).

`LLMProvider` is the provider abstraction: game logic never sees
provider-specific code. Ollama lands in Phase 10; cloud providers later.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from pydantic import Field

from simulation.models.base import ChatMessage, StrictModel


class ModelConfig(StrictModel):
    """Normalized model parameters (spec section 19).

    Providers that do not support a parameter ignore it; extra
    provider-specific knobs live in `options`.
    """

    provider: str = "ollama"
    name: str = "gpt-oss:20b"
    temperature: float = 0.7
    max_tokens: int = 512
    timeout_seconds: int = 120
    reasoning_effort: str = "medium"
    options: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(StrictModel):
    content: str
    model: str = ""
    latency_ms: Optional[float] = None
    tokens_used: Optional[int] = None


class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        ...
