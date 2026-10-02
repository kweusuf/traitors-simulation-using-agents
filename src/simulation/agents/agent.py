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
from simulation.memory.short_term import DEFAULT_DECAY, DEFAULT_FLOOR, ShortTermMemory


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
        # Set when the engine flips this player from faithful to traitor
        # mid-game. A converted player needs different coaching: they have
        # been an open, accusing faithful player until tonight.
        self.converted_round: Optional[int] = None
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
        previous = self.role
        if role is Role.TRAITOR and previous is Role.FAITHFUL:
            # A live flip, not the initial assignment.
            self.converted_round = 0
        self.role = role
        self.ambition = ambition
        self.goals = inject_role_goals(self.base_goals, role, ambition)

    async def remember(
        self,
        content: str,
        kind: str = "event",
        round_number: int = 0,
        sequence: int = 0,
        subjects: tuple[str, ...] = (),
        salience: float = 1.0,
    ) -> None:
        await self.memory.remember(
            {
                "content": content,
                "kind": kind,
                "round": round_number,
                "sequence": sequence,
                "subjects": subjects,
                "salience": salience,
            }
        )

    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self.memory.retrieve(query, limit)

    def memory_items(
        self,
        now_round: int,
        limit: int = 6,
        decay: float = DEFAULT_DECAY,
        floor: float = DEFAULT_FLOOR,
    ) -> list[dict[str, Any]]:
        """What this agent still remembers, weighted by who it is about.

        The per-observer factor is the point: a player remembers what a
        friend told them and discounts what someone they are at odds with
        said. `Relationships` carries that read, so the same event weighs
        differently in two different heads.
        """

        def factor(subject: str, round_number: int) -> float:
            rel = self.relationships.get(subject)
            # A close relationship keeps a memory alive; a hostile one
            # silences it. An indifferent observer sits at 1.0, which is
            # where the plain decay curve already lands.
            closeness = rel.trust - rel.suspicion
            return max(0.0, min(1.5, 1.0 + 0.5 * closeness))

        return self.memory.recalled(
            now_round=now_round,
            limit=limit,
            decay=decay,
            floor=floor,
            factor=factor,
        )

    @property
    def role_value(self) -> str:
        return self.role.value if self.role else "unassigned"
