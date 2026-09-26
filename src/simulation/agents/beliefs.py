"""Structured beliefs, separate from memory (spec section 15).

Memory: "Alice accused Bob in Round 2."
Belief: "I believe Bob is a traitor with 0.70 confidence."

Only structured summaries are stored, never chain-of-thought.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from simulation.models.base import StrictModel


class Belief(StrictModel):
    suspected_role: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    updated_round: int = 0


class Beliefs:
    def __init__(self) -> None:
        self._by_target: dict[str, Belief] = {}

    def update(
        self,
        target: str,
        suspected_role: Optional[str],
        confidence: float,
        round_number: int,
    ) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if suspected_role is not None and suspected_role not in ("traitor", "faithful"):
            raise ValueError("suspected_role must be 'traitor', 'faithful', or None")
        self._by_target[target] = Belief(
            suspected_role=suspected_role,
            confidence=confidence,
            updated_round=round_number,
        )

    def get(self, target: str) -> Optional[Belief]:
        return self._by_target.get(target)

    @property
    def all(self) -> dict[str, Belief]:
        return dict(self._by_target)

    def prompt_lines(self) -> list[str]:
        lines = []
        for target, belief in sorted(self._by_target.items()):
            role = belief.suspected_role or "unknown"
            lines.append(
                f"{target}: suspected {role} (confidence {belief.confidence:.2f}, "
                f"round {belief.updated_round})"
            )
        return lines
