"""What each player remembers, and who is allowed to remember it.

The engine's event stream is public to the simulation but not to the
players: `TRAITOR_KILL.payload` keys `proposals` and `final` by traitor
name, so ingesting a raw payload would hand the whole tower to every
faithful player. Everything here therefore writes from an explicit
whitelist per event type, and every write names its audience.

Three kinds of knowledge, per the show:

- a night murder is public as a *fact* (somebody died, the traitors did
  it) but the council that chose them is traitor-only;
- a banishment is public down to who voted for whom and what it followed;
- the motive a faithful player holds is reconstructed from the public
  record, never from the council.

See docs/2026-10-01-agent-memory-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from simulation.engine.state import GameState, Role

# Salience bands. Deliberate rather than model-judged: an LLM call per
# message is not affordable at 22 players over 12 rounds, and a rule is
# reproducible.
BEQUEST = 5.0        # an ultimatum, a recruitment offer, a bequest
DEATH = 4.0          # a murder, a banishment
ACCUSATION = 3.0     # a named accusation or a rebuttal on the record
ATTENTION = 3.5      # a survivor coming under the room's scrutiny
PUBLIC_RECORD = 2.0  # nominations and the ballot that followed

ALL = "all"
TRAITORS = "traitors"
NAMED = "named"

# Phrases that make an otherwise ordinary line worth keeping. A private
# "if I'm eliminated, remember this" is the case the design turns on.
BEQUEST_MARKERS = (
    "if i'm eliminated",
    "if i am eliminated",
    "if i'm out",
    "remember this",
    "remember that",
    "don't trust",
    "do not trust",
    "i want you to know",
    "if i go",
    "before i go",
)
ACCUSATION_MARKERS = (
    "traitor",
    "lying",
    "liar",
    "caught you",
    "i don't believe",
    "you're the one",
    "you are the one",
    "not buying",
    "defending you",
    "convenient",
)


def _has(text: str, markers: Iterable[str]) -> bool:
    low = text.lower()
    return any(marker in low for marker in markers)


def _first_clause(text: str, limit: int = 160) -> str:
    """A memory is a gist, not a transcript entry."""
    trimmed = " ".join(text.split())
    for stop in (". ", "? ", "! "):
        head, _, _ = trimmed.partition(stop)
        if len(head) >= 40:
            trimmed = head
            break
    return trimmed[:limit]


@dataclass(frozen=True)
class MemoryWrite:
    """One memory, and the only players allowed to have it."""

    content: str
    kind: str
    subjects: tuple[str, ...]
    salience: float
    audience: str = ALL
    named: frozenset[str] = field(default_factory=frozenset)

    def visible_to(self, agent_id: str, state: GameState) -> bool:
        if self.audience == ALL:
            return agent_id in state.alive_players
        if self.audience == TRAITORS:
            return (
                agent_id in state.alive_players
                and state.roles.get(agent_id) is Role.TRAITOR
            )
        return agent_id in self.named



def public_rivals(
    state: GameState,
    history: list[dict[str, Any]],
    victim: str,
    since_round: int,
) -> list[str]:
    """Living players the room has seen at odds with `victim`.

    Derived from the public record - nominations and ballots - rather than
    from any private state, so every observer agrees on who was at odds
    with whom and the attention effect lands identically for all of them.
    A single nomination counts as enough; a vote against counts half,
    because voting is weaker evidence than naming someone.
    """
    rivals: dict[str, float] = {}
    for event in history:
        if int(event.get("round") or 0) < since_round - 2:
            continue
        if event["type"] == "NOMINATION_TALLY":
            for accuser, nominee in (event["payload"].get("accusations") or {}).items():
                if nominee == victim and accuser != victim:
                    rivals[accuser] = rivals.get(accuser, 0.0) + 1.0
        elif event["type"] == "PLAYER_ELIMINATED":
            for voter, target in (event["payload"].get("votes") or {}).items():
                if target == victim and voter != victim:
                    rivals[voter] = rivals.get(voter, 0.0) + 0.5
    return sorted(
        pid
        for pid, weight in rivals.items()
        if weight >= 1.0 and pid in state.alive_players
    )



def _public_motive(history: list[dict[str, Any]], pid: str) -> str:
    """Why the room might have expected this death, in public terms only.

    Reconstructed from nominations already in the log, so it never names a
    traitor or a council. This is the "vague idea from the general vibe"
    a faithful player is entitled to, and it is what makes a death legible
    without leaking the deliberation behind it.
    """
    named = sum(
        1
        for event in history
        if event["type"] == "NOMINATION_TALLY"
        and pid in (event["payload"].get("nominees") or [])
    )
    backed = sum(
        1
        for event in history
        if event["type"] == "NOMINATION_TALLY"
        and event["payload"].get("counts", {}).get(pid)
    )
    if named:
        return f" They had been put up at the round table {named} time(s) recently."
    if backed:
        return " They had been named in the nominations, if only a few times."
    return ""


def writes_for(
    state: GameState,
    event: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> list[MemoryWrite]:
    """The memories one event produces, in the order they should be written."""
    history = list(history or [])
    kind = event["type"]
    actor = event.get("actor")
    targets = event.get("targets") or []
    payload = event.get("payload") or {}
    round_number = int(event.get("round") or 0)
    out: list[MemoryWrite] = []

    if kind == "PLAYER_ELIMINATED":
        victim = actor
        if payload.get("method") == "night":
            # Public fact, public reason. No culprit, no council.
            out.append(
                MemoryWrite(
                    content=(
                        f"{victim} was murdered in the night by the traitors."
                        + _public_motive(history, victim)
                    ),
                    kind="murder",
                    subjects=(victim,),
                    salience=DEATH,
                )
            )
        else:
            votes = payload.get("votes") or {}
            tally = ", ".join(
                f"{name} {count}"
                for name, count in sorted(
                    votes.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
            out.append(
                MemoryWrite(
                    content=f"{victim} was banished by vote. The ballot: {tally}.",
                    kind="banishment",
                    subjects=(victim,),
                    salience=DEATH,
                )
            )
        # Animosity becomes attention: anyone publicly at odds with the
        # dead player is now the centre of the room, because the feud is a
        # motive and everyone could see it. This is the one write that
        # moves suspicion toward somebody still in the game.
        for rival in public_rivals(state, history, victim, round_number):
            out.append(
                MemoryWrite(
                    content=(
                        f"Since {victim} went, {rival} is under scrutiny: the "
                        f"room saw the two of them at odds, and a feud is a "
                        f"motive."
                    ),
                    kind="attention",
                    subjects=(rival,),
                    salience=ATTENTION,
                )
            )
        return out

    if kind == "TRAITOR_KILL":
        # Traitors only. The payload names every traitor, so it must never
        # be written to a faithful player under any circumstances.
        victim = targets[0] if targets else "someone"
        proposals = payload.get("final") or payload.get("proposals") or {}
        voices = ", ".join(
            f"{who} argued for {whom}" for who, whom in sorted(proposals.items())
        )
        out.append(
            MemoryWrite(
                content=f"The council chose {victim} tonight."
                + (f" {voices}." if voices else ""),
                kind="council",
                subjects=(victim,),
                salience=BEQUEST,
                audience=TRAITORS,
            )
        )
        return out



    if kind == "NOMINATION_TALLY":
        accusations = payload.get("accusations") or {}
        counts = payload.get("counts") or {}
        for accuser, nominee in sorted(accusations.items()):
            out.append(
                MemoryWrite(
                    content=f"You named {nominee} for the round table.",
                    kind="nomination",
                    subjects=(nominee,),
                    salience=ACCUSATION,
                    audience=NAMED,
                    named=frozenset({accuser, nominee}),
                )
            )
            others = sum(
                1
                for other, target in accusations.items()
                if target == nominee and other != accuser
            )
            if others:
                out.append(
                    MemoryWrite(
                        content=(
                            f"{others} other player(s) named {nominee} too; "
                            f"the nominations ran {counts.get(nominee, 0)} "
                            f"to them."
                        ),
                        kind="nomination_count",
                        subjects=(nominee,),
                        salience=PUBLIC_RECORD,
                    )
                )
        return out

    if kind == "ULTIMATUM_ISSUED":
        target = payload.get("target")
        named = frozenset({actor, target} - {None})
        if target:
            out.append(
                MemoryWrite(
                    content=(
                        f"{target} refused the offer and was killed for it. "
                        f"A lone traitor's ultimatum leaves no way out."
                    ),
                    kind="ultimatum",
                    subjects=tuple(sorted(named)),
                    salience=BEQUEST,
                    audience=NAMED,
                    named=named,
                )
            )
        return out

    if kind in ("RECRUIT_OFFERED", "RECRUIT_ACCEPTED", "RECRUIT_DECLINED"):
        offered = payload.get("target") or actor
        by = payload.get("by") or actor
        named = frozenset({offered, by} - {None})
        verb = {
            "RECRUIT_OFFERED": "was offered a place on the traitor team",
            "RECRUIT_ACCEPTED": "took the offer and joined the traitors",
            "RECRUIT_DECLINED": "turned the offer down",
        }[kind]
        out.append(
            MemoryWrite(
                content=f"{offered} {verb}.",
                kind="recruit",
                subjects=tuple(sorted(named)),
                salience=BEQUEST,
                audience=NAMED,
                named=named,
            )
        )
        return out

    if kind == "PUBLIC_MESSAGE" and targets:
        # An accusation reaches the log as a public message aimed at the
        # nominee, so a target is the marker and the content is the reason.
        nominee = targets[0]
        body = str(payload.get("content") or "")
        if nominee and _has(body, ACCUSATION_MARKERS):
            out.append(
                MemoryWrite(
                    content=f"{actor} called you out: {_first_clause(body)}",
                    kind="accusation",
                    subjects=(actor,),
                    salience=ACCUSATION,
                    audience=NAMED,
                    named=frozenset({nominee}),
                )
            )
        return out

    if kind == "PRIVATE_MESSAGE":
        # A confession in a private chat is the bequest case. Only the
        # sender and the recipients can have it.
        recipients = set(targets)
        named = frozenset({actor} | recipients)
        body = str(payload.get("content") or "")
        if recipients and _has(body, BEQUEST_MARKERS):
            out.append(
                MemoryWrite(
                    content=f"In private, {actor} told you: {_first_clause(body)}",
                    kind="bequest",
                    subjects=(actor,),
                    salience=BEQUEST,
                    audience=NAMED,
                    named=named,
                )
            )
        return out

    return out
