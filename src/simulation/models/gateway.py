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
                return await self._provider.generate(
                    messages, response_schema, config
                )
            finally:
                self._in_flight -= 1
