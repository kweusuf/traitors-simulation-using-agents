"""Recruitment: a banished traitor converts one living faithful player.

Covers the config switch, the action rules, the engine conversion (and
its cap), the elimination phase that asks for it, the prompt line the
recruiting traitor sees, and replay/transcript handling of the new
event.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from simulation.actions.actions import Action, ActionType
from simulation.actions.validator import ActionParseError
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.agents.agent import Agent
from simulation.communication.visibility import AgentView
from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import PhaseContext
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.environments.traitors.phases import EliminationPhase
from simulation.environments.traitors.rules import legal_targets
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.replay import ReplayState, build_transcript, render_transcript
from simulation.experiments.runner import GameRunner
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.sink import EventSink


def make_engine(seed: int = 42, **game_overrides) -> GameEngine:
    config = GameConfig(
        game={"players": 6, "traitors": 2, **game_overrides}, seed=seed
    )
    engine = GameEngine(config, EventSink("game-001"), seed=seed)
    engine.start()
    return engine


def roles_of(engine: GameEngine, role: Role) -> list[str]:
    """Living players currently holding `role` (an eliminated player keeps
    their role in the state for reveal purposes)."""
    return sorted(
        p
        for p in engine.state.alive_players
        if engine.state.roles.get(p) is role
    )


def banish(engine: GameEngine, target: str) -> None:
    """Everyone but `target` votes them out, then the tally resolves."""
    engine.state.votes.clear()  # normally done by `start_round`
    engine.begin_phase(GamePhase.VOTING)
    for voter in sorted(engine.state.alive_players):
        if voter == target:
            continue
        result = engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target=target)
        )
        assert result.ok, result.reason
    engine.begin_phase(GamePhase.ELIMINATION)


# ----------------------------------------------------------------------
# Config and targets
# ----------------------------------------------------------------------


def test_recruitment_is_off_by_default() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    assert config.game.recruit_on_banish is False
    assert config.game.max_recruits == 0


def test_long_game_enables_recruitment() -> None:
    config = load_config("configs/traitors/long_game.yaml")
    assert config.game.recruit_on_banish is True
    assert config.game.max_recruits == 0  # no cap: recruits on every banishment


def test_recruit_targets_are_living_faithful_only() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful = roles_of(engine, Role.FAITHFUL)[0]

    targets = legal_targets(engine.state, traitor, ActionType.RECRUIT)
    assert targets
    assert set(targets) <= set(roles_of(engine, Role.FAITHFUL))
    assert traitor not in targets
    assert legal_targets(engine.state, faithful, ActionType.RECRUIT) == []


# ----------------------------------------------------------------------
# Action rules
# ----------------------------------------------------------------------


def test_recruit_rejected_when_disabled() -> None:
    engine = make_engine()  # recruit_on_banish off
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful = roles_of(engine, Role.FAITHFUL)[0]
    engine.begin_phase(GamePhase.ELIMINATION)

    result = engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=faithful)
    )
    assert not result.ok
    assert "disabled" in result.reason


def test_recruit_rejected_for_faithful_actor() -> None:
    engine = make_engine(recruit_on_banish=True)
    faithful = roles_of(engine, Role.FAITHFUL)
    engine.begin_phase(GamePhase.ELIMINATION)

    result = engine.submit_action(
        Action(
            action=ActionType.RECRUIT,
            actor_id=faithful[0],
            target=faithful[1],
        )
    )
    assert not result.ok
    assert "only traitors" in result.reason


def test_recruit_rejected_for_traitor_or_self_target() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor_a, traitor_b = roles_of(engine, Role.TRAITOR)
    engine.begin_phase(GamePhase.ELIMINATION)

    fellow = engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor_a, target=traitor_b)
    )
    assert not fellow.ok and "faithful" in fellow.reason

    self_recruit = engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor_a, target=traitor_a)
    )
    assert not self_recruit.ok and "yourself" in self_recruit.reason


def test_recruit_outside_elimination_phase_is_rejected() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful = roles_of(engine, Role.FAITHFUL)[0]
    engine.begin_phase(GamePhase.VOTING)

    result = engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=faithful)
    )
    assert not result.ok
    assert "not allowed in phase" in result.reason


# ----------------------------------------------------------------------
# Engine conversion
# ----------------------------------------------------------------------


def test_banished_traitor_converts_a_faithful_player() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful_before = set(roles_of(engine, Role.FAITHFUL))
    banish(engine, traitor)

    recruit = sorted(faithful_before)[0]
    accepted = engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=recruit)
    )
    assert accepted.ok
    assert engine.recruitment_opportunity(traitor)  # still open at this point

    engine.resolve_votes()

    assert engine.state.roles[recruit] is Role.TRAITOR
    assert traitor in engine.state.eliminated_players
    assert recruit in engine.state.alive_players
    assert engine.recruits_used == 1
    assert engine.state.winner is None  # traitor count unchanged

    recruited = [e for e in engine.sink.events if e.type is EventType.ROLE_RECRUITED]
    assert len(recruited) == 1
    assert recruited[0].actor == recruit
    assert recruited[0].payload["by"] == traitor


def test_recruitment_saves_the_last_traitor_from_a_faithful_win() -> None:
    engine = make_engine(players=4, traitors=1, recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    recruit = sorted(roles_of(engine, Role.FAITHFUL))[0]
    banish(engine, traitor)
    engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=recruit)
    )

    engine.resolve_votes()
    assert engine.state.winner is None
    assert roles_of(engine, Role.TRAITOR) == [recruit]

    # Without the recruit the same banishment ends the game.
    clean = make_engine(players=4, traitors=1, recruit_on_banish=True)
    other = roles_of(clean, Role.TRAITOR)[0]
    banish(clean, other)
    clean.resolve_votes()
    assert clean.state.winner == "faithful"


def test_recruit_cap_stops_conversions() -> None:
    engine = make_engine(recruit_on_banish=True, max_recruits=1)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    recruit = sorted(roles_of(engine, Role.FAITHFUL))[0]
    banish(engine, traitor)
    engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=recruit)
    )
    engine.resolve_votes()
    assert engine.recruits_used == 1
    assert engine.state.roles[recruit] is Role.TRAITOR

    # Second traitor goes: the cap is spent, so nobody converts this time.
    second = next(p for p in roles_of(engine, Role.TRAITOR) if p != recruit)
    assert not engine.recruitment_opportunity(second)
    banish(engine, second)
    engine.resolve_votes()

    assert engine.recruits_used == 1
    assert second in engine.state.eliminated_players
    assert engine.state.roles[recruit] is Role.TRAITOR
    assert engine.state.winner is None  # one traitor, three faithful left


def test_only_traitors_are_offered_recruitment() -> None:
    engine = make_engine(players=4, traitors=2, recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    faithful = sorted(roles_of(engine, Role.FAITHFUL))[0]
    banish(engine, traitor)

    assert engine.recruitment_opportunity(traitor)
    assert not engine.recruitment_opportunity(faithful)


# ----------------------------------------------------------------------
# Elimination phase
# ----------------------------------------------------------------------


def test_elimination_phase_requests_recruit_before_banishing() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    recruit = sorted(roles_of(engine, Role.FAITHFUL))[0]
    banish(engine, traitor)

    engine.begin_phase(GamePhase.ELIMINATION)
    asked: list[tuple[str, ActionType]] = []

    async def spy(agent_id: str, action_type: ActionType, targets: list[str]):
        asked.append((agent_id, action_type))
        assert action_type is ActionType.RECRUIT
        assert targets == legal_targets(engine.state, agent_id, ActionType.RECRUIT)
        return Action(action=ActionType.RECRUIT, actor_id=agent_id, target=targets[0])

    context = PhaseContext(engine=engine, config=engine.config, request_action=spy)
    result = asyncio.run(EliminationPhase().run(context))

    assert asked == [(traitor, ActionType.RECRUIT)]
    assert result["tie"] is False
    assert engine.state.roles[recruit] is Role.TRAITOR
    assert traitor in engine.state.eliminated_players


def test_elimination_phase_skips_recruit_when_disabled() -> None:
    engine = make_engine()  # recruitment off
    traitor = roles_of(engine, Role.TRAITOR)[0]
    banish(engine, traitor)

    async def spy(agent_id: str, action_type: ActionType, targets: list[str]):
        raise AssertionError("no action should be requested when recruitment is off")

    context = PhaseContext(engine=engine, config=engine.config, request_action=spy)
    asyncio.run(EliminationPhase().run(context))
    assert engine.state.roles[traitor] is Role.TRAITOR
    assert traitor in engine.state.eliminated_players


def test_unparseable_recruit_does_not_abort_the_banishment() -> None:
    engine = make_engine(recruit_on_banish=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    banish(engine, traitor)

    async def broken(agent_id: str, action_type: ActionType, targets: list[str]):
        raise ActionParseError("model returned prose")

    context = PhaseContext(engine=engine, config=engine.config, request_action=broken)
    asyncio.run(EliminationPhase().run(context))

    assert traitor in engine.state.eliminated_players
    assert engine.recruits_used == 0
    rejected = [e for e in engine.sink.events if e.type is EventType.ACTION_REJECTED]
    assert rejected and rejected[0].payload["action"] == "recruit"


# ----------------------------------------------------------------------
# Prompt and agent role sync
# ----------------------------------------------------------------------


def test_prompt_explains_the_recruit_action() -> None:
    view = AgentView(
        agent_id="alice",
        game_id="game-001",
        round_number=1,
        phase=GamePhase.ELIMINATION,
        own_role=Role.TRAITOR,
        known_roles={"alice": Role.TRAITOR},
        alive_players=["alice", "bob"],
        eliminated_players=[],
        public_transcript=[],
        private_conversations=[],
    )
    text = PromptBuilder().build_user(view, ActionType.RECRUIT, ["bob"])
    assert "Required action type: recruit." in text
    assert "recruit a living faithful player" in text
    assert "Legal targets: bob" in text


def test_sync_roles_promotes_a_recruited_agent() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2, "recruit_on_banish": True})
    env = TraitorsEnvironment(config, EventSink("game-001"))
    env.initialize()
    traitor = roles_of(env.engine, Role.TRAITOR)[0]
    faithful = roles_of(env.engine, Role.FAITHFUL)[0]

    agents = {
        pid: Agent(pid, pid.capitalize(), Persona(description="plays."), Goals())
        for pid in env.engine.player_ids
    }
    for pid, role in env.state.roles.items():
        agents[pid].assign_role(role)
    assert agents[faithful].role is Role.FAITHFUL

    env.state.roles[faithful] = Role.TRAITOR  # what `_apply_recruit` does
    GameRunner._sync_roles(env, agents)

    assert agents[faithful].role is Role.TRAITOR
    assert agents[faithful].goals.secondary == ["ensure_traitor_team_wins"]
    assert agents[traitor].role is Role.TRAITOR


# ----------------------------------------------------------------------
# Replay, transcript, and an end-to-end fake game
# ----------------------------------------------------------------------


def recruit_events() -> list[Event]:
    return [
        Event(
            event_id="e1",
            game_id="game-001",
            sequence=1,
            round=1,
            phase="setup",
            type=EventType.GAME_STARTED,
            payload={"players": ["alice", "bob"]},
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
            type=EventType.ROLE_RECRUITED,
            actor="bob",
            targets=["alice"],
            payload={"by": "alice", "recruits_used": 1},
        ),
    ]


def test_replay_applies_role_recruitment() -> None:
    state = ReplayState.from_events(recruit_events())
    assert state.roles["bob"] is Role.TRAITOR
    assert state.project("bob").own_role is Role.TRAITOR
    # Bob now sees his fellow traitor, and nobody else does.
    assert state.project("bob").known_roles["alice"] is Role.TRAITOR

    transcript = build_transcript(recruit_events())
    assert transcript["recruitments"] == [
        {"round": 1, "player": "bob", "by": "alice"}
    ]
    assert "Recruited: bob (by alice)" in render_transcript(recruit_events())


def test_full_fake_game_with_recruitment(tmp_path: Path) -> None:
    """Play whole games with recruitment on; roles, artifacts and replay agree."""
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    config.game.recruit_on_banish = True

    total_recruits = 0
    for seed in range(1, 11):
        runner = GameRunner(
            config,
            runs_dir=tmp_path / str(seed),
            db=Database(),
            personas_dir=Path("configs/personas"),
        )
        result = runner.run(game_id="game-001", seed=seed)
        events = result.events

        recruited = [e for e in events if e.type is EventType.ROLE_RECRUITED]
        total_recruits += len(recruited)

        saved = json.loads((result.run_dir / "game.json").read_text())
        replay = ReplayState.from_events(events)
        assert {pid: r.value for pid, r in replay.roles.items()} == saved["roles"]
        assert sorted(replay.alive) == sorted(
            pid for pid, p in saved["players"].items() if p["alive"]
        )
        assert replay.winner == saved["winner"]
        assert result.metrics["winner"] == saved["winner"]

        # A recruit must have been faithful at assignment time.
        assigned = {
            e.actor: e.payload["role"]
            for e in events
            if e.type is EventType.ROLE_ASSIGNED
        }
        for event in recruited:
            assert assigned[event.actor] == "faithful"
            assert saved["roles"][event.actor] == "traitor"

        # Traitors can never be recruited onto a dead or non-faithful player.
        assert result.metrics["rejected_actions"] == 0

    assert total_recruits >= 1, "no traitor was banished across seeds 1-10"
