"""Deterministic fake LLM providers (spec section 30).

`FakeLLMProvider` returns scripted structured actions keyed by agent id
so the whole engine can be tested without any model calls.
`PromptScriptProvider` answers from the prompt itself, which lets the
CLI play a full game deterministically without a model (spec section 47).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from simulation.actions.actions import Action, ActionType
from simulation.models.base import ChatMessage
from simulation.models.llm import LLMResponse, ModelConfig

_AGENT_RE = re.compile(r"You are ([a-zA-Z0-9_]+),")
_ACTION_RE = re.compile(r"Required action type: (\w+)")
_TARGETS_RE = re.compile(r"^Legal targets: (.+)$", re.MULTILINE)
_ALIVE_RE = re.compile(r"^Alive players: (.+)$", re.MULTILINE)
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


class PromptScriptProvider(FakeLLMProvider):
    """A fake provider that answers from the prompt itself.

    Reads the required action type and legal target list out of the
    user message and replies with a deterministic structured action, so
    any game length can be played back without scripting every turn.
    """

    def __init__(self) -> None:
        super().__init__(script={})
        self.counts: dict[str, int] = {}

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type = Action,
        config: Optional[ModelConfig] = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        agent_id = self._agent_id(messages)
        user = next(m for m in reversed(messages) if m.role == "user").content

        action_match = _ACTION_RE.search(user)
        if action_match is None:
            raise RuntimeError("prompt must state the required action type")
        action_type = ActionType(action_match.group(1))

        targets_match = _TARGETS_RE.search(user)
        raw_targets = targets_match.group(1).strip() if targets_match else ""
        if raw_targets.startswith("none") or not raw_targets:
            targets: list[str] = []
        else:
            targets = [t.strip() for t in raw_targets.split(",")]

        n = self.counts.get(agent_id, 0)
        self.counts[agent_id] = n + 1

        if action_type is ActionType.PUBLIC_MESSAGE:
            payload = {
                "action": "public_message",
                "content": f"[{agent_id}#{n}] I am watching everyone closely.",
                "confidence": 0.6,
            }
        elif action_type is ActionType.TRAITOR_MESSAGE:
            payload = {
                "action": "traitor_message",
                "content": (
                    f"[{agent_id}#{n}] let us agree the loudest voice dies "
                    "tonight and the blame lands elsewhere."
                ),
                "confidence": 0.6,
            }
        elif action_type is ActionType.PRIVATE_MESSAGE:
            payload = {
                "action": "private_message",
                "target": targets[0],
                "content": f"[{agent_id}#{n}] privately sharing a read.",
                "confidence": 0.5,
            }
        elif action_type in (ActionType.SEER_CHECK, ActionType.NOMINATE):
            # One-shot Wave B actions: take the first name offered.
            payload = {
                "action": action_type.value,
                "target": targets[0],
                "confidence": 0.9,
            }
        elif action_type is ActionType.END_VOTE:
            # Deterministic endgame rule: keep forcing banishments while
            # more than three players remain, then end. The prompt's
            # "Alive players:" line is the only count available here, and
            # it lets fake games finish without scripting every turn.
            alive_match = _ALIVE_RE.search(user)
            alive = (
                [p.strip() for p in alive_match.group(1).split(",")]
                if alive_match
                else []
            )
            payload = {
                "action": "end_vote",
                "content": "end" if len(alive) <= 3 else "banish",
                "confidence": 0.7,
            }
        else:
            # VOTE and TRAITOR_KILL: pile onto the first legal target.
            payload = {
                "action": action_type.value,
                "target": targets[0],
                "confidence": 0.8,
                "reason_summary": "first legal target",
            }
        return LLMResponse(content=json.dumps(payload), model="fake")
