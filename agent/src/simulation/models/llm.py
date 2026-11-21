"""LLM gateway types (spec section 17).

`LLMProvider` is the provider abstraction: game logic never sees
provider-specific code. Ollama lands in Phase 10; cloud providers later.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Protocol

from pydantic import Field, field_validator

from simulation.models.base import ChatMessage, StrictModel

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UNSAFE_HEADERS = frozenset(
    {"authorization", "host", "content-length", "connection", "transfer-encoding"}
)


def check_api_key_env(value: str) -> str:
    if value and not ENV_NAME.fullmatch(value):
        raise ValueError(
            "api_key_env must be an environment variable name, not a value"
        )
    return value


def check_extra_headers(value: dict[str, str]) -> dict[str, str]:
    bad = sorted(
        name for name in value if name.strip().lower() in UNSAFE_HEADERS
    )
    if bad:
        raise ValueError("llm.extra_headers may not override: " + ", ".join(bad))
    return value


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
    # Extra attempts for transient transport failures (spec section 18).
    retries: int = Field(default=3, ge=0)
    reasoning_effort: str = "medium"
    # Local Ollama server (or any OpenAI-compatible proxy base later).
    base_url: str = "http://localhost:11434"
    # Name of the environment variable holding a cloud provider's API
    # key. The value never enters a tracked config, only the name.
    api_key_env: str = "OPENAI_COMPAT_API_KEY"
    # Extra request headers for routes that route on them, such as the
    # session id an OpenAI-compatible proxy uses for prompt caching.
    extra_headers: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("api_key_env")
    @classmethod
    def _check_api_key_env(cls, value: str) -> str:
        return check_api_key_env(value)

    @field_validator("extra_headers")
    @classmethod
    def _check_extra_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return check_extra_headers(value)


class LLMResponse(StrictModel):
    content: str
    model: str = ""
    latency_ms: Optional[float] = None
    tokens_used: Optional[int] = None  # output tokens
    input_tokens: Optional[int] = None  # prompt tokens
    # Transport retries this one call spent before it answered. Carried on
    # the response rather than read off the provider's shared counter so a
    # concurrent call cannot have its retries attributed to this one.
    retries: int = 0


class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        ...
