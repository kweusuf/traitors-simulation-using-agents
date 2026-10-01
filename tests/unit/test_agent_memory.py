"""Who remembers what, and who must never know.

The traitor-night scoping is the point of this file: `TRAITOR_KILL`
carries the council in its payload, and a faithful player who can read it
has the whole tower.
"""

from __future__ import annotations

import asyncio

from simulation.engine.state import GameState, PlayerState, Role
from simulation.environments.traitors.memory import ALL, public_rivals, writes_for
from simulation.memory.short_term import ShortTermMemory

FAITHFUL = ["ann", "bea", "cal"]
TRAITORS = ["tara", "tom"]


def make_state() -> GameState:
    state = GameState(game_id="g", phase="round_table")
    for pid in FAITHFUL + TRAITORS:
        state.players[pid] = PlayerState(player_id=pid, name=pid)
        state.alive_players.add(pid)
        state.roles[pid] = Role.FAITHFUL if pid in FAITHFUL else Role.TRAITOR
    return state


def visible(state: GameState, event, agent_ids) -> dict[str, list[str]]:
    """What each named agent would actually be told by this event."""
    out: dict[str, list[str]] = {pid: [] for pid in agent_ids}
    for write in writes_for(state, event, []):
        for pid in agent_ids:
            if write.visible_to(pid, state):
                out[pid].append(write.content)
    return out


def test_night_murder_is_public_but_the_council_is_not() -> None:
    state = make_state()
    kill = {
        "type": "TRAITOR_KILL",
        "actor": None,
        "targets": ["bea"],
        "round": 2,
        "payload": {
            "proposals": {"tara": "bea", "tom": "bea"},
            "final": {"tara": "bea", "tom": "bea"},
        },
    }
    # The council is traitor-only, and it names the traitors.
    seen = visible(state, kill, FAITHFUL + TRAITORS)
    for pid in FAITHFUL:
        assert seen[pid] == [], f"{pid} learned the council"
    for pid in TRAITORS:
        assert seen[pid], f"{pid} forgot the council"
        assert "tara argued for bea" in " ".join(seen[pid])

    # The fact of the death is public, and carries no culprit.
    death = {
        "type": "PLAYER_ELIMINATED",
        "actor": "bea",
        "round": 2,
        "payload": {"method": "night", "votes": {}},
    }
    public = visible(state, death, FAITHFUL + TRAITORS)
    for pid in FAITHFUL + TRAITORS:
        assert any("murdered in the night" in c for c in public[pid])
        joined = " ".join(public[pid])
        assert "tara" not in joined and "tom" not in joined


def test_banishment_is_public_with_the_whole_ballot() -> None:
    state = make_state()
    banish = {
        "type": "PLAYER_ELIMINATED",
        "actor": "cal",
        "round": 3,
        "payload": {"method": "vote", "votes": {"ann": 4, "bea": 2, "tom": 1}},
    }
    seen = visible(state, banish, FAITHFUL)
    assert seen["ann"], "the voter could not remember their own ballot"
    joined = " ".join(seen["ann"])
    assert "banished by vote" in joined
    assert "ann 4" in joined and "bea 2" in joined


def test_private_bequest_reaches_only_the_pair() -> None:
    state = make_state()
    message = {
        "type": "PRIVATE_MESSAGE",
        "actor": "tara",
        "targets": ["ann"],
        "round": 4,
        "payload": {
            "content": "If I'm eliminated, remember this: tom gave the order."
        },
    }
    seen = visible(state, message, FAITHFUL + TRAITORS)
    assert seen["ann"], "the recipient forgot the bequest"
    assert "remember" in " ".join(seen["ann"]).lower()
    assert seen["bea"] == [], "a bystander overheard a private confession"
    assert seen["cal"] == [], "a bystander overheard a private confession"


def test_ordinary_chatter_writes_nothing() -> None:
    state = make_state()
    small_talk = {
        "type": "PRIVATE_MESSAGE",
        "actor": "ann",
        "targets": ["bea"],
        "round": 4,
        "payload": {"content": "Morning all, how is everyone feeling today?"},
    }
    assert visible(state, small_talk, FAITHFUL) == {"ann": [], "bea": [], "cal": []}


def test_eliminated_player_stops_accumulating_memories() -> None:
    state = make_state()
    state.players["bea"].alive = False
    state.alive_players.discard("bea")
    state.eliminated_players.add("bea")
    death = {
        "type": "PLAYER_ELIMINATED",
        "actor": "cal",
        "round": 3,
        "payload": {"method": "vote", "votes": {}},
    }
    seen = visible(state, death, ["bea", "ann"])
    assert seen["bea"] == [], "a dead player kept taking notes"
    assert seen["ann"]


def test_nomination_is_recorded_from_both_sides() -> None:
    state = make_state()
    tally = {
        "type": "NOMINATION_TALLY",
        "actor": "host",
        "targets": ["bea"],
        "round": 2,
        "payload": {
            "counts": {"bea": 3},
            "nominees": ["bea"],
            "accusations": {"ann": "bea", "cal": "bea"},
        },
    }
    seen = visible(state, tally, FAITHFUL)
    # The accuser remembers naming someone; the nominee is told they were named.
    assert any("You named bea" in c for c in seen["ann"])
    assert seen["bea"], "the nominee never found out they were named"
    assert seen["cal"]


