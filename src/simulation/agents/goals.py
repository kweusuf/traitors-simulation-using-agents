"""Goals: objectives separate from personality (spec section 13).

The environment injects role-specific goals on top of persona goals.
"""

from __future__ import annotations

from simulation.engine.state import Role
from simulation.models.base import StrictModel

ROLE_GOALS: dict[Role, tuple[str, list[str]]] = {
    Role.TRAITOR: ("survive", ["ensure_traitor_team_wins"]),
    Role.FAITHFUL: ("survive", ["identify_traitors"]),
}


class Goals(StrictModel):
    primary: str = "survive"
    secondary: list[str] = []

    def lines(self) -> list[str]:
        lines = [f"Primary goal: {self.primary}."]
        if self.secondary:
            lines.append("Secondary goals: " + ", ".join(self.secondary) + ".")
        return lines


def inject_role_goals(persona_goals: Goals, role: Role) -> Goals:
    """Merge persona objectives with role objectives (spec section 13)."""
    role_primary, role_secondary = ROLE_GOALS[role]
    secondary = list(dict.fromkeys([*persona_goals.secondary, *role_secondary]))
    primary = persona_goals.primary or role_primary
    return Goals(primary=primary, secondary=secondary)
