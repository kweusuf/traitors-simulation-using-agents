"""Goals: objectives separate from personality (spec section 13).

The environment injects role-specific goals on top of persona goals,
plus, for traitors, the hidden ambition (solo or team) seeded at game
start.
"""

from __future__ import annotations

from typing import Optional

from simulation.engine.state import Role
from simulation.models.base import StrictModel

ROLE_GOALS: dict[Role, tuple[str, list[str]]] = {
    Role.TRAITOR: ("survive", ["ensure_traitor_team_wins"]),
    Role.FAITHFUL: ("survive", ["identify_traitors"]),
}

# Seeded per player. A solo traitor wants to be the last one standing,
# a team traitor wants the faction to win together; both only win if
# the traitor side wins at all.
TRAITOR_AMBITIONS: dict[str, str] = {
    "solo": (
        "Be the last traitor standing: you may turn rival traitors over "
        "to the faithful so you win alone."
    ),
    "team": (
        "Win with your traitor allies: work with them to remove every "
        "faithful player."
    ),
}


class Goals(StrictModel):
    primary: str = "survive"
    secondary: list[str] = []
    ambition: Optional[str] = None

    def lines(self) -> list[str]:
        lines = [f"Primary goal: {self.primary}."]
        if self.secondary:
            lines.append("Secondary goals: " + ", ".join(self.secondary) + ".")
        if self.ambition:
            lines.append(f"Ambition: {self.ambition}")
        return lines


def inject_role_goals(
    persona_goals: Goals, role: Role, ambition: Optional[str] = None
) -> Goals:
    """Merge persona objectives with role objectives (spec section 13)."""
    role_primary, role_secondary = ROLE_GOALS[role]
    secondary = list(dict.fromkeys([*persona_goals.secondary, *role_secondary]))
    primary = persona_goals.primary or role_primary
    return Goals(
        primary=primary,
        secondary=secondary,
        ambition=TRAITOR_AMBITIONS.get(ambition) if role is Role.TRAITOR else None,
    )
