"""Persona as data (spec section 12).

Personality traits are numeric; the prompt builder translates them into
behavioral tendencies. Instructions describe tendencies, never forced
strategies ("you always lie" is banned).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from simulation.models.base import StrictModel

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
