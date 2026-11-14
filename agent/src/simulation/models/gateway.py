"""Concurrency-limited gateway in front of any LLM provider (spec section 18).

One provider instance is shared by all agents; `max_concurrency`
caps in-flight calls (e.g. 2 against a local Ollama server).
"""

from __future__ import annotations

import asyncio

from simulation.models.base import ChatMessage
from simulation.models.llm import LLMProvider, LLMResponse, ModelConfig


class LLMGateway:
    def __init__(self, provider: LLMProvider, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._provider = provider
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        self.in_flight_peak = 0
        self._in_flight = 0
        # Lightweight run stats for metrics.json (spec section 34).
        self.calls = 0
        self.total_latency_ms = 0.0
        self.total_tokens = 0

    @property
    def provider(self) -> LLMProvider:
        """The wrapped provider, so callers can read its own counters
        (e.g. how many transport retries it spent)."""
        return self._provider

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type,
        config: ModelConfig,
    ) -> LLMResponse:
        async with self._semaphore:
            self._in_flight += 1
            self.in_flight_peak = max(self.in_flight_peak, self._in_flight)
            try:
                response = await self._provider.generate(
                    messages, response_schema, config
                )
            finally:
                self._in_flight -= 1
        self.calls += 1
        if response.latency_ms:
            self.total_latency_ms += response.latency_ms
        if response.tokens_used:
            self.total_tokens += response.tokens_used
        return response
