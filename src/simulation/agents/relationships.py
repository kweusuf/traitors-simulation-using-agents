"""Structured relationship state (spec section 16).

Optional at runtime: the framework supports relationships without
requiring them for the first game.
"""

from __future__ import annotations

from pydantic import Field

from simulation.models.base import StrictModel


class Relationship(StrictModel):
    trust: float = Field(default=0.0, ge=0.0, le=1.0)
    suspicion: float = Field(default=0.0, ge=0.0, le=1.0)
    threat: float = Field(default=0.0, ge=0.0, le=1.0)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class Relationships:
    def __init__(self) -> None:
        self._by_target: dict[str, Relationship] = {}

    def update(
        self,
        target: str,
        trust: float | None = None,
        suspicion: float | None = None,
        threat: float | None = None,
    ) -> None:
        current = self._by_target.get(target, Relationship())
        updated = Relationship(
            trust=_clamp(current.trust if trust is None else trust),
            suspicion=_clamp(current.suspicion if suspicion is None else suspicion),
            threat=_clamp(current.threat if threat is None else threat),
        )
        self._by_target[target] = updated

    def get(self, target: str) -> Relationship:
        return self._by_target.get(target, Relationship())

    @property
    def all(self) -> dict[str, Relationship]:
        return dict(self._by_target)

    def prompt_lines(self) -> list[str]:
        return [
            f"{target}: trust {rel.trust:.2f}, suspicion {rel.suspicion:.2f}, "
            f"threat {rel.threat:.2f}"
            for target, rel in sorted(self._by_target.items())
        ]
