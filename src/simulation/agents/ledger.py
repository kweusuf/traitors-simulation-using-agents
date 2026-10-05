"""The suspicion ledger: what a player thinks, ranked, in both directions.

`Beliefs` already stored a per-target read with confidence and a history of
movement. Nothing wrote to it at runtime and nothing showed it to the model,
so a player had no record of their own reasoning to consult: they saw the
transcript once, in one forward pass, and were asked for a bare `target`.
That is why voting tracked chance - see docs/learnings.md.

This module is the missing wiring, and it is a separate generation on
purpose. Reading a suspicion out of the `confidence` on a message action
would be free, but it is unreliable: the model reports how sure it is of
*what it just said*, not who it thinks is guilty. A dedicated call asks the
question directly and returns a ranked list, which is what makes the ledger
something a player can act on rather than a number that happened to be
nearby.

Two lists, not one. Suspects are who you think is guilty; allies are who you
have decided is clean and will defend. Keeping the second list short and
separate is deliberate:

- A player defending someone who is actually a traitor is the most
  interesting thing that can happen in this game, and it can only arise if
  the defence is unconditional. Judged purely on interaction, a convincing
  traitor earns a place on the innocent list and the faithful will burn a
  vote on them.
- A long innocent list is not a defence, it is a preference. One or two names
  makes the commitment costly and visible, so rallying has something to
  rally *for*.

The ledger records conclusions, never reasoning transcripts. Evidence is one
line per name, because that is what makes a read defensible when the room
pushes back.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from pydantic import Field

from simulation.models.base import StrictModel

# Both lists are shown to the player every phase, so their size is a standing
# prompt cost. Three is enough to have a first choice and a fallback;
# twenty-one lines would crowd the transcript, which is where the evidence is.
DEFAULT_SUSPECT_SIZE = 3

# The innocent list is shorter on purpose - see the module docstring. Three is
# the hard cap; one or two is what a player should actually return.
DEFAULT_ALLY_SIZE = 2

LEDGER_SYSTEM = """\
You keep two private lists: who you suspect, and who you have decided is
clean. You are not deciding a vote here. You are recording where your read of
the table stands, so that your next message and your next vote follow from it
instead of from whatever you happened to read most recently.

Who you suspect:
- At most {suspects} names, ordered by how much you suspect them.
- Confidence is your honest estimate that this person is a traitor, not how
  interesting they are to argue about. Most people are below 0.4.
- Evidence is one short line naming something a specific person did that
  could be observed. "Vague and quiet" is not evidence. "Deflected twice when
  asked who they killed, and their vote did not match their claim" is.

Who you have cleared:
- At most {allies} names, and only if you can say why you trust them.
- Base this purely on what they have done in front of you. If someone has
  behaved consistently and answered under pressure, they are clean. Whether
  they are actually a traitor is not something you can see, and you must not
  try to work it out - judge the person you have observed.
- Once you name someone here you will defend them. Be slow to add and even
  slower to drop. An empty list is an honest state and is fine.

If you have nothing yet on either list, return empty lists rather than
guesses.
"""


class Suspect(StrictModel):
    """One entry in a player's ranked read of the table."""

    player: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


class Ally(StrictModel):
    """Someone this player has decided is clean, and will defend."""

    player: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


class Ledger(StrictModel):
    """A player's current ranked read of the table, both directions."""

    suspects: list[Suspect] = Field(default_factory=list)
    allies: list[Ally] = Field(default_factory=list)

    @property
    def top(self) -> Optional[Suspect]:
        return self.suspects[0] if self.suspects else None
def ledger_schema(
    suspects: int = DEFAULT_SUSPECT_SIZE, allies: int = DEFAULT_ALLY_SIZE
) -> type[Ledger]:
    """Schema for the ledger call, with both lists bounded.

    Bounded in the schema rather than only in the prompt because the bound is
    the thing a small model ignores: asked for "at most three" it will
    cheerfully return eleven names, and eleven lines is a standing prompt tax
    on every turn after it.
    """
    from pydantic import create_model

    bounded = create_model(  # type: ignore[misc,no-redef]
        "BoundedLedger",
        suspects=(
            list[Suspect],
            Field(
                default_factory=list,
                max_length=suspects,
                description=f"At most {suspects}, most suspicious first.",
            ),
        ),
        allies=(
            list[Ally],
            Field(
                default_factory=list,
                max_length=allies,
                description=f"At most {allies} people judged clean.",
            ),
        ),
        __base__=Ledger,
    )
    # `create_model` builds a fresh class and does not carry properties over,
    # so the convenience accessors are attached here rather than being
    # redefined per schema. Without this a caller that reached for
    # `.top` would get an AttributeError only on the bounded schema, which
    # is the one every run actually uses.
    bounded.top = property(lambda self: self.suspects[0] if self.suspects else None)
    bounded.protected = property(
        lambda self: self.allies[0] if self.allies else None
    )
    return bounded