def test_animosity_puts_the_survivor_under_scrutiny() -> None:
    """The one write that moves suspicion toward a living player."""
    state = make_state()
    history = [
        {
            "type": "NOMINATION_TALLY",
            "actor": "host",
            "round": 2,
            "payload": {"accusations": {"tara": "bea"}, "counts": {"bea": 3}},
        }
    ]
    death = {
        "type": "PLAYER_ELIMINATED",
        "actor": "bea",
        "round": 3,
        "payload": {"method": "vote", "votes": {"ann": 2}},
    }
    writes = writes_for(state, death, history)
    for pid in FAITHFUL:
        texts = [w.content for w in writes if w.visible_to(pid, state)]
        assert any("tara is under scrutiny" in t for t in texts), (
            f"{pid} did not see that the feud made tara a target"
        )


def test_public_rivals_needs_public_evidence() -> None:
    state = make_state()
    # A vote alone is not enough; a nomination is.
    one_vote = [
        {
            "type": "PLAYER_ELIMINATED",
            "actor": "bea",
            "round": 2,
            "payload": {"method": "vote", "votes": {"ann": 3}},
        }
    ]
    assert public_rivals(state, one_vote, "bea", 3) == []
    named = one_vote + [
        {
            "type": "NOMINATION_TALLY",
            "actor": "host",
            "round": 2,
            "payload": {"accusations": {"ann": "bea"}, "counts": {}},
        }
    ]
    assert public_rivals(state, named, "bea", 3) == ["ann"]


def test_a_nominee_is_told_they_were_named_not_that_they_named() -> None:
    """The nominee's memory has to be phrased for them.

    It used to be addressed to the accuser, so a player nobody liked was
    told "You named wilf for the round table" about themselves, once per
    player who named them.
    """
    state = make_state()
    tally = {
        "type": "NOMINATION_TALLY",
        "actor": "host",
        "targets": ["tara"],
        "round": 2,
        "payload": {
            "counts": {"tara": 4},
            "nominees": ["tara"],
            "accusations": {"ann": "tara", "cal": "tara", "tom": "tara"},
        },
    }
    seen = visible(state, tally, ["tara", "ann", "bea"])
    joined = " ".join(seen["tara"])
    assert "named you" in joined, f"the nominee was not told: {joined}"
    assert "You named tara" not in joined, "the nominee was told they named themselves"
    # The accuser still gets their own act, exactly once, alongside the
    # room's tally (they are in the room too).
    assert seen["ann"].count("You named tara for the round table.") == 1
    assert "tara led the nominations" in " ".join(seen["ann"])


def test_a_heavily_nominated_player_gets_a_diverse_memory_block() -> None:
    """17 people naming one player must not produce 17 identical lines.

    This is the case that broke the feature: the copies tied on salience,
    sorted together, and filled the whole top-6 of the prompt with one
    sentence.
    """
    state = make_state()
    players = ["ann", "bea", "cal", "tom"] + TRAITORS
    tally = {
        "type": "NOMINATION_TALLY",
        "actor": "host",
        "targets": ["wilf"],
        "round": 2,
        "payload": {
            "counts": {"wilf": 17},
            "nominees": ["wilf"],
            "accusations": {p: "wilf" for p in players if p != "wilf"},
        },
    }
    writes = writes_for(state, tally, [])
    for pid in ("wilf", "ann"):
        texts = [w.content for w in writes if w.visible_to(pid, state)]
        assert len(texts) == len(set(texts)), f"{pid} got duplicate memories"
    # And the room hears it once, not seventeen times.
    room = [w.content for w in writes if w.audience == ALL]
    assert len(room) == 1, f"the room got {len(room)} copies"


def test_recall_never_repeats_a_line() -> None:
    memory = ShortTermMemory("g", "ann")
    for _ in range(5):
        asyncio.run(
            memory.remember(
                {"content": "tom was named for the round table", "round": 1, "salience": 3.0}
            )
        )
    asyncio.run(memory.remember({"content": "bea was murdered", "round": 1, "salience": 4.0}))
    top = memory.recalled(now_round=1, limit=6)
    assert len({i["content"] for i in top}) == len(top)
    assert len(top) == 2, f"expected two distinct memories, got {len(top)}"


def test_recall_forgets_trivia_and_keeps_the_weight() -> None:
    """Decay is the point: an old remark goes, an old accusation stays."""
    memory = ShortTermMemory("g", "ann")
    asyncio.run(
        memory.remember(
            {"content": "bea said the room looked calm", "round": 1, "salience": 1.0}
        )
    )
    asyncio.run(
        memory.remember(
            {
                "content": "tara called bea a traitor to their face",
                "round": 1,
                "salience": 3.0,
                "subjects": ("tara",),
            }
        )
    )
    # Two rounds on, the trivia is under the floor and the accusation is not.
    later = memory.recalled(now_round=3, limit=5)
    kept = [item["content"] for item in later]
    assert kept == ["tara called bea a traitor to their face"]


def test_recall_weights_the_same_event_differently_per_observer() -> None:
    """A friend remembers you; someone you are at odds with does not."""
    memory = ShortTermMemory("g", "ann")
    asyncio.run(
        memory.remember(
            {
                "content": "tara told ann to watch out",
                "round": 5,
                "salience": 3.0,
                "subjects": ("tara",),
            }
        )
    )
    friendly = memory.recalled(now_round=5, limit=5, factor=lambda s, r: 1.0)
    hostile = memory.recalled(now_round=5, limit=5, factor=lambda s, r: 0.0)
    assert len(friendly) == 1
    assert hostile == [], "animosity should silence the memory"

