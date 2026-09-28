"""Finale: normal play stops at 3 traitors and 3 faithful, or at the
configured living count, then rapid-fire voting decides. A configured
endgame replaces the parity win, so extinction is the only elimination
victory left. Also covers the seeded solo/team ambitions and the
individual outcomes they drive."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from simulation.actions.actions import Action, ActionType
from simulation.agents.goals import Goals, inject_role_goals
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.replay import ReplayState, build_transcript, render_transcript
from simulation.experiments.runner import GameRunner
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.sink import EventSink


def make_engine(seed: int = 42, **game_overrides) -> GameEngine:
    config = GameConfig(game=game_overrides, seed=seed)
    engine = GameEngine(config, EventSink("game-001"), seed=seed)
    engine.start()
    return engine


def alive_roles(engine: GameEngine, role: Role) -> list[str]:
    return sorted(
        p for p in engine.state.alive_players if engine.state.roles.get(p) is role
    )


def banish(engine: GameEngine, target: str) -> None:
    """Everyone except `target` votes them out; the tally resolves."""
    engine.state.votes.clear()
    engine.begin_phase(GamePhase.VOTING)
    for voter in sorted(engine.state.alive_players):
        if voter == target:
            continue
        result = engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target=target)
        )
        assert result.ok, result.reason
    engine.begin_phase(GamePhase.ELIMINATION)
    engine.resolve_votes()


def at_finale(seed: int = 42, **overrides) -> GameEngine:
    """An engine already sitting at the finale split (3 traitors, 3 faithful)."""
    game = {
        "players": 8,
        "traitors": 3,
        "finale_traitors": 3,
        "finale_faithful": 3,
        "max_rounds": 5,
        **overrides,
    }
    engine = make_engine(seed=seed, **game)
    faithful = alive_roles(engine, Role.FAITHFUL)
    assert len(faithful) == 5
    for pid in faithful[:2]:
        engine.eliminate(pid, method="night")
    return engine


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_finale_is_off_by_default() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    assert config.game.finale_traitors == 0
    assert config.game.finale_faithful == 0
    assert config.game.finale_max_votes == 10


def test_long_game_stops_at_three_against_three() -> None:
    config = load_config("configs/traitors/long_game.yaml")
    assert config.game.finale_traitors == 3
    assert config.game.finale_faithful == 3
    assert config.game.finale_max_votes == 10


def test_finale_counts_must_be_set_together() -> None:
    with pytest.raises(Exception, match="finale_traitors and finale_faithful"):
        GameConfig(game={"players": 21, "traitors": 3, "finale_traitors": 3})
    with pytest.raises(Exception, match="finale_traitors must be fewer than players"):
        GameConfig(
            game={
                "players": 6,
                "traitors": 3,
                "finale_traitors": 6,
                "finale_faithful": 3,
            }
        )


# ----------------------------------------------------------------------
# Trigger
# ----------------------------------------------------------------------


def test_finale_starts_when_the_split_is_reached() -> None:
    engine = at_finale()
    assert engine.state.finale is True
    assert engine.state.winner is None  # parity is suppressed at 3v3

    started = [e for e in engine.sink.events if e.type is EventType.FINALE_STARTED]
    assert len(started) == 1
    assert sorted(started[0].payload["traitors"]) == alive_roles(engine, Role.TRAITOR)
    assert len(started[0].payload["faithful"]) == 3


def test_no_finale_config_keeps_the_parity_win() -> None:
    # 3 traitors against 3 faithful with no finale configured: normal
    # play continues, and the first banishment decides by parity.
    engine = make_engine(players=6, traitors=3, max_rounds=5)
    assert engine.state.finale is False
    assert engine.state.winner is None

    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])
    assert engine.state.winner == "traitor"
    assert engine.state.finale is False
    assert not [e for e in engine.sink.events if e.type is EventType.FINALE_STARTED]


def test_no_recruitment_once_the_finale_runs() -> None:
    engine = at_finale(recruit_on_banish=True)
    traitor = alive_roles(engine, Role.TRAITOR)[0]
    assert engine.recruitment_opportunity(traitor) is False

    # Before the split the offer still stands.
    early = make_engine(
        players=8,
        traitors=3,
        recruit_on_banish=True,
        finale_traitors=3,
        finale_faithful=3,
        max_rounds=5,
    )
    early.eliminate(alive_roles(early, Role.FAITHFUL)[0], method="night")
    assert early.state.finale is False
    assert early.recruitment_opportunity(alive_roles(early, Role.TRAITOR)[0]) is True


# ----------------------------------------------------------------------
# Outcomes from the finale
# ----------------------------------------------------------------------


def won_payload(engine: GameEngine) -> dict:
    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won, "game did not produce a winner"
    return won[-1].payload


def test_finale_can_end_with_a_traitor_team_win() -> None:
    engine = at_finale()
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])

    assert engine.state.winner == "traitor"
    payload = won_payload(engine)
    assert payload["finale"] is True
    assert payload["solo"] is False
    assert len(payload["surviving_traitors"]) == 3
    assert payload["reason"] == "elimination"


def test_finale_can_end_with_a_solo_traitor_win() -> None:
    engine = at_finale()
    # Two rival traitors go (the faithful did the work), then two
    # faithful, leaving one traitor against one faithful: parity, solo.
    for _ in range(2):
        banish(engine, alive_roles(engine, Role.TRAITOR)[0])
        assert engine.state.winner is None
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])
    assert engine.state.winner is None
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])

    assert engine.state.winner == "traitor"
    payload = won_payload(engine)
    assert payload["solo"] is True
    assert len(payload["surviving_traitors"]) == 1


def test_finale_can_end_with_a_faithful_win() -> None:
    engine = at_finale()
    for _ in range(3):
        banish(engine, alive_roles(engine, Role.TRAITOR)[0])

    assert engine.state.winner == "faithful"
    payload = won_payload(engine)
    assert payload["finale"] is True
    assert payload["surviving_traitors"] == []


# ----------------------------------------------------------------------
# Phase engine: rapid fire rounds
# ----------------------------------------------------------------------


def tie_callback():
    """Split the six players two/three against three so every vote ties."""

    async def callback(agent_id: str, action_type: ActionType, targets: list[str]):
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} argues their case",
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            target = next(t for t in targets if t != agent_id)
            return Action(
                action=ActionType.PRIVATE_MESSAGE,
                actor_id=agent_id,
                target=target,
                content=f"{agent_id} whispers a plan",
            )
        if action_type is ActionType.VOTE:
            alive = sorted([*targets, agent_id])  # targets exclude the voter
            first_half, second_half = alive[: len(alive) // 2], alive[len(alive) // 2 :]
            pick = second_half[-1] if agent_id in first_half else first_half[0]
            assert pick != agent_id
            return Action(action=ActionType.VOTE, actor_id=agent_id, target=pick)
        raise AssertionError(f"unexpected action type {action_type}")

    return callback


def test_rapid_fire_rounds_tie_until_the_fallback_declares_a_winner() -> None:
    config = GameConfig(
        game={
            "players": 6,
            "traitors": 3,
            "finale_traitors": 3,
            "finale_faithful": 3,
            "finale_max_votes": 3,
            "max_rounds": 5,
        },
        seed=7,
    )
    env = TraitorsEnvironment(config, EventSink("game-001"), seed=7)
    phase_engine = PhaseEngine(config, env.engine, env.phases())
    context = PhaseContext(engine=env.engine, config=config, request_action=tie_callback())
    asyncio.run(phase_engine.run(context))

    engine = env.engine
    assert engine.state.finale is True
    assert engine.state.round_number == 3  # three tied rapid-fire rounds
    assert engine.state.winner == "faithful"  # round_limit_winner fallback

    types = [e.type for e in engine.sink.events]
    assert EventType.FINALE_STARTED in types
    assert EventType.MISSION_STARTED not in types
    assert EventType.TRAITOR_KILL not in types
    assert len(engine.state.alive_players) == 6  # nobody was banished
    votes = [e for e in engine.sink.events if e.type is EventType.VOTE_CAST]
    assert len(votes) == 6 * 3  # six players, three rapid-fire rounds

    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won[-1].payload["reason"] == "finale_vote_limit"
    assert engine.state.phase is GamePhase.GAME_END


def test_rapid_fire_without_ties_bans_someone_each_round() -> None:
    config = GameConfig(
        game={
            "players": 6,
            "traitors": 3,
            "finale_traitors": 3,
            "finale_faithful": 3,
            "finale_max_votes": 10,
            "max_rounds": 5,
        },
        seed=3,
    )
    env = TraitorsEnvironment(config, EventSink("game-001"), seed=3)

    # Everyone piles onto the alphabetically first other player, so
    # every round banishes somebody and the finale cannot stall.
    async def pile_on(agent_id: str, action_type: ActionType, targets: list[str]):
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} makes their case",
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            target = next(t for t in targets if t != agent_id)
            return Action(
                action=ActionType.PRIVATE_MESSAGE,
                actor_id=agent_id,
                target=target,
                content=f"{agent_id} whispers",
            )
        pick = next(t for t in targets if t != agent_id)
        return Action(action=ActionType.VOTE, actor_id=agent_id, target=pick)

    phase_engine = PhaseEngine(config, env.engine, env.phases())
    context = PhaseContext(engine=env.engine, config=config, request_action=pile_on)
    asyncio.run(phase_engine.run(context))

    engine = env.engine
    assert engine.state.finale is True
    assert engine.state.winner in ("faithful", "traitor")
    assert len(engine.state.eliminated_players) >= 1
    assert not [e for e in engine.sink.events if e.type is EventType.MISSION_STARTED]


# ----------------------------------------------------------------------
# Ambitions and prompts
# ----------------------------------------------------------------------


def test_ambitions_are_seeded_and_deterministic() -> None:
    first = make_engine(players=8, traitors=3, seed=42)
    second = make_engine(players=8, traitors=3, seed=42)
    other = make_engine(players=8, traitors=3, seed=99)

    assert first.state.ambitions == second.state.ambitions
    assert set(first.state.ambitions.values()) <= {"solo", "team"}
    assert set(first.state.ambitions) == set(first.state.players)
    assert first.state.ambitions != other.state.ambitions


def test_traitor_prompt_offers_both_alliances() -> None:
    goals = inject_role_goals(Goals(), Role.TRAITOR, ambition="solo")
    text = PromptBuilder().build_system("alice", Role.TRAITOR, Persona(), goals)
    assert "switch sides" in text
    assert "win alone" in text
    assert "win as a team" in text
    assert goals.ambition and "last traitor standing" in goals.ambition
    assert "Ambition:" in text

    faithful_goals = inject_role_goals(Goals(), Role.FAITHFUL)
    faithful_text = PromptBuilder().build_system("bob", Role.FAITHFUL, Persona(), faithful_goals)
    assert "switch sides" not in faithful_text
    assert faithful_goals.ambition is None


# ----------------------------------------------------------------------
# Artifacts: metrics, replay, transcript
# ----------------------------------------------------------------------


def test_metrics_report_finale_flag_outcomes_and_ambitions(tmp_path: Path) -> None:
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    runner = GameRunner(
        config, runs_dir=tmp_path, db=Database(), personas_dir=Path("configs/personas")
    )
    result = runner.run(game_id="game-001", seed=42)
    metrics = result.metrics

    assert metrics["finale"] is False
    assert metrics["solo_traitor_win"] == (
        metrics["winner"] == "traitor"
        and len(metrics["surviving_traitors"]) == 1
    )
    assert set(metrics["ambitions"]) == set(metrics["outcomes"])
    assert set(metrics["outcomes"].values()) <= {"won", "lost"}
    winners = {p for p, outcome in metrics["outcomes"].items() if outcome == "won"}
    roles = {
        e.actor: e.payload["role"]
        for e in result.events
        if e.type is EventType.ROLE_ASSIGNED
    }
    expected = {
        pid
        for pid, role in roles.items()
        if (
            (metrics["winner"] == "faithful" and role == "faithful")
            or (
                metrics["winner"] == "traitor"
                and role == "traitor"
                and pid in metrics["surviving_traitors"]
            )
        )
    }
    assert winners == expected


def test_replay_and_transcript_pick_up_the_finale() -> None:
    events = [
        Event(
            event_id="e1",
            game_id="game-001",
            sequence=1,
            payload={"players": ["alice", "bob"]},
            type=EventType.GAME_STARTED,
        ),
        Event(
            event_id="e2",
            game_id="game-001",
            sequence=2,
            type=EventType.ROLE_ASSIGNED,
            actor="alice",
            payload={"role": "traitor"},
        ),
        Event(
            event_id="e3",
            game_id="game-001",
            sequence=3,
            type=EventType.ROLE_ASSIGNED,
            actor="bob",
            payload={"role": "faithful"},
        ),
        Event(
            event_id="e4",
            game_id="game-001",
            sequence=4,
            round=1,
            type=EventType.FINALE_STARTED,
            payload={"traitors": ["alice"], "faithful": ["bob"]},
        ),
        Event(
            event_id="e5",
            game_id="game-001",
            sequence=5,
            type=EventType.GAME_WON,
            payload={
                "team": "traitor",
                "reason": "elimination",
                "finale": True,
                "surviving_traitors": ["alice"],
                "solo": True,
            },
        ),
    ]

    state = ReplayState.from_events(events)
    assert state.finale is True
    assert state.winner == "traitor"

    transcript = build_transcript(events)
    assert transcript["finale"] is True
    assert transcript["solo_traitor_win"] is True
    assert "Finale: rapid fire voting (1 traitors, 1 faithful)" in render_transcript(
        events
    )


def test_finale_can_trigger_on_a_total_count() -> None:
    # Four faithful against one traitor is the shape the faction pair
    # rule cannot express, but the show's final five does.
    engine = make_engine(
        players=6, traitors=1, finale_total=5, max_rounds=5
    )
    assert engine.state.finale is False
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="night")
    assert engine.state.finale is True
    started = [e for e in engine.sink.events if e.type is EventType.FINALE_STARTED]
    assert len(started) == 1
    assert len(started[0].payload["traitors"]) == 1
    assert len(started[0].payload["faithful"]) == 4


def test_total_trigger_fires_at_or_below_the_count() -> None:
    engine = make_engine(players=6, traitors=1, finale_total=4, max_rounds=5)
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="night")
    assert engine.state.finale is False  # five left, above the count
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="night")
    assert engine.state.finale is True


def test_total_trigger_fires_when_the_cast_is_below_the_count() -> None:
    # A trigger the cast can never land on exactly (the count is above
    # every possible living total) opens the endgame at game start,
    # rather than leaving the game to be decided by parity or the round
    # limit.
    engine = make_engine(players=6, traitors=1, finale_total=8, max_rounds=5)
    assert engine.state.finale is True


# ----------------------------------------------------------------------
# A configured endgame replaces parity
# ----------------------------------------------------------------------


def test_parity_does_not_end_a_game_with_a_configured_endgame() -> None:
    # Three against three is a traitor win under parity, which is what
    # ended the season run at six alive, one short of the final five the
    # real season reached.
    engine = make_engine(
        players=6, traitors=3, finale_total=5, max_rounds=8
    )
    engine.check_win()
    assert engine.state.winner is None  # parity declined at three-three
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="vote")
    assert engine.state.winner is None  # still declined, five remain
    assert engine.state.finale is True


def test_extinction_still_ends_a_game_with_a_configured_endgame() -> None:
    # The endgame replaces parity, not extinction: losing the last
    # traitor still hands the game to the faithful.
    engine = make_engine(players=4, traitors=1, finale_total=2, max_rounds=5)
    engine.eliminate(alive_roles(engine, Role.TRAITOR)[0], method="vote")
    assert engine.state.winner == "faithful"
    assert engine.state.finale is False


def test_parity_still_applies_without_a_configured_endgame() -> None:
    engine = make_engine(players=5, traitors=2, max_rounds=5)
    assert engine.state.winner is None  # two against three is not parity
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="vote")
    assert engine.state.winner == "traitor"  # two against two is


def test_endgame_vote_alone_also_suppresses_parity() -> None:
    # The end vote flag on its own is enough: the show's endgame is
    # configured, so an equal split is played out rather than called.
    engine = make_engine(
        players=5, traitors=2, endgame_vote=True, max_rounds=5
    )
    engine.eliminate(alive_roles(engine, Role.FAITHFUL)[0], method="night")
    assert engine.state.winner is None  # two against two, not called
    assert engine.state.finale is False  # no finale count configured

    # The same cast without the flag is a plain parity win.
    plain = make_engine(players=5, traitors=2, max_rounds=5)
    plain.eliminate(alive_roles(plain, Role.FAITHFUL)[0], method="night")
    assert plain.state.winner == "traitor"


def test_a_fake_season_shaped_game_reaches_the_final_five(
    tmp_path: Path,
) -> None:
    """The season shape end to end, on the fake backend.

    Three traitors reach parity at six alive, the configured endgame
    keeps the game going to the final five, and the end-or-banish loop
    decides it. Seed 3 is the regression case: under the plain parity
    rule the same run was declared a traitor win at six alive, with no
    finale at all, which is how the season replay (game-009) ended
    early.
    """
    config = GameConfig(
        game={
            "players": 8,
            "traitors": 3,
            "finale_total": 5,
            "endgame_vote": True,
            "blind_finale_banishments": True,
            "max_rounds": 10,
        },
        seed=3,
    )
    config.llm.provider = "fake"
    result = GameRunner(
        config,
        runs_dir=tmp_path,
        db=Database(),
        personas_dir=Path("configs/personas"),
    ).run(game_id="game-001", seed=3)
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    started = [e for e in events if e["type"] == "FINALE_STARTED"]
    assert len(started) == 1
    opening = started[0]["payload"]
    # Five alive, and the split is already at parity strength: the sort
    # of board the old rule closed before the finale could open.
    assert len(opening["traitors"]) == 3
    assert len(opening["faithful"]) == 2
    # Exactly the three eliminations that took eight players down to the
    # configured five happened first.
    assert (
        len(
            [
                e
                for e in events
                if e["type"] == "PLAYER_ELIMINATED"
                and e["sequence"] < started[0]["sequence"]
            ]
        )
        == 3
    )

    won = [e for e in events if e["type"] == "GAME_WON"]
    assert len(won) == 1
    assert won[0]["payload"]["reason"] == "endgame"
    assert won[0]["payload"]["finale"] is True
    # The end vote is what finished it, not an elimination victory.
    assert any(e["type"] == "END_VOTE_CAST" for e in events)
    assert result.metrics["status"] == "completed"
    assert result.metrics["finale"] is True
