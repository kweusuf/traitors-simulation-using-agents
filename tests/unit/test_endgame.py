"""Endgame end-or-banish vote with the blind finale (phase 25).

The finale loop, the END_VOTE action, the two-player auto-end, and the
hidden roles of finale-time banishments.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from simulation.actions.actions import Action, ActionType
from simulation.communication.visibility import InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.runner import GameRunner
from simulation.models.base import ChatMessage
from simulation.models.fake import PromptScriptProvider
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
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
    """An engine sitting at the finale split (3 traitors, 3 faithful)."""
    game = {
        "players": 8,
        "traitors": 3,
        "finale_traitors": 3,
        "finale_faithful": 3,
        "max_rounds": 5,
        **overrides,
    }
    engine = make_engine(seed=seed, **game)
    for pid in alive_roles(engine, Role.FAITHFUL)[:2]:
        engine.eliminate(pid, method="night")
    assert engine.state.finale is True
    return engine


def cast_end_votes(engine: GameEngine, choice: str) -> bool:
    """Every living player answers `choice`; resolve the vote."""
    engine.begin_phase(GamePhase.END_VOTE)
    for pid in sorted(engine.state.alive_players):
        result = engine.submit_action(
            Action(action=ActionType.END_VOTE, actor_id=pid, content=choice)
        )
        assert result.ok, result.reason
    return engine.resolve_end_vote()


def won_payload(engine: GameEngine) -> dict:
    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won, "game did not produce a winner"
    return won[-1].payload


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_endgame_flags_default_off() -> None:
    config = GameConfig()
    assert config.game.endgame_vote is False
    assert config.game.blind_finale_banishments is False


def test_show_configs_enable_the_endgame() -> None:
    for path in (
        "configs/traitors/season_uk_s01.yaml",
        "configs/traitors/long_game.yaml",
    ):
        config = load_config(path)
        assert config.game.endgame_vote is True, path
        assert config.game.blind_finale_banishments is True, path


def test_end_vote_is_not_a_configurable_phase() -> None:
    with pytest.raises(Exception, match="unknown phases"):
        GameConfig(phases=["mission", "voting", "end_vote"])


# ----------------------------------------------------------------------
# The action
# ----------------------------------------------------------------------


def test_end_vote_requires_content() -> None:
    with pytest.raises(ValidationError):
        Action(action=ActionType.END_VOTE, actor_id="alice")


def test_end_vote_rejects_other_content() -> None:
    engine = at_finale(endgame_vote=True)
    engine.begin_phase(GamePhase.END_VOTE)
    actor = sorted(engine.state.alive_players)[0]

    result = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="maybe")
    )
    assert not result.ok
    assert "'end' or 'banish'" in result.reason


def test_end_vote_content_is_trimmed_and_case_insensitive() -> None:
    engine = at_finale(endgame_vote=True)
    engine.begin_phase(GamePhase.END_VOTE)
    actor = sorted(engine.state.alive_players)[0]

    result = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="  BANISH ")
    )
    assert result.ok, result.reason
    assert engine.end_vote_choices()[actor] == "banish"


def test_end_vote_is_rejected_outside_the_finale() -> None:
    # Flag on, but the finale has not started (2 traitors vs 4 faithful).
    engine = make_engine(
        players=6,
        traitors=2,
        finale_traitors=2,
        finale_faithful=3,
        endgame_vote=True,
    )
    assert engine.state.finale is False
    engine.begin_phase(GamePhase.END_VOTE)
    actor = sorted(engine.state.alive_players)[0]

    result = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="end")
    )
    assert not result.ok
    assert "finale" in result.reason


def test_end_vote_is_rejected_when_the_flag_is_off() -> None:
    engine = at_finale()  # finale running, endgame_vote off
    engine.begin_phase(GamePhase.END_VOTE)
    actor = sorted(engine.state.alive_players)[0]

    result = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="end")
    )
    assert not result.ok
    assert "disabled" in result.reason


def test_one_end_vote_per_player_per_phase() -> None:
    engine = at_finale(endgame_vote=True)
    engine.begin_phase(GamePhase.END_VOTE)
    actor = sorted(engine.state.alive_players)[0]

    first = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="end")
    )
    assert first.ok
    second = engine.submit_action(
        Action(action=ActionType.END_VOTE, actor_id=actor, content="banish")
    )
    assert not second.ok
    assert "limit" in second.reason


# ----------------------------------------------------------------------
# Outcomes
# ----------------------------------------------------------------------


def test_unanimous_end_hands_the_win_to_the_traitors() -> None:
    engine = at_finale(endgame_vote=True)
    assert cast_end_votes(engine, "end") is True

    assert engine.state.winner == "traitor"
    payload = won_payload(engine)
    assert payload["reason"] == "endgame"
    assert payload["finale"] is True
    assert payload["solo"] is False
    assert len(payload["surviving_traitors"]) == 3


def test_unanimous_end_with_one_traitor_left_is_a_solo_win() -> None:
    engine = at_finale(endgame_vote=True)
    for _ in range(2):
        banish(engine, alive_roles(engine, Role.TRAITOR)[0])
        assert engine.state.winner is None  # parity does not end it

    assert len(alive_roles(engine, Role.TRAITOR)) == 1
    assert cast_end_votes(engine, "end") is True
    payload = won_payload(engine)
    assert engine.state.winner == "traitor"
    assert payload["solo"] is True
    assert payload["reason"] == "endgame"


def test_unanimous_end_with_no_traitor_hands_it_to_the_faithful() -> None:
    engine = at_finale(endgame_vote=True)
    # Rewrite the roles so no traitor remains: this isolates the ending
    # rule from the extinction win that normal eliminations would fire.
    for pid in list(engine.state.roles):
        engine.state.roles[pid] = Role.FAITHFUL

    assert cast_end_votes(engine, "end") is True
    assert engine.state.winner == "faithful"
    payload = won_payload(engine)
    assert payload["reason"] == "endgame"
    assert payload["surviving_traitors"] == []


def test_extinction_still_ends_an_endgame_finale() -> None:
    engine = at_finale(endgame_vote=True)
    for _ in range(3):
        banish(engine, alive_roles(engine, Role.TRAITOR)[0])

    assert engine.state.winner == "faithful"
    payload = won_payload(engine)
    assert payload["reason"] == "endgame"
    assert engine.state.finale is True


def test_parity_no_longer_ends_an_endgame_finale() -> None:
    engine = at_finale(endgame_vote=True)
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])
    assert engine.state.winner is None  # 3 traitors against 2 faithful
    assert engine.state.finale is True

    # Contrast: the same banishment ends a plain finale by parity.
    plain = at_finale()
    banish(plain, alive_roles(plain, Role.FAITHFUL)[0])
    assert plain.state.winner == "traitor"


def test_the_endgame_auto_ends_when_two_players_remain() -> None:
    engine = at_finale(endgame_vote=True)
    for _ in range(2):
        banish(engine, alive_roles(engine, Role.TRAITOR)[0])  # 1 traitor, 3 faithful
    assert engine.state.winner is None
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])  # 1 traitor, 2 faithful
    assert engine.state.winner is None
    banish(engine, alive_roles(engine, Role.FAITHFUL)[0])  # 1 and 1: auto-end

    assert len(engine.state.alive_players) == 2
    assert engine.state.winner == "traitor"
    payload = won_payload(engine)
    assert payload["solo"] is True
    assert payload["reason"] == "endgame"


# ----------------------------------------------------------------------
# Finale loop
# ----------------------------------------------------------------------


def test_one_banish_vote_runs_a_banishment_then_another_end_vote() -> None:
    config = GameConfig(
        game={
            "players": 8,
            "traitors": 3,
            "finale_traitors": 3,
            "finale_faithful": 3,
            "finale_max_votes": 10,
            "max_rounds": 5,
            "endgame_vote": True,
        },
        seed=42,
    )
    env = TraitorsEnvironment(config, EventSink("game-001"), seed=42)
    env.initialize()
    engine = env.engine
    for pid in alive_roles(engine, Role.FAITHFUL)[:2]:
        engine.eliminate(pid, method="night")
    assert engine.state.finale is True

    async def callback(agent_id: str, action_type: ActionType, targets: list[str]):
        if action_type is ActionType.END_VOTE:
            # Always keep playing: the vote must hand over to a real
            # banishment, never finish the game on its own here.
            return Action(
                action=ActionType.END_VOTE, actor_id=agent_id, content="banish"
            )
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} makes their case",
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            return Action(
                action=ActionType.PRIVATE_MESSAGE,
                actor_id=agent_id,
                target=targets[0],
                content=f"{agent_id} whispers",
            )
        # VOTE: pile onto the alphabetically first other player.
        pick = next(p for p in sorted(engine.state.alive_players) if p != agent_id)
        return Action(action=ActionType.VOTE, actor_id=agent_id, target=pick)

    phase_engine = PhaseEngine(config, engine, env.phases())
    context = PhaseContext(engine=engine, config=config, request_action=callback)
    asyncio.run(phase_engine.run(context))

    end_vote_phases = [
        e
        for e in engine.sink.events
        if e.type is EventType.PHASE_STARTED and e.payload.get("phase") == "end_vote"
    ]
    assert len(end_vote_phases) >= 2  # the loop asked again after banishing
    assert len(engine.state.eliminated_players) >= 1
    assert engine.state.winner is not None
    assert not [
        e for e in engine.sink.events if e.type is EventType.MISSION_STARTED
    ]


# ----------------------------------------------------------------------
# Blind finale
# ----------------------------------------------------------------------


def test_blind_finale_hides_a_banishment_until_the_game_ends() -> None:
    config = GameConfig(
        game={
            "players": 8,
            "traitors": 3,
            "finale_traitors": 3,
            "finale_faithful": 3,
            "max_rounds": 5,
            "endgame_vote": True,
            "blind_finale_banishments": True,
        },
        seed=42,
    )
    env = TraitorsEnvironment(config, EventSink("game-001"), seed=42)
    env.initialize()
    engine = env.engine
    for pid in alive_roles(engine, Role.FAITHFUL)[:2]:
        engine.eliminate(pid, method="night")
    assert engine.state.finale is True

    faithful = alive_roles(engine, Role.FAITHFUL)
    banished, viewer = faithful[0], faithful[1]
    banish(engine, banished)
    assert engine.state.winner is None

    view = env.projector.project(engine.state, viewer)
    assert banished not in view.known_roles  # hidden while the game runs

    # Without the blind flag the same projector reveals it immediately.
    open_view = InformationProjector(engine.router).project(engine.state, viewer)
    assert open_view.known_roles[banished] is Role.FAITHFUL

    # Once the game is over, everything is revealed as before.
    for _ in range(3):
        engine.eliminate(alive_roles(engine, Role.TRAITOR)[0], method="vote")
    assert engine.state.winner == "faithful"
    after = env.projector.project(engine.state, viewer)
    assert after.known_roles[banished] is Role.FAITHFUL


# ----------------------------------------------------------------------
# Fake provider and a full fake game
# ----------------------------------------------------------------------


def test_prompt_script_provider_answers_the_end_vote() -> None:
    provider = PromptScriptProvider()

    def prompt(alive: list[str]) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content="You are alice, a player."),
            ChatMessage(
                role="user",
                content=(
                    f"Alive players: {', '.join(alive)}\n"
                    "Required action type: end_vote.\n"
                    'Legal targets: none (this action takes no target; '
                    'set "target" to null).'
                ),
            ),
        ]

    crowded = json.loads(asyncio.run(provider.generate(prompt(["a", "b", "c", "d"]))).content)
    assert crowded["action"] == "end_vote"
    assert crowded["content"] == "banish"  # four remain: keep voting

    few = json.loads(asyncio.run(provider.generate(prompt(["a", "b", "c"]))).content)
    assert few["content"] == "end"  # three remain: confident enough


def test_a_full_fake_endgame_game_runs_to_completion(tmp_path: Path) -> None:
    config = GameConfig(
        game={
            "players": 6,
            "traitors": 2,
            "finale_traitors": 2,
            "finale_faithful": 2,
            "max_rounds": 6,
            "endgame_vote": True,
            "blind_finale_banishments": True,
        },
        seed=3,
    )
    config.llm.provider = "fake"
    runner = GameRunner(
        config, runs_dir=tmp_path, db=Database(), personas_dir=Path("configs/personas")
    )
    result = runner.run(game_id="game-001", seed=3)

    assert result.metrics["status"] == "completed"
    assert result.winner in ("faithful", "traitor")
    transcript = (result.run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert "Game over. Winner:" in transcript
    assert "quality" in result.metrics
