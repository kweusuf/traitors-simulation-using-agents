"""Unit tests for the game engine (spec section 5, 6, 8, 29)."""

from __future__ import annotations

import pytest

from simulation.actions.actions import Action, ActionType
from simulation.engine.game_engine import GameEngine
from simulation.engine.state import GamePhase, Role
from simulation.engine.rules import RuleValidator
from simulation.experiments.config import GameConfig, load_config
from simulation.persistence.event_log import EventType
from simulation.persistence.sink import EventSink


def make_engine(game_id: str = "game-001", seed: int = 42, **game_overrides) -> GameEngine:
    config = GameConfig(game={"players": 6, "traitors": 2, **game_overrides}, seed=seed)
    engine = GameEngine(config, EventSink(game_id), seed=seed)
    engine.start()
    return engine


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_load_basic_config() -> None:
    config = load_config("configs/traitors/basic.yaml")
    assert config.game.players == 6
    assert config.game.traitors == 2
    assert config.game.faithful == 4
    assert config.game.max_rounds == 5
    assert config.phases[0] == "mission"
    assert config.llm.provider == "ollama"
    assert config.llm.max_concurrency == 2


def test_config_rejects_bad_phase() -> None:
    with pytest.raises(Exception, match="unknown phases"):
        GameConfig(phases=["mission", "dance_party"])


def test_config_rejects_impossible_role_counts() -> None:
    with pytest.raises(Exception):
        GameConfig(game={"players": 4, "traitors": 4})


# ----------------------------------------------------------------------
# Role assignment
# ----------------------------------------------------------------------


def test_role_assignment_counts_and_determinism() -> None:
    a = make_engine(seed=42)
    b = make_engine(seed=42)
    c = make_engine(seed=7)
    assert len(a.state.roles) == 6
    assert sum(1 for r in a.state.roles.values() if r is Role.TRAITOR) == 2
    assert a.state.roles == b.state.roles
    assert a.state.roles != c.state.roles
    # GAME_STARTED precedes ROLE_ASSIGNED events.
    types = [e.type for e in a.sink.events[:3]]
    assert types[0] is EventType.GAME_STARTED
    assert types[1] is EventType.ROLE_ASSIGNED


def test_engine_refuses_double_start() -> None:
    engine = make_engine()
    with pytest.raises(RuntimeError, match="already started"):
        engine.start()


# ----------------------------------------------------------------------
# Action validation (spec section 8, 29)
# ----------------------------------------------------------------------


def test_vote_only_allowed_in_voting_phase() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    result = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="bob")
    )
    assert not result.ok
    assert "not allowed" in result.reason


def test_dead_agent_cannot_act() -> None:
    engine = make_engine()
    engine.eliminate("alice", method="vote")
    engine.begin_phase(GamePhase.VOTING)
    result = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="bob")
    )
    assert not result.ok
    assert "not alive" in result.reason


def test_self_vote_rejected_by_default() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.VOTING)
    result = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="alice")
    )
    assert not result.ok
    assert "self-vote" in result.reason


def test_only_traitors_may_kill() -> None:
    engine = make_engine()
    faithful = next(p for p, r in engine.state.roles.items() if r is Role.FAITHFUL)
    traitor = next(p for p, r in engine.state.roles.items() if r is Role.TRAITOR)
    victim = next(p for p in engine.player_ids if p not in (faithful, traitor))
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)

    bad = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=faithful, target=victim)
    )
    assert not bad.ok
    assert "traitors" in bad.reason

    good = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitor, target=victim)
    )
    assert good.ok


def test_traitor_cannot_kill_fellow_traitor() -> None:
    engine = make_engine()
    traitors = [p for p, r in engine.state.roles.items() if r is Role.TRAITOR]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    result = engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[0], target=traitors[1])
    )
    assert not result.ok
    assert "fellow traitor" in result.reason


def test_message_limits_enforced() -> None:
    engine = make_engine()  # public: 1, private: 2 per agent per phase
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    first = engine.submit_action(
        Action(
            action=ActionType.PUBLIC_MESSAGE,
            actor_id="alice",
            content="hello",
        )
    )
    assert first.ok
    second = engine.submit_action(
        Action(
            action=ActionType.PUBLIC_MESSAGE,
            actor_id="alice",
            content="again",
        )
    )
    assert not second.ok
    assert "limit" in second.reason


def test_private_message_requires_alive_target() -> None:
    engine = make_engine()
    engine.eliminate("bob", method="vote")
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    result = engine.submit_action(
        Action(
            action=ActionType.PRIVATE_MESSAGE,
            actor_id="alice",
            target="bob",
            content="psst",
        )
    )
    assert not result.ok
    assert "not alive" in result.reason


