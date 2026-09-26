"""Deterministic fake LLM provider (spec section 30).

Returns scripted structured actions keyed by agent id so the whole
engine can be tested without any model calls.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from simulation.actions.actions import Action
from simulation.models.base import ChatMessage
from simulation.models.llm import LLMResponse, ModelConfig

_AGENT_RE = re.compile(r"You are ([a-zA-Z0-9_]+),")
DEFAULT_QUEUE = "__default__"


class FakeLLMProvider:
    """Pops the next scripted response for each agent.

    script: {agent_id: [response, ...]} where a response is an Action,
    a dict, or a raw JSON string. `DEFAULT_QUEUE` is the fallback key.
    """

    def __init__(
        self,
        script: Optional[dict[str, list[Any]]] = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.script: dict[str, list[Any]] = {
            key: list(values) for key, values in (script or {}).items()
        }
        self.delay_seconds = delay_seconds
        self.calls: list[list[ChatMessage]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type = Action,
        config: Optional[ModelConfig] = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            agent_id = self._agent_id(messages)
            queue = self.script.get(agent_id) or self.script.get(DEFAULT_QUEUE)
            if not queue:
                raise RuntimeError(f"FakeLLMProvider: no scripted response for '{agent_id}'")
            payload = queue.pop(0)
            if isinstance(payload, Action):
                content = payload.model_dump_json()
            elif isinstance(payload, str):
                content = payload
            else:
                content = json.dumps(payload)
            return LLMResponse(content=content, model="fake")
        finally:
            self.in_flight -= 1

    @staticmethod
    def _agent_id(messages: list[ChatMessage]) -> str:
        for message in messages:
            if message.role == "system":
                match = _AGENT_RE.search(message.content)
                if match:
                    return match.group(1)
        return DEFAULT_QUEUE

    @property
    def remaining(self) -> dict[str, int]:
        return {key: len(q) for key, q in self.script.items()}
