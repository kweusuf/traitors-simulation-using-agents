"""Persona as data (spec section 12).

Personality traits are numeric; the prompt builder translates them into
behavioral tendencies. Instructions describe tendencies, never forced
strategies ("you always lie" is banned).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import Field

from simulation.models.base import StrictModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from simulation.agents.goals import Goals

# trait -> (high threshold, high instruction, low instruction)
TRAIT_INSTRUCTIONS: dict[str, tuple[float, str, str]] = {
    "analytical": (
        0.7,
        "You carefully weigh evidence before drawing conclusions.",
        "You go with impressions and gut reads rather than detailed analysis.",
    ),
    "assertiveness": (
        0.7,
        "You speak up and push your reads in group discussion.",
        "You tend to defer to others in group discussion.",
    ),
    "sociability": (
        0.7,
        "You actively build rapport and keep social contact flowing.",
        "You keep a low social profile and engage sparingly.",
    ),
    "trust": (
        0.7,
        "You extend trust readily and treat cooperation as the default.",
        "You extend trust slowly and verify claims independently.",
    ),
    "risk_tolerance": (
        0.7,
        "You are comfortable taking bold, visible swings.",
        "You prioritize personal safety over bold moves.",
    ),
}

# The goals the cast's deceivers carry. A persona written around one of these
# is a practised liar by construction; the other nineteen are written as
# honest contestants. That gap is not a judgement about the characters, it is
# where the writing put them, and it is what the traitor mandate has to
# absorb: the deal is persona-blind, so it can and does hand the tower to
# three players the cast never wrote as liars.
DECEPTION_GOALS = frozenset(
    {
        "redirect_suspicion_early",
        "be_everyones_confidant",
        "build_alliances",
    }
)


class Persona(StrictModel):
    description: str = ""
    personality: dict[str, float] = Field(default_factory=dict)

    def instructions(self) -> list[str]:
        """Translate traits into behavioral guidance (no forced strategy)."""
        lines: list[str] = []
        if self.description:
            lines.append(self.description)
        for trait, value in sorted(self.personality.items()):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"trait '{trait}' must be within [0, 1]")
            if trait not in TRAIT_INSTRUCTIONS:
                continue
            threshold, high, low = TRAIT_INSTRUCTIONS[trait]
            lines.append(high if value >= threshold else low)
        return lines

    def deception_aptitude(self, goals: "Goals | None" = None) -> float:
        """How well this player can hold a false line, in [0, 1].

        Not a moral score: a capacity. Sustaining a lie across rounds needs
        enough analysis to keep the story straight, a low enough default trust
        that assuming good faith is not the reflex, and the appetite to be
        seen making a claim that can be checked.

        The goal signal is half the score on purpose. The cast is not written
        evenly - three personas exist to deceive and the rest were written for
        a faithful game - so two players with similar trait numbers can be
        very differently equipped. Wilf (0.85) against imran (0.28) is the
        distinction this is for, and it is deliberately coarse: it selects
        which mandate a drawn traitor is handed, nothing finer.
        """
        return round(0.5 * self._trait_aptitude() + 0.5 * self._goal_signal(goals), 3)

    def _trait_aptitude(self) -> float:
        traits = self.personality or {}
        analytical = traits.get("analytical", 0.5)
        risk = traits.get("risk_tolerance", 0.5)
        # Inverted: a player who extends trust readily is the least braced
        # for the possibility that they are the one being lied to.
        trust = traits.get("trust", 0.5)
        return (analytical + risk + (1.0 - trust)) / 3.0

    @staticmethod
    def _goal_signal(goals: "Goals | None") -> float:
        if goals is None:
            return 0.0
        return 1.0 if DECEPTION_GOALS & set(goals.secondary or []) else 0.0


def load_persona(path: str | Path) -> Persona:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"persona {path} must be a YAML mapping")
    data.pop("goals", None)  # goals live in goals.py (spec section 13)
    return Persona.model_validate(data)


def load_persona_bundle(path: str | Path) -> tuple[Persona, "Goals"]:
    """A persona file may carry both personality and goals; they stay separate models."""
    from simulation.agents.goals import Goals

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"persona {path} must be a YAML mapping")
    goals = Goals.model_validate(data.pop("goals", {}) or {})
    return Persona.model_validate(data), goals
