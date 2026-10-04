"""Structured beliefs, separate from memory (spec section 15).

Memory: "Alice accused Bob in Round 2."
Belief: "I believe Bob is a traitor with 0.70 confidence."

Only structured summaries are stored, never chain-of-thought.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from simulation.models.base import StrictModel


class BeliefShift(StrictModel):
    """One movement of a belief, from where it was to where it went."""

    round_number: int
    from_role: Optional[str] = None
    to_role: Optional[str] = None
    from_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    to_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def moved_toward_traitor(self) -> bool:
        """Whether this shift hardened a suspicion rather than raising it."""
        return (self.to_confidence > self.from_confidence
                and self.to_role == "traitor")


class Belief(StrictModel):
    suspected_role: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    updated_round: int = 0
    # How the belief got here, oldest first. Kept so that movement is
    # visible: "Alice went from 0.45 to 0.65 on Bob" is a different claim
    # from "Alice is at 0.65 on Bob", and a game of accusations is mostly
    # about the first. The current state is still the last entry, so this
    # changes what can be read, not what the prompt shows.
    history: list[BeliefShift] = Field(default_factory=list)


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
        previous = self._by_target.get(target)
        history = list(previous.history) if previous else []
        if previous is not None:
            # A belief that moves and one that is restated identically are
            # different events. Only a real move is recorded, so history stays
            # a record of changes rather than of updates.
            if (previous.suspected_role != suspected_role
                    or abs(previous.confidence - confidence) > 1e-9):
                history.append(
                    BeliefShift(
                        round_number=round_number,
                        from_role=previous.suspected_role,
                        to_role=suspected_role,
                        from_confidence=previous.confidence,
                        to_confidence=confidence,
                    )
                )
        self._by_target[target] = Belief(
            suspected_role=suspected_role,
            confidence=confidence,
            updated_round=round_number,
            history=history,
        )

    def shifts(self, target: str) -> list[BeliefShift]:
        """How a player's read of one target moved, oldest first."""
        belief = self._by_target.get(target)
        return list(belief.history) if belief else []

    def hardening(self, target: str) -> int:
        """How many times a suspicion of `target` stiffened.

        A read that swings to 0.9 and back is not the same as one that climbs
        steadily, and which of those a player did is most of what separates a
        player who is close to the truth from one who is merely loud.
        """
        return sum(1 for s in self.shifts(target) if s.moved_toward_traitor)

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
