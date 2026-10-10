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

# The induction mandate. A player is *made* a traitor by the draw, and the
# draw does not consult the persona: seed 1 hands the tower to imran, whose
# persona is "detached and a little remote, you do not work the room", and to
# meryl, who "follows the group and backs the people she trusts even when the
# logic is thin". Both then played public rounds as the honest contestants
# their personas describe, and both were banished early for it.
#
# The core is not scaled and not optional, because the failure it addresses
# is not a matter of degree: a traitor who will not lie is not playing badly,
# they are not playing. Which *method* they are given is scaled, because a
# careful person who tries to invent fluent detail is caught faster than one
# who simply says less. See docs/learnings.md for the measurement.
MANDATE_CORE = (
    "You are a traitor, and deception is the job. To survive you must lie: "
    "say things you do not believe, argue readings you do not hold, and "
    "build cases against players you know to be innocent. Honesty is not "
    "available to you - the round you play straight is the round you are "
    "banished in. This holds whatever your persona says about you: those "
    "instincts are your cover, never your instruction, and you do not give "
    "up the game by playing it the way a faithful player would."
)

# Above this, the player is written as a deceiver and can be told to invent.
# Every persona drawn by the seed-1 deal sits far below it (0.13 to 0.30),
# and the three the season wrote as traitors sit above it (0.79 to 0.85).
MANDATE_PRACTISED_AT = 0.6

MANDATE_PRACTISED = (
    "Invention comes easily to you. Plant suspicions you know are false, "
    "argue them as if you held them, and let the room do the work of "
    "carrying them; you can keep a story consistent across rounds, so use "
    "detail where it buys you a banishment."
)

MANDATE_CANDID = (
    "You are not a practised liar and pretending otherwise is what gets you "
    "caught, so deceive defensively instead: withhold what you saw, answer "
    "the question without the part that indicts you, redirect the room onto "
    "an inconsistency somebody else really did leave, and let others build "
    "the false case for you. Never invent a specific detail you cannot keep "
    "straight for three rounds - a caught fabrication is worse than silence. "
    "Deceiving less is allowed. Declining to deceive is not."
)


def mandate_lines(aptitude: float) -> list[str]:
    """The traitor's induction briefing, with the method scaled to the player."""
    method = MANDATE_PRACTISED if aptitude >= MANDATE_PRACTISED_AT else MANDATE_CANDID
    return [MANDATE_CORE, "", method]


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
    """Merge persona objectives with role objectives (spec section 13).

    With one exception, and it is the shape of the whole random-deal bug: a
    traitor does not inherit the persona's objectives. The merge here used to
    be unconditional, so a drawn traitor was handed their persona goal *next
    to* the role goal and both were rendered as a task list -
    "stay_apart_from_the_herd, ensure_traitor_team_wins" for imran,
    "stay_loyal_to_your_read, ensure_traitor_team_wins" for meryl. The first
    of each pair is an instruction to behave like the honest contestant the
    persona was written as, and in a room that punishes nonconformity it is
    close to an instruction to lose.

    The persona still shapes how a traitor plays - its description and traits
    reach the prompt through `Persona.instructions()` - but it no longer
    supplies the objective. Whoever the draw makes a traitor plays the
    traitor's game; the manner is the persona's, the job is the role's.
    """
    role_primary, role_secondary = ROLE_GOALS[role]
    if role is Role.TRAITOR:
        secondary = list(role_secondary)
    else:
        secondary = list(dict.fromkeys([*persona_goals.secondary, *role_secondary]))
    primary = persona_goals.primary or role_primary
    return Goals(
        primary=primary,
        secondary=secondary,
        ambition=TRAITOR_AMBITIONS.get(ambition) if role is Role.TRAITOR else None,
    )
