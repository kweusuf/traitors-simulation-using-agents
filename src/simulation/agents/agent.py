"""Agent: a stateful simulated player (spec section 11).

Holds identity, persona, role, goals, memory, beliefs, and
relationships. The agent never executes actions itself; it returns a
structured action to the engine (the decision loop lives alongside the
LLM gateway, see Phase 7).
"""

from __future__ import annotations

from typing import Any, Optional

from simulation.agents.beliefs import Beliefs
from simulation.agents.goals import Goals, inject_role_goals
from simulation.agents.persona import Persona
from simulation.agents.relationships import Relationships
from simulation.engine.state import Role
from simulation.memory.short_term import ShortTermMemory


class Agent:
    def __init__(
        self,
        agent_id: str,
        name: str,
        persona: Persona,
        goals: Optional[Goals] = None,
        memory: Optional[ShortTermMemory] = None,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.persona = persona
        self.base_goals = goals or Goals()
        self.role: Optional[Role] = None
        self.ambition: Optional[str] = None
        self.goals = self.base_goals
        self.memory = memory or ShortTermMemory(game_id="", agent_id=agent_id)
        self.beliefs = Beliefs()
        self.relationships = Relationships()

    def assign_role(
        self, role: Role, ambition: Optional[str] = None
    ) -> None:
        """Roles come from the engine; role goals are injected here (spec section 13).

        `ambition` is the seeded solo/team disposition; it only affects
        traitors and never enters an observation.
        """
        self.role = role
        self.ambition = ambition
        self.goals = inject_role_goals(self.base_goals, role, ambition)

    async def remember(
        self,
        content: str,
        kind: str = "event",
        round_number: int = 0,
        sequence: int = 0,
    ) -> None:
        await self.memory.remember(
            {
                "content": content,
                "kind": kind,
                "round": round_number,
                "sequence": sequence,
            }
        )

    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self.memory.retrieve(query, limit)

    @property
    def role_value(self) -> str:
        return self.role.value if self.role else "unassigned"