def test_rejected_action_emits_event() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.VOTING)
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="alice", target="alice"))
    rejected = [e for e in engine.sink.events if e.type is EventType.ACTION_REJECTED]
    assert len(rejected) == 1
    assert rejected[0].actor == "alice"
    assert "self-vote" in rejected[0].payload["reason"]


def test_vote_recorded_and_counted() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.VOTING)
    for voter in ["alice", "bob", "charlie"]:
        assert engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target="david")
        ).ok
    assert engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="david", target="alice")
    ).ok
    # One vote per agent per phase.
    again = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="david")
    )
    assert not again.ok

    tally = engine.tally_votes()
    assert tally.counts == {"david": 3, "alice": 1}
    assert tally.top == ["david"]
    assert not tally.tie


# ----------------------------------------------------------------------
# Elimination and win conditions (spec section 29)
# ----------------------------------------------------------------------


def test_resolve_votes_eliminates_majority_target() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.VOTING)
    for voter in ["alice", "bob", "charlie", "eve"]:
        engine.submit_action(Action(action=ActionType.VOTE, actor_id=voter, target="david"))
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="david", target="alice"))
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="frank", target="alice"))

    tally = engine.resolve_votes()
    assert not tally.tie
    assert "david" not in engine.state.alive_players
    assert "david" in engine.state.eliminated_players
    assert engine.state.players["david"].alive is False


def test_vote_tie_means_no_elimination() -> None:
    engine = make_engine()
    engine.begin_phase(GamePhase.VOTING)
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="alice", target="bob"))
    engine.submit_action(Action(action=ActionType.VOTE, actor_id="bob", target="alice"))
    tally = engine.resolve_votes()
    assert tally.tie
    assert engine.state.alive_players == set(engine.player_ids)
    ties = [e for e in engine.sink.events if e.type is EventType.VOTE_TIE]
    assert len(ties) == 1


def test_night_kill_majority_and_tiebreak() -> None:
    engine = make_engine()
    traitors = [p for p, r in engine.state.roles.items() if r is Role.TRAITOR]
    faithful = [p for p in engine.player_ids if engine.state.roles[p] is Role.FAITHFUL]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[0], target=faithful[0])
    )
    engine.submit_action(
        Action(action=ActionType.TRAITOR_KILL, actor_id=traitors[1], target=faithful[1])
    )
    # Tie: earliest submitted choice (traitors[0]'s target) wins.
    victim = engine.resolve_night()
    assert victim == faithful[0]
    assert victim not in engine.state.alive_players


def test_faithful_win_when_all_traitors_eliminated() -> None:
    engine = make_engine()
    traitors = [p for p, r in engine.state.roles.items() if r is Role.TRAITOR]
    engine.eliminate(traitors[0], method="vote")
    assert not engine.is_over
    engine.eliminate(traitors[1], method="vote")
    assert engine.is_over
    assert engine.state.winning_team is Role.FAITHFUL
    assert engine.state.winner == "faithful"
    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won[-1].payload["reason"] == "elimination"


def test_traitor_win_at_parity() -> None:
    engine = make_engine()
    traitors = {p for p, r in engine.state.roles.items() if r is Role.TRAITOR}
    faithful = [p for p in engine.player_ids if p not in traitors]
    # Eliminate three faithful: 2 traitors vs 3 faithful -> no win.
    for p in faithful[:3]:
        if engine.is_over:
            break
        engine.eliminate(p, method="vote")
    # 2 vs 2 remaining faithful means one more elimination ends it.
    if not engine.is_over:
        engine.eliminate(faithful[3], method="night")
    assert engine.is_over
    assert engine.state.winning_team is Role.TRAITOR


def test_round_limit_winner_applied() -> None:
    engine = make_engine(round_limit_winner="traitor")
    engine.apply_round_limit()
    assert engine.state.winning_team is Role.TRAITOR
    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won[-1].payload["reason"] == "round_limit"


def test_eliminating_dead_player_raises() -> None:
    engine = make_engine()
    engine.eliminate("alice", method="vote")
    with pytest.raises(ValueError, match="not alive"):
        engine.eliminate("alice", method="vote")


def test_mission_lifecycle() -> None:
    engine = make_engine()
    engine.start_mission()
    engine.complete_mission(success=True)
    assert engine.state.missions[0].completed
    assert engine.state.missions[0].outcome is True
    with pytest.raises(ValueError, match="no mission"):
        engine.complete_mission(success=False)


def test_validator_standalone_usage_limits() -> None:
    config = GameConfig()
    validator = RuleValidator(config)
    engine = make_engine()
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    action = Action(action=ActionType.PUBLIC_MESSAGE, actor_id="alice", content="hi")
    assert validator.validate(action, engine.state, {}).ok
    over = validator.validate(action, engine.state, {("alice", ActionType.PUBLIC_MESSAGE): 1})
    assert not over.ok
