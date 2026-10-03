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
POINTER = 1.5        # a general one-line pointer at something that was said

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


# Longest pointer worth storing. A pointer exists to replace a full message
# in a later prompt, so a pointer nearly as long as the message saves nothing.
MAX_POINTER_CHARS = 160


def usable_pointer(gist: str | None, content: str) -> str:
    """The pointer to store for a message: the model's own, or a fallback.

    The model is asked for the pointer in the same call that writes the
    message, which is free, but it is a small model and "also summarise
    this" is an easy instruction to half-obey. Three ways that goes wrong,
    all handled here rather than at the prompt:

    - no pointer at all, so the deterministic gist is used instead;
    - a pointer that is a verbatim slice of the message, which is the
      "summarise" instruction answered with a copy and is no more compact
      than the message it was meant to replace;
    - a pointer so long it costs as much as the message.

    Never raises and never returns empty: the caller always gets something
    to store, because a memory row is expected to have content.
    """
    fallback = _first_clause(content)
    if not gist:
        return fallback
    pointer = " ".join(gist.split())[:MAX_POINTER_CHARS]
    if not pointer:
        return fallback
    # A pointer that merely repeats the head of the message has not been
    # distilled, it has been copied; the deterministic clause is no worse and
    # costs nothing.
    head = " ".join(content.split())[: len(pointer)]
    if pointer.lower() == head.lower():
        return fallback
    return pointer


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
    remember_everything: bool = False,
) -> list[MemoryWrite]:
    """The memories one event produces, in the order they should be written.

    `remember_everything` adds a decaying pointer for every message on top of
    the marker-triggered memories. It is off by default, so a config that says
    nothing keeps storing only what the show's rules make worth keeping.
    """
    history = list(history or [])
    kind = event["type"]
    actor = event.get("actor")
    targets = event.get("targets") or []
    payload = event.get("payload") or {}
    round_number = int(event.get("round") or 0)
    # The pointer the model wrote alongside the message, if any. Absent unless
    # `co_generate_gist` is on and the model obeyed; every use below falls
    # back to the deterministic clause, so this is always optional.
    gist = payload.get("gist")
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
        # Counted once per nominee, not once per accusation. Looping per
        # accuser wrote the same tally line N times, and the N copies tied
        # on salience and so filled the whole top-6 of the prompt.
        tally: dict[str, int] = {}
        for _accuser, nominee in accusations.items():
            tally[nominee] = tally.get(nominee, 0) + 1
        for accuser, nominee in sorted(accusations.items()):
            # The accuser's own act, in their own voice.
            out.append(
                MemoryWrite(
                    content=f"You named {nominee} for the round table.",
                    kind="nomination",
                    subjects=(nominee,),
                    salience=ACCUSATION,
                    audience=NAMED,
                    named=frozenset({accuser}),
                )
            )
        for nominee, count in sorted(tally.items()):
            total = counts.get(nominee, count)
            # The nominee's side of it, phrased for them. Telling a nominee
            # "you named <themselves>" was both wrong and the single
            # loudest thing in their prompt.
            out.append(
                MemoryWrite(
                    content=(
                        f"{count} player(s) named you for the round table; "
                        f"the nominations ran {total}."
                    ),
                    kind="nominated",
                    subjects=(nominee,),
                    salience=ACCUSATION,
                    audience=NAMED,
                    named=frozenset({nominee}),
                )
            )
            # And the room's view, which is the same fact with the number.
            if count > 1:
                out.append(
                    MemoryWrite(
                        content=f"{nominee} led the nominations, {total} in all.",
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
                    content=f"{actor} called you out: {usable_pointer(gist, body)}",
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
                    content=f"In private, {actor} told you: {usable_pointer(gist, body)}",
                    kind="bequest",
                    subjects=(actor,),
                    salience=BEQUEST,
                    audience=NAMED,
                    named=named,
                )
            )
            return out

    if kind in ("PUBLIC_MESSAGE", "PRIVATE_MESSAGE") and remember_everything:
        # A pointer for every message, not only for marker-triggered ones.
        #
        # This is the half of the pointer design that actually shrinks a
        # prompt. Without it, memory holds only accusations and bequests, and
        # the transcript - the 93% of the prompt - has nothing to shrink
        # *into*. With it, a message is remembered as one short line that
        # decays, so an old exchange leaves the prompt on its own instead of
        # being carried until it falls out of the window.
        #
        # Salience sits below ACCUSATION so that a real accusation, which is
        # also stored by the branch above, always outranks a general pointer
        # to the same round. The pointer is the memory of a remark, and it
        # should read as one.
        #
        # This branch does not inherit `body` from the branches above: each of
        # those binds it inside its own `if`, so a message with no targets
        # and no bequest marker arrives here with `body` unbound. It is read
        # from the payload here for that reason.
        out.append(
            MemoryWrite(
                content=usable_pointer(gist, str(payload.get("content") or "")),
                kind="pointer",
                subjects=(actor,),
                salience=POINTER,
                audience=ALL,
            )
        )
        return out

    return out