def ledger_prompt(
    view,
    current: Iterable[str],
    round_number: int,
    suspects: int = DEFAULT_SUSPECT_SIZE,
    allies: int = DEFAULT_ALLY_SIZE,
) -> str:
    """The user message for the ledger call.

    Carries the evidence the player would reason from, capped hard. This is
    not the moment to read the whole transcript: the ledger is a
    prioritisation task, and a faithful pass over fifty thousand characters
    costs a generation to return the same few names.
    """
    lines = [
        f"Round {round_number}. Players still in the game: "
        + ", ".join(sorted(view.alive_players)),
    ]
    if current:
        lines.append(
            "\nYour lists so far (oldest first):\n  " + "\n  ".join(current)
        )
        lines.append(
            "Revise them. A suspect who has done nothing new drops off; "
            "someone who has given you a real reason moves up. An ally stays "
            "unless they have actually broken your trust - not because the "
            "table is turning on them."
        )
    else:
        lines.append(
            "\nYou have not settled on anyone yet. Watch who is asked a "
            "question and does not answer it, who benefits when the table "
            "looks elsewhere, and who answers honestly when it would have "
            "been easy for them not to."
        )
    lines.append(
        f"\nReturn at most {suspects} suspects and at most {allies} people "
        f"you have cleared. Evidence must name something {view.agent_id} "
        f"could actually have observed."
    )
    return "\n".join(lines)


def apply_ledger(ledger: Ledger, beliefs, round_number: int) -> None:
    """Fold a fresh read into the belief store, both directions.

    Goes through `Beliefs.update` rather than replacing state, so the existing
    history keeps recording how a read moved. A player who hardens on someone
    for three rounds and then drops them is making a different claim from one
    who was always suspicious, and only the history shows which.

    An ally is recorded as a belief of `faithful`. That is not the truth - it
    is what this player concluded - which is exactly why the two can disagree.
    """
    for suspect in ledger.suspects:
        beliefs.update(suspect.player, "traitor", suspect.confidence, round_number)
    for ally in ledger.allies:
        beliefs.update(ally.player, "faithful", ally.confidence, round_number)


def _ranked(beliefs, role: str, size: int) -> list[str]:
    entries = [
        (target, belief)
        for target, belief in beliefs.all.items()
        if belief.suspected_role == role and belief.confidence > 0
    ]
    entries.sort(key=lambda pair: pair[1].confidence, reverse=True)
    lines = []
    for target, belief in entries[:size]:
        held = (
            f", held since round {belief.history[0].round_number}"
            if belief.history
            else ""
        )
        lines.append(f"{target} ({belief.confidence:.2f}{held})")
    return lines


def ledger_lines(
    beliefs, suspects: int = DEFAULT_SUSPECT_SIZE, allies: int = DEFAULT_ALLY_SIZE
) -> list[str]:
    """Render both lists for the prompt, most confident first.

    Sorted by confidence rather than insertion order: the point of a ranked
    list is that the top of it is the answer, and an alphabetical list throws
    that away.
    """
    lines = [f"- {line}" for line in _ranked(beliefs, "traitor", suspects)]
    cleared = _ranked(beliefs, "faithful", allies)
    if cleared:
        lines.append("")
        lines.append("People you have cleared, and will defend:")
        lines.extend(f"- {line}" for line in cleared)
    return lines


def parse_ledger(payload) -> Ledger:
    """Parse the ledger call's reply, tolerating a bare list.

    A bare list is what a small model returns when the wrapper object is not
    echoed verbatim, and it loses nothing - the fields are all inside the
    entries. Refusing it would cost a generation to obtain a shape the model
    already produced.
    """
    data = payload
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return Ledger()
    if isinstance(data, list):
        data = {"suspects": data}
    if not isinstance(data, dict):
        return Ledger()
    try:
        return Ledger.model_validate(data)
    except Exception:
        return Ledger()

    @property
    def protected(self) -> Optional[Ally]:
        return self.allies[0] if self.allies else None