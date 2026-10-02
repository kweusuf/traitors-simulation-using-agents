"""Wave B mechanics: mission items and the on-trial murder shortlist.

Covers the four config flags, the per-round item award and its seeded
determinism, each new action's rules, the shield and dagger effects on
the tally and the night, the seer's private answer, the murder
shortlist, and one full fake game with every flag on.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from simulation.actions.actions import Action, ActionType
from simulation.communication.channels import Channel
from simulation.communication.visibility import InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import PhaseContext
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.environments.traitors.rules import legal_targets
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.replay import ReplayState, render_transcript
from simulation.experiments.runner import ARTIFACTS, GameRunner
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.repositories import VoteRepository
from simulation.persistence.sink import EventSink


def make_engine(
    seed: int = 42, db: Database | None = None, **game_overrides
) -> GameEngine:
    config = GameConfig(
        game={"players": 6, "traitors": 2, **game_overrides}, seed=seed
    )
    engine = GameEngine(config, EventSink("game-001", db=db), db=db, seed=seed)
    engine.start()
    return engine


def make_env(players: int = 6, traitors: int = 3, seed: int = 42, **game_overrides):
    config = GameConfig(
        game={"players": players, "traitors": traitors, **game_overrides},
        seed=seed,
    )
    env = TraitorsEnvironment(config, EventSink("game-001"))
    env.initialize()
    return env


def roles_of(engine: GameEngine, role: Role) -> list[str]:
    return sorted(
        p for p in engine.state.alive_players
        if engine.state.roles.get(p) is role
    )


def events_of(engine: GameEngine, type_: EventType) -> list:
    return [e for e in engine.sink.events if e.type is type_]


def run_missions(engine: GameEngine, rounds: int, missions_per_round: int = 1) -> None:
    """Play `rounds` days of missions, the way MissionPhase does."""
    for _ in range(rounds):
        engine.start_round()
        engine.begin_phase(GamePhase.MISSION)
        for _ in range(missions_per_round):
            engine.start_mission()
            engine.complete_mission(success=True)


def award_sequence(seed: int) -> list[tuple[str, str]]:
    engine = make_engine(shield=True, dagger=True, seer=True, seed=seed)
    run_missions(engine, rounds=3)
    return [
        (e.actor, e.payload["item"]) for e in events_of(engine, EventType.ITEM_AWARDED)
    ]


# ----------------------------------------------------------------------
# Config flags
# ----------------------------------------------------------------------


def test_item_flags_are_off_by_default() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    assert config.game.shield is False
    assert config.game.dagger is False
    assert config.game.seer is False
    assert config.game.on_trial is False


def test_long_game_enables_all_four_and_basic_enables_none() -> None:
    long_game = load_config("configs/traitors/long_game.yaml")
    assert long_game.game.shield is True
    assert long_game.game.dagger is True
    assert long_game.game.seer is True
    assert long_game.game.on_trial is True

    basic = load_config("configs/traitors/basic.yaml")
    assert not (
        basic.game.shield
        or basic.game.dagger
        or basic.game.seer
        or basic.game.on_trial
    )


# ----------------------------------------------------------------------
# Item awards
# ----------------------------------------------------------------------


def test_award_cycles_shield_dagger_seer_one_per_round() -> None:
    engine = make_engine(shield=True, dagger=True, seer=True, seed=7)
    # Three missions in one round still award exactly one item.
    run_missions(engine, rounds=3, missions_per_round=3)

    awards = events_of(engine, EventType.ITEM_AWARDED)
    assert [e.payload["item"] for e in awards] == ["shield", "dagger", "seer"]
    assert [e.round for e in awards] == [1, 2, 3]
    for event in awards:
        assert event.actor in engine.state.alive_players
        assert engine.state.items[event.actor].count(event.payload["item"]) == 1


def test_award_skips_disabled_and_already_held_items() -> None:
    engine = make_engine(shield=True, seer=True, seed=7)  # dagger stays off
    run_missions(engine, rounds=3)
    items = [e.payload["item"] for e in events_of(engine, EventType.ITEM_AWARDED)]
    assert items == ["shield", "seer"]

    # Once every enabled item is out there, later rounds award nothing.
    engine = make_engine(shield=True, dagger=True, seer=True, seed=7)
    run_missions(engine, rounds=5)
    assert len(events_of(engine, EventType.ITEM_AWARDED)) == 3


def test_no_item_flags_means_no_award() -> None:
    engine = make_engine(seed=7)
    run_missions(engine, rounds=2)
    assert events_of(engine, EventType.ITEM_AWARDED) == []


def test_award_skipped_when_nobody_is_eligible() -> None:
    engine = make_engine(shield=True, seed=7)
    engine.state.alive_players.clear()  # nobody left to receive it
    run_missions(engine, rounds=1)
    assert events_of(engine, EventType.ITEM_AWARDED) == []
    assert engine.state.items == {}


def test_award_recipients_follow_the_seed() -> None:
    # Same seed, same recipients; a different seed draws differently.
    assert award_sequence(11) == award_sequence(11)
    assert award_sequence(1) != award_sequence(2)


# ----------------------------------------------------------------------
# Shield
# ----------------------------------------------------------------------


def test_shield_blocks_the_murder_and_is_spent() -> None:
    engine = make_engine(shield=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    victim = roles_of(engine, Role.FAITHFUL)[0]
    engine.state.items[victim] = ["shield"]

    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    assert engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitor, target=victim)
    ).ok
    assert engine.resolve_night() is None

    assert victim in engine.state.alive_players
    assert "shield" not in engine.state.items.get(victim, [])
    blocked = events_of(engine, EventType.SHIELD_BLOCKED)
    assert len(blocked) == 1
    assert blocked[0].actor == victim
    # The traitors' choice is spent: the kill was recorded once and
    # there is no second pick.
    assert len(events_of(engine, EventType.TRAITOR_KILL)) == 1
    assert events_of(engine, EventType.PLAYER_ELIMINATED) == []


def test_murder_lands_once_the_shield_is_gone() -> None:
    engine = make_engine(shield=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    victim = roles_of(engine, Role.FAITHFUL)[0]
    engine.state.items[victim] = ["shield"]

    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitor, target=victim)
    )
    engine.resolve_night()

    engine.start_round()
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitor, target=victim)
    )
    assert engine.resolve_night() == victim
    assert victim not in engine.state.alive_players


def test_transcript_shows_awards_and_blocked_murders() -> None:
    engine = make_engine(shield=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    victim = roles_of(engine, Role.FAITHFUL)[0]
    engine.state.items[victim] = ["shield"]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitor, target=victim)
    )
    engine.resolve_night()
    run_missions(engine, rounds=1)

    text = render_transcript(engine.sink.events)
    assert f"Shield blocked the murder: {victim} survives" in text
    assert "Item awarded:" in text


# ----------------------------------------------------------------------
# Dagger
# ----------------------------------------------------------------------


def test_dagger_doubles_the_first_vote_and_is_spent() -> None:
    db = Database()
    engine = make_engine(dagger=True, seed=42, db=db)
    holder, target = "alice", "david"
    engine.state.items[holder] = ["dagger"]
    engine.begin_phase(GamePhase.VOTING)

    assert engine.submit_action(
        Action(action=ActionType.VOTE, actor_id=holder, target=target)
    ).ok
    assert "dagger" not in engine.state.items.get(holder, [])
    used = events_of(engine, EventType.DAGGER_USED)
    assert len(used) == 1
    assert used[0].actor == holder

    assert engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="bob", target=target)
    ).ok
    weights = {
        e.actor: e.payload["weight"] for e in events_of(engine, EventType.VOTE_CAST)
    }
    assert weights == {holder: 2, "bob": 1}
    assert engine.tally_votes().counts == {target: 3}

    # Persistence carries the doubled weight, not just the tally.
    engine.begin_phase(GamePhase.ELIMINATION)
    engine.resolve_votes()
    stored = VoteRepository(db).get_round_weights(
        "game-001", engine.state.round_number
    )
    assert stored == {holder: 2, "bob": 1}


def test_dagger_weight_can_turn_the_tally_into_a_tie() -> None:
    engine = make_engine(dagger=True, seed=1)
    engine.state.items["alice"] = ["dagger"]
    engine.begin_phase(GamePhase.VOTING)
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="alice", target="david"))
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="bob", target="alice"))
    engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="charlie", target="alice")
    )

    tally = engine.tally_votes()
    assert tally.counts == {"david": 2, "alice": 2}
    assert tally.tie
    result = engine.resolve_votes()
    assert result.tie
    assert engine.state.alive_players == set(engine.player_ids)


def test_a_vote_without_a_dagger_counts_once() -> None:
    engine = make_engine(dagger=True, seed=1)
    engine.begin_phase(GamePhase.VOTING)
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="alice", target="bob"))
    weights = {
        e.actor: e.payload["weight"] for e in events_of(engine, EventType.VOTE_CAST)
    }
    assert weights == {"alice": 1}
    assert events_of(engine, EventType.DAGGER_USED) == []


# ----------------------------------------------------------------------
# Seer
# ----------------------------------------------------------------------


def test_seer_check_rules() -> None:
    engine = make_engine(seer=True)
    holder = "alice"
    engine.state.items[holder] = ["seer"]

    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    wrong_phase = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target="bob")
    )
    assert not wrong_phase.ok and "not allowed in phase" in wrong_phase.reason

    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    no_item = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id="bob", target="alice")
    )
    assert not no_item.ok and "seer item" in no_item.reason

    self_check = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target=holder)
    )
    assert not self_check.ok and "yourself" in self_check.reason

    engine.eliminate("charlie", method="vote")
    dead = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target="charlie")
    )
    assert not dead.ok and "not alive" in dead.reason

    assert engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target="bob")
    ).ok
    assert engine.seer_checks_done == {holder}
    again = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target="david")
    )
    assert not again.ok and "already used" in again.reason

    # Phase usage resets every phase, the seer limit does not.
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    next_phase = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target="david")
    )
    assert not next_phase.ok and "already used" in next_phase.reason


def test_seer_check_rejected_when_the_flag_is_off() -> None:
    engine = make_engine()
    engine.state.items["alice"] = ["seer"]
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    result = engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id="alice", target="bob")
    )
    assert not result.ok and "not allowed in phase" in result.reason


def test_seer_answer_is_visible_only_to_the_holder() -> None:
    engine = make_engine(seer=True)
    holder, target = "alice", "bob"
    engine.state.items[holder] = ["seer"]
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    assert engine.submit_action(
        Action(action=ActionType.SEER_CHECK, actor_id=holder, target=target)
    ).ok

    expected = f"{target} is a {engine.state.roles[target].value}"
    checks = events_of(engine, EventType.SEER_CHECK)
    assert len(checks) == 1
    assert checks[0].actor == holder
    assert checks[0].targets == [target]
    # The event records who asked whom, never the answer itself.
    assert checks[0].payload == {"holder": holder, "target": target}

    answers = [m for m in engine.router.messages if m.sender_id == "host"]
    assert len(answers) == 1
    assert answers[0].channel is Channel.ROLE_PRIVATE
    assert answers[0].recipients == [holder]
    assert answers[0].content == expected

    projector = InformationProjector(engine.router)
    holder_view = projector.project(engine.state, holder)
    assert [m.content for m in holder_view.private_conversations] == [expected]

    faithful = next(p for p in roles_of(engine, Role.FAITHFUL) if p != holder)
    faithful_view = projector.project(engine.state, faithful)
    assert expected not in faithful_view.render()
    assert expected not in faithful_view.model_dump_json()
    assert all(
        m.content != expected for m in faithful_view.private_conversations
    )


def test_item_holders_are_private() -> None:
    engine = make_engine(shield=True)
    engine.state.items["alice"] = ["shield"]
    projector = InformationProjector(engine.router)

    holder_view = projector.project(engine.state, "alice")
    assert holder_view.items == ["shield"]
    assert "Items: shield" in holder_view.render()

    outsider_view = projector.project(engine.state, "bob")
    assert outsider_view.items == []
    assert "Items:" not in outsider_view.render()
    assert "shield" not in outsider_view.render()


# ----------------------------------------------------------------------
# On trial: nominations and the murder shortlist
# ----------------------------------------------------------------------


def test_nominate_rejected_when_on_trial_is_off() -> None:
    engine = make_engine()
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful = roles_of(engine, Role.FAITHFUL)[0]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    result = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitor, target=faithful)
    )
    assert not result.ok and "not allowed in phase" in result.reason


def test_nominate_rules() -> None:
    engine = make_engine(on_trial=True)
    traitors = roles_of(engine, Role.TRAITOR)
    faithful = roles_of(engine, Role.FAITHFUL)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)

    by_faithful = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=faithful[0], target=faithful[1])
    )
    assert not by_faithful.ok and "only traitors" in by_faithful.reason

    self_nomination = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=traitors[0])
    )
    assert not self_nomination.ok and "yourself" in self_nomination.reason

    fellow = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=traitors[1])
    )
    assert not fellow.ok and "fellow traitor" in fellow.reason

    assert engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=faithful[0])
    ).ok
    second = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=faithful[1])
    )
    assert not second.ok and "limit" in second.reason

    engine.begin_phase(GamePhase.VOTING)
    wrong_phase = engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=faithful[1])
    )
    assert not wrong_phase.ok and "not allowed in phase" in wrong_phase.reason


def test_kill_must_target_the_shortlist_when_on_trial() -> None:
    engine = make_engine(on_trial=True)
    traitors = roles_of(engine, Role.TRAITOR)
    faithful = roles_of(engine, Role.FAITHFUL)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)

    # Nothing nominated yet: no kill is legal at all.
    early = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[0], target=faithful[0])
    )
    assert not early.ok and "shortlist" in early.reason

    assert engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=faithful[1])
    ).ok
    assert engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[1], target=faithful[0])
    ).ok
    shortlist = engine.resolve_nominations()
    assert shortlist == sorted([faithful[0], faithful[1]])

    listed = events_of(engine, EventType.MURDER_SHORTLIST)
    assert len(listed) == 1
    assert listed[0].targets == shortlist
    assert listed[0].payload["shortlist"] == shortlist
    assert listed[0].payload["nominations"] == {
        traitors[0]: faithful[1],
        traitors[1]: faithful[0],
    }

    # Legal targets shrink to the shortlist.
    offers = legal_targets(
        engine.state, traitors[0], ActionType.TRAITOR_KILL, shortlist=shortlist
    )
    assert offers == shortlist

    outside = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[0], target=faithful[2])
    )
    assert not outside.ok and "shortlist" in outside.reason
    inside = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[0], target=faithful[1])
    )
    assert inside.ok


def test_shortlist_clears_with_each_round() -> None:
    engine = make_engine(on_trial=True)
    traitors = roles_of(engine, Role.TRAITOR)
    faithful = roles_of(engine, Role.FAITHFUL)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    engine.submit_action(
        Action(action=ActionType.NOMINATE, actor_id=traitors[0], target=faithful[0])
    )
    engine.resolve_nominations()
    assert engine.murder_shortlist

    engine.start_round()
    assert engine.murder_shortlist == []


def test_night_phase_runs_nominations_before_the_kill() -> None:
    env = make_env(players=6, traitors=3, on_trial=True)
    engine = env.engine
    order: list[tuple[str, ActionType, list[str]]] = []

    async def callback(agent_id, action_type, targets):
        order.append((agent_id, action_type, list(targets)))
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=ActionType.TRAITOR_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} argues for tonight's kill",
            )
        return Action(action=action_type, actor_id=agent_id, target=targets[0])

    context = PhaseContext(engine=engine, config=env.config, request_action=callback)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    result = asyncio.run(env.phases()["traitor_night"].run(context))

    traitors = sorted(p for p, r in engine.state.roles.items() if r is Role.TRAITOR)
    asked = [action for _, action, _ in order]
    assert asked[:3] == [ActionType.TRAITOR_MESSAGE] * 3
    assert asked[3:6] == [ActionType.NOMINATE] * 3
    assert asked[6:] == [ActionType.TRAITOR_KILL] * 3

    shortlisted = [
        targets for _, action, targets in order if action is ActionType.NOMINATE
    ]
    shortlist = sorted({t[0] for t in shortlisted})
    assert engine.murder_shortlist == shortlist
    assert result["victim"] in shortlist
    kill_offers = [
        targets for _, action, targets in order if action is ActionType.TRAITOR_KILL
    ]
    assert kill_offers
    assert all(set(targets) <= set(shortlist) for targets in kill_offers)
    assert not [
        e for e in engine.sink.events if e.type is EventType.ACTION_REJECTED
    ]


def test_night_phase_without_on_trial_asks_no_nominations() -> None:
    env = make_env(players=6, traitors=3)
    engine = env.engine
    asked: list[ActionType] = []

    async def callback(agent_id, action_type, targets):
        asked.append(action_type)
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=ActionType.TRAITOR_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} plots",
            )
        return Action(action=action_type, actor_id=agent_id, target=targets[0])

    context = PhaseContext(engine=engine, config=env.config, request_action=callback)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    asyncio.run(env.phases()["traitor_night"].run(context))

    assert ActionType.NOMINATE not in asked
    assert engine.murder_shortlist == []


def test_private_chat_asks_the_seer_holder_once() -> None:
    env = make_env(players=6, traitors=2, seer=True)
    engine = env.engine
    holder = sorted(engine.state.alive_players)[0]
    engine.state.items[holder] = ["seer"]
    asked: list[tuple[str, ActionType]] = []

    async def callback(agent_id, action_type, targets):
        asked.append((agent_id, action_type))
        if action_type is ActionType.SEER_CHECK:
            return Action(
                action=ActionType.SEER_CHECK, actor_id=agent_id, target=targets[0]
            )
        return Action(
            action=ActionType.PRIVATE_MESSAGE,
            actor_id=agent_id,
            target=targets[0],
            content=f"{agent_id} whispers",
        )

    context = PhaseContext(engine=engine, config=env.config, request_action=callback)
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    asyncio.run(env.phases()["private_chat"].run(context))

    assert asked[0] == (holder, ActionType.SEER_CHECK)
    assert engine.seer_checks_done == {holder}
    # The seer check is asked once, up front; the private chat then runs its
    # opening wave and its reply wave, so the number of private asks is no
    # longer one per player. What matters here is that nothing is rejected.
    assert asked.count((holder, ActionType.SEER_CHECK)) == 1
    assert ActionType.PRIVATE_MESSAGE in {a for _, a in asked}
    assert not [
        e for e in engine.sink.events if e.type is EventType.ACTION_REJECTED
    ]

    # The one-shot check is never requested again in a later round.
    asked.clear()
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    asyncio.run(env.phases()["private_chat"].run(context))
    assert all(action is not ActionType.SEER_CHECK for _, action in asked)


# ----------------------------------------------------------------------
# Replay of items
# ----------------------------------------------------------------------


def test_replay_reconstructs_held_items() -> None:
    engine = make_engine(shield=True, dagger=True, seed=42)
    run_missions(engine, rounds=2)  # shield then dagger awarded

    dagger_holder = next(
        p for p, items in engine.state.items.items() if "dagger" in items
    )
    target = next(p for p in sorted(engine.state.alive_players) if p != dagger_holder)
    engine.begin_phase(GamePhase.VOTING)
    engine.submit_action(
        Action(action=ActionType.VOTE, actor_id=dagger_holder, target=target)
    )

    replay = ReplayState.from_events(engine.sink.events)
    assert replay.items == engine.state.items
    assert "dagger" not in replay.items.get(dagger_holder, [])
    for pid in engine.state.players:
        assert replay.project(pid).items == engine.state.items.get(pid, [])


# ----------------------------------------------------------------------
# One full fake game with all four flags on
# ----------------------------------------------------------------------


def test_full_fake_game_with_all_four_flags(tmp_path: Path) -> None:
    config = GameConfig(
        game={
            "players": 10,
            "traitors": 2,
            "max_rounds": 4,
            "shield": True,
            "dagger": True,
            "seer": True,
            "on_trial": True,
        },
        seed=5,
    )
    config.llm.provider = "fake"
    runner = GameRunner(
        config,
        runs_dir=tmp_path / "runs",
        db=Database(),
        personas_dir=Path("configs/personas"),
    )
    result = runner.run(game_id="game-001", seed=5)

    assert result.winner in ("faithful", "traitor")
    for name in ARTIFACTS:
        assert (result.run_dir / name).exists(), f"missing artifact {name}"

    types = [e.type for e in result.events]
    assert EventType.ACTION_REJECTED not in types
    awards = [e for e in result.events if e.type is EventType.ITEM_AWARDED]
    # The order advances only past items that are still held, so a
    # spent shield may come round again; every round awards exactly one.
    assert awards[0].payload["item"] == "shield"
    assert {"dagger", "seer"} <= {e.payload["item"] for e in awards}
    assert len(awards) == len({e.round for e in awards})
    assert EventType.MURDER_SHORTLIST in types
    assert EventType.DAGGER_USED in types
    assert EventType.SEER_CHECK in types

    # The host's seer line must not read as a player's speech, and
    # secrecy stays clean with the new events in the log.
    quality = result.metrics["quality"]
    assert quality["secrecy"]["flags_total"] == 0
    assert "host" not in quality["speech_similarity"]["per_player"]

    # Replay rebuilds the held items from events alone.
    saved = json.loads((result.run_dir / "game.json").read_text())
    replay = ReplayState.from_events(result.events)
    assert replay.items == saved["items"]

    assert "Item awarded:" in render_transcript(result.events)
