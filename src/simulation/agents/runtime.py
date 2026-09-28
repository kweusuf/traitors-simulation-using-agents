"""Agent runtime: the observe -> remember -> prompt -> decide loop (spec section 11).

The runtime returns a structured action to the engine; it never
executes actions itself. Malformed or illegal output triggers one
correction retry before giving up (spec section 21).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from simulation.actions.actions import Action, ActionType
from simulation.actions.validator import ActionParseError, check_action_constraints, parse_action
from simulation.agents.agent import Agent
from simulation.agents.prompts import PromptBuilder, action_json_hint
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.models.base import ChatMessage
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig

if TYPE_CHECKING:  # avoids an agents -> experiments import at runtime
    from simulation.experiments.telemetry import TelemetryRecorder


class AgentRuntime:
    """One decide loop per agent turn; `max_retries` correction attempts
    are additional, so the default is three attempts per action (spec
    section 21). Small local models miss required fields often enough
    that a single retry still lost whole runs."""

    def __init__(
        self,
        agents: dict[str, Agent],
        gateway: LLMGateway,
        model_config: ModelConfig,
        prompt_builder: Optional[PromptBuilder] = None,
        max_retries: int = 2,
        telemetry: Optional["TelemetryRecorder"] = None,
    ) -> None:
        self.agents = agents
        self.gateway = gateway
        self.model_config = model_config
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.telemetry = telemetry
        self.max_retries = max_retries  # extra attempts after the first

    async def decide(
        self,
        agent_id: str,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
        extra_instruction: Optional[str] = None,
    ) -> Action:
        """Produce one validated structured action for this observation."""
        agent = self.agents[agent_id]
        if agent.role is None:
            raise RuntimeError(f"agent '{agent_id}' has no assigned role")

        memory_items = await agent.recall("", limit=10)
        messages = self.prompt_builder.build(
            agent_id=agent_id,
            role=agent.role,
            persona=agent.persona,
            goals=agent.goals,
            view=view,
            action_type=action_type,
            legal_targets=legal_targets,
            memory_items=memory_items,
            extra_instruction=extra_instruction,
        )

        attempts = 1 + self.max_retries
        last_reason = ""
        for attempt in range(attempts):
            retries_before = self._transport_retries()
            try:
                response = await self.gateway.generate(
                    messages, Action, self.model_config
                )
            except Exception as exc:
                # Transport failures still belong in the run's telemetry.
                self._record_call(
                    view,
                    action_type,
                    attempt + 1,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    retries=self._transport_retries() - retries_before,
                    messages=messages,
                )
                raise
            retries_spent = self._transport_retries() - retries_before
            try:
                action = parse_action(response.content, agent_id)
                reason = check_action_constraints(action, {action_type}, legal_targets)
                if reason is not None:
                    raise ActionParseError(reason)
            except ActionParseError as exc:
                last_reason = str(exc)
                self._record_call(
                    view,
                    action_type,
                    attempt + 1,
                    ok=False,
                    response=response,
                    error=last_reason,
                    retries=retries_spent,
                    messages=messages,
                )
                if attempt == attempts - 1:
                    raise ActionParseError(
                        f"'{agent_id}' failed to produce a valid action after "
                        f"{attempts} attempts: {last_reason}"
                    ) from exc
                messages = messages + [
                    ChatMessage(role="assistant", content=response.content),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Invalid action: {last_reason}. Try again. "
                            f"{action_json_hint(action_type)}"
                        ),
                    ),
                ]
                continue
            self._record_call(
                view,
                action_type,
                attempt + 1,
                ok=True,
                response=response,
                retries=retries_spent,
                messages=messages,
            )
            return action

        raise AssertionError("unreachable")

    def _transport_retries(self) -> int:
        """Provider-level transport retries spent so far, if it reports them."""
        return int(getattr(getattr(self.gateway, "provider", None), "retries", 0))

    def _record_call(
        self,
        view: AgentView,
        action_type: ActionType,
        attempt: int,
        *,
        ok: bool,
        response=None,
        retries: int = 0,
        error: Optional[str] = None,
        messages: Optional[list[ChatMessage]] = None,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.record(
            agent_id=view.agent_id,
            action_type=action_type.value,
            phase=view.phase.value,
            round_number=view.round_number,
            attempt=attempt,
            ok=ok,
            response=response,
            retries=retries,
            error=error,
            prompt_chars=sum(len(m.content) for m in (messages or [])),
        )


def make_action_callback(
    runtime: AgentRuntime,
    engine: GameEngine,
    projector: InformationProjector,
):
    """Wire the runtime into a PhaseContext's action callback (spec section 7)."""

    async def callback(
        agent_id: str,
        action_type: ActionType,
        legal_targets: list[str],
        extra_instruction: Optional[str] = None,
    ) -> Action:
        view = projector.project(engine.state, agent_id)
        return await runtime.decide(
            agent_id, view, action_type, legal_targets, extra_instruction
        )

    return callback
