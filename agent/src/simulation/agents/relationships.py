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

    def stand_lines(self, size: int = 4) -> list[str]:
        """Where this player's standing sits, for the prompt.

        Separate from `prompt_lines` because the two want different things.
        `prompt_lines` dumps every tracked pair for inspection; this is the
        handful that actually informs a decision, and it says *why* a number
        is what it is - otherwise the model reads "suspicion 0.80" as a fact
        about a person rather than a tally of what has happened so far.

        Both directions, because trust is the other half: a player who has
        quietly backed someone four times is evidence too, and the earlier
        runs showed a room that only ever accumulates suspicion reads every
        disagreement as a tell.

        These numbers were already being maintained on every event and never
        shown to anyone. See docs/code-review.md.
        """
        entries = [
            (target, rel)
            for target, rel in self._by_target.items()
            if rel.trust or rel.suspicion or rel.threat
        ]
        if not entries:
            return []
        entries.sort(
            key=lambda pair: pair[1].suspicion + pair[1].trust, reverse=True
        )
        lines = []
        for target, rel in entries[:size]:
            if rel.suspicion >= 0.5:
                why = "you have caught them evading a question"
            elif rel.suspicion > 0:
                why = "you have pressed them on something they dodged"
            elif rel.trust >= 0.6:
                why = "they have answered you straightly, repeatedly"
            elif rel.trust > 0:
                why = "they have been straight with you so far"
            else:
                why = "you have not had cause to read them either way"
            lines.append(
                f"- {target}: {rel.trust:.0%} trust, "
                f"{rel.suspicion:.0%} doubt - {why}"
            )
        return lines
