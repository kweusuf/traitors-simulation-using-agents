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
from simulation.environments.traitors.phases import EliminationPhase, TraitorNightPhase
from simulation.environments.traitors.rules import legal_targets
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.replay import ReplayState, build_transcript, render_transcript
from simulation.experiments.runner import ARTIFACTS, GameRunner
from simulation.models.base import ChatMessage
from simulation.models.fake import PromptScriptProvider
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


def banish_traitor(engine: GameEngine) -> str:
    """Banish a living traitor, opening tonight's recruitment window."""
    traitor = roles_of(engine, Role.TRAITOR)[0]
    banish(engine, traitor)
    engine.resolve_votes()
    return traitor


def events_of(engine: GameEngine, type_: EventType) -> list:
    return [e for e in engine.sink.events if e.type is type_]


def night_deaths(engine: GameEngine) -> list:
    """PLAYER_ELIMINATED events whose method is the night murder."""
    return [
        e
        for e in engine.sink.events
        if e.type is EventType.PLAYER_ELIMINATED
        and e.payload.get("method") == "night"
    ]


def night_callback(
    engine: GameEngine,
    *,
    decision: str = "recruit",
    offer_to: str | None = None,
    response: str | None = None,
    asked: list[ActionType] | None = None,
):
    """Scripted traitor night: decide, offer, answer, and kill or not."""

    async def callback(agent_id: str, action_type: ActionType, targets: list[str]):
        if asked is not None:
            asked.append(action_type)
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=ActionType.TRAITOR_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} plots",
            )
        if action_type is ActionType.RECRUIT_DECISION:
            return Action(
                action=ActionType.RECRUIT_DECISION,
                actor_id=agent_id,
                content=decision,
            )
        if action_type is ActionType.RECRUIT:
            return Action(
                action=ActionType.RECRUIT,
                actor_id=agent_id,
                target=offer_to or targets[0],
            )
        if action_type is ActionType.RECRUIT_RESPONSE:
            assert response is not None, "no response was scripted"
            return Action(
                action=ActionType.RECRUIT_RESPONSE,
                actor_id=agent_id,
                content=response,
            )
        # NOMINATE and TRAITOR_KILL: first legal target.
        return Action(action=action_type, actor_id=agent_id, target=targets[0])

    return callback


def run_night(engine: GameEngine, callback) -> dict:
    context = PhaseContext(
        engine=engine, config=engine.config, request_action=callback
    )
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    return asyncio.run(TraitorNightPhase().run(context))


# ----------------------------------------------------------------------
# Config and targets
# ----------------------------------------------------------------------


def test_recruitment_is_off_by_default() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    assert config.game.recruit_on_banish is False
    assert config.game.recruit_choice is False
    assert config.game.max_recruits == 0


def test_show_configs_use_recruitment_as_a_choice() -> None:
    for path in (
        "configs/traitors/season_uk_s01.yaml",
        "configs/traitors/long_game.yaml",
    ):
        config = load_config(path)
        assert config.game.recruit_choice is True, path
        # The automatic banishment conversion is off: the vote owns it.
        assert config.game.recruit_on_banish is False, path
        # The cap applies to the choice window as well. The long game
        # leaves it open; the season replay caps it at the two attempts
        # Series 1 actually made, so recruitment cannot refill the tower
        # ahead of the endgame.
        expected = 2 if "season_uk_s01" in path else 0
        assert config.game.max_recruits == expected, path


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


# ----------------------------------------------------------------------
# Recruitment as a choice (phase 26)
# ----------------------------------------------------------------------


def test_recruit_decision_requires_traitor_role_phase_and_window() -> None:
    engine = make_engine(players=6, traitors=2, recruit_choice=True)
    traitor = roles_of(engine, Role.TRAITOR)[0]

    # The vote lives in the traitor night only.
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    out_of_phase = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=traitor,
            content="recruit",
        )
    )
    assert not out_of_phase.ok and "not allowed in phase" in out_of_phase.reason

    # No window until a traitor is actually banished at the round table.
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    closed = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=traitor,
            content="recruit",
        )
    )
    assert not closed.ok and "window" in closed.reason

    banish_traitor(engine)
    assert engine.recruit_window_open()
    survivor = roles_of(engine, Role.TRAITOR)[0]
    faithful = roles_of(engine, Role.FAITHFUL)[0]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)

    # A faithful player never gets the call.
    by_faithful = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=faithful,
            content="recruit",
        )
    )
    assert not by_faithful.ok and "only traitors" in by_faithful.reason

    # Content is trimmed and case-insensitive, like the end vote.
    accepted = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=survivor,
            content="  RECRUIT ",
        )
    )
    assert accepted.ok, accepted.reason
    assert engine.resolve_recruit_choice() == "recruit"


def test_recruit_decision_rejects_bad_content() -> None:
    engine = make_engine(players=6, traitors=2, recruit_choice=True)
    banish_traitor(engine)
    traitor = roles_of(engine, Role.TRAITOR)[0]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)

    result = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=traitor,
            content="maybe",
        )
    )
    assert not result.ok and "'recruit' or 'murder'" in result.reason
    assert engine.recruit_offered is None


def test_window_closed_when_tower_is_full() -> None:
    """No banishment yet, so the window mode refuses the choice."""
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    assert len(roles_of(engine, Role.TRAITOR)) == 3 == engine.traitor_capacity
    assert not engine.recruit_window_open()


def test_capacity_is_the_starting_traitor_count() -> None:
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    assert engine.traitor_capacity == 3
    banish_traitor(engine)
    assert len(roles_of(engine, Role.TRAITOR)) == 2
    # A vacancy opened, so the window has something to fill.
    assert engine.recruit_window_open()


def test_window_off_needs_no_banishment() -> None:
    """The switch drops the banishment gate entirely.

    Nothing has been banished, so the default mode refuses; the point of
    the switch is that the traitors get the choice on any traitor night.
    """
    gated = make_engine(players=8, traitors=3, recruit_choice=True)
    assert not gated.recruit_window_open()

    free = make_engine(
        players=8, traitors=3, recruit_choice=True, recruit_window=False
    )
    # Still no vacancy to fill, so it is refused for the other reason.
    assert not free.recruit_window_open()

    banish_traitor(gated)
    banish_traitor(free)
    assert gated.recruit_window_open()
    assert free.recruit_window_open()


def test_recruit_refills_to_capacity_then_closes() -> None:
    """A recruit tops the tower back up and the choice is withdrawn.

    The ceiling is the count the game dealt, so a converted faithful can
    only ever fill a slot a banishment opened - never grow the team past
    where it started.
    """
    engine = make_engine(
        players=8, traitors=3, recruit_choice=True, recruit_window=False
    )
    banish_traitor(engine)
    faithful = roles_of(engine, Role.FAITHFUL)[0]

    run_night(engine, night_callback(engine, offer_to=faithful, response="accept"))

    assert engine.state.roles[faithful] is Role.TRAITOR
    assert len(roles_of(engine, Role.TRAITOR)) == 3 == engine.traitor_capacity
    assert engine.recruits_used == 1
    # Tower full again, so no further offer even with the window off.
    assert not engine.recruit_window_open()


def test_recruit_decision_tie_falls_to_murder() -> None:
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    banish_traitor(engine)
    traitors = roles_of(engine, Role.TRAITOR)
    assert len(traitors) == 2  # a split council can tie

    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    for actor, choice in zip(traitors, ("recruit", "murder")):
        assert engine.submit_action(
            Action(
                action=ActionType.RECRUIT_DECISION,
                actor_id=actor,
                content=choice,
            )
        ).ok

    assert engine.resolve_recruit_choice() == "murder"
    made = events_of(engine, EventType.RECRUIT_CHOICE_MADE)
    assert made[0].payload["choice"] == "murder"
    assert made[0].payload["votes"] == {
        traitors[0]: "recruit",
        traitors[1]: "murder",
    }


def test_recruit_decision_majority_recruit() -> None:
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    banish_traitor(engine)
    traitors = roles_of(engine, Role.TRAITOR)
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    for actor, choice in zip(traitors, ("recruit", "recruit", "murder")):
        engine.submit_action(
            Action(
                action=ActionType.RECRUIT_DECISION,
                actor_id=actor,
                content=choice,
            )
        )
    assert engine.resolve_recruit_choice() == "recruit"


def test_recruit_choice_wins_over_recruit_on_banish() -> None:
    # Both flags on: the automatic conversion must not fire, and the
    # banishment must not open the elimination-phase recruit.
    engine = make_engine(
        players=6, traitors=2, recruit_on_banish=True, recruit_choice=True
    )
    traitor = roles_of(engine, Role.TRAITOR)[0]
    assert not engine.recruitment_opportunity(traitor)
    assert engine.state.roles[traitor] is Role.TRAITOR


def test_max_recruits_cap_blocks_the_offer() -> None:
    engine = make_engine(
        players=8, traitors=3, recruit_choice=True, max_recruits=1
    )
    engine.recruits_used = 1  # the cap is already spent
    banish_traitor(engine)

    assert not engine.recruit_window_open()
    traitor = roles_of(engine, Role.TRAITOR)[0]
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    blocked = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_DECISION,
            actor_id=traitor,
            content="recruit",
        )
    )
    assert not blocked.ok and "window" in blocked.reason


# ----------------------------------------------------------------------
# The night itself
# ----------------------------------------------------------------------


def test_murder_choice_still_kills_and_spends_the_offer() -> None:
    engine = make_engine(
        players=8, traitors=3, recruit_choice=True, on_trial=True
    )
    banish_traitor(engine)
    asked: list[ActionType] = []
    callback = night_callback(engine, decision="murder", asked=asked)

    result = run_night(engine, callback)

    assert result["victim"] is not None
    assert result["victim"] in engine.state.eliminated_players
    # The normal night still ran: nominations, then the kill.
    assert ActionType.NOMINATE in asked
    assert ActionType.TRAITOR_KILL in asked
    assert ActionType.RECRUIT not in asked
    assert (
        events_of(engine, EventType.RECRUIT_CHOICE_MADE)[0].payload["choice"]
        == "murder"
    )
    assert events_of(engine, EventType.RECRUIT_OFFERED) == []
    assert events_of(engine, EventType.ROLE_RECRUITED) == []
    assert not engine.recruit_window_open()  # the opportunity is spent


def test_recruit_path_kills_nobody_and_flips_the_target_on_accept() -> None:
    engine = make_engine(
        players=8, traitors=3, recruit_choice=True, on_trial=True
    )
    banish_traitor(engine)
    target = roles_of(engine, Role.FAITHFUL)[0]
    asked: list[ActionType] = []
    callback = night_callback(
        engine, decision="recruit", offer_to=target, response="accept", asked=asked
    )

    result = run_night(engine, callback)

    assert result["victim"] is None
    # No murder means no nomination and no kill at all.
    assert ActionType.NOMINATE not in asked
    assert ActionType.TRAITOR_KILL not in asked
    assert ActionType.RECRUIT_RESPONSE in asked

    assert engine.state.roles[target] is Role.TRAITOR
    assert target in engine.state.alive_players
    assert engine.recruits_used == 1
    assert night_deaths(engine) == []
    assert events_of(engine, EventType.TRAITOR_KILL) == []

    offered = events_of(engine, EventType.RECRUIT_OFFERED)
    assert offered[0].payload["target"] == target
    assert offered[0].targets == [target]
    assert events_of(engine, EventType.RECRUIT_ACCEPTED)[0].actor == target
    assert events_of(engine, EventType.ROLE_RECRUITED)[0].actor == target
    assert not engine.recruit_window_open()


def test_decline_with_two_traitors_wastes_the_night() -> None:
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    banish_traitor(engine)
    assert len(roles_of(engine, Role.TRAITOR)) == 2
    target = roles_of(engine, Role.FAITHFUL)[0]
    callback = night_callback(
        engine, decision="recruit", offer_to=target, response="decline"
    )

    result = run_night(engine, callback)

    assert result["victim"] is None
    assert night_deaths(engine) == []
    assert events_of(engine, EventType.RECRUIT_DECLINED)[0].actor == target
    assert events_of(engine, EventType.ULTIMATUM_ISSUED) == []
    assert events_of(engine, EventType.ROLE_RECRUITED) == []
    assert engine.state.roles[target] is Role.FAITHFUL
    assert engine.recruits_used == 0


def test_decline_against_a_lone_traitor_murders_the_target() -> None:
    engine = make_engine(players=6, traitors=2, recruit_choice=True)
    banish_traitor(engine)
    lone = roles_of(engine, Role.TRAITOR)
    assert len(lone) == 1
    target = roles_of(engine, Role.FAITHFUL)[0]
    callback = night_callback(
        engine, decision="recruit", offer_to=target, response="decline"
    )

    result = run_night(engine, callback)

    assert result["victim"] == target
    assert target not in engine.state.alive_players
    assert events_of(engine, EventType.RECRUIT_DECLINED)[0].actor == target
    ultimatum = events_of(engine, EventType.ULTIMATUM_ISSUED)
    assert ultimatum[0].actor == lone[0]
    assert ultimatum[0].targets == [target]
    assert events_of(engine, EventType.ROLE_RECRUITED) == []

    # The ultimatum lands through the normal night elimination path.
    deaths = night_deaths(engine)
    assert deaths[0].actor == target
    events = engine.sink.events
    assert events.index(ultimatum[0]) < events.index(deaths[0])


def test_recruit_response_is_only_for_the_offered_player() -> None:
    engine = make_engine(players=8, traitors=3, recruit_choice=True)
    banish_traitor(engine)
    target = roles_of(engine, Role.FAITHFUL)[0]
    other = next(p for p in roles_of(engine, Role.FAITHFUL) if p != target)
    traitor = roles_of(engine, Role.TRAITOR)[0]

    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    assert engine.submit_action(
        Action(action=ActionType.RECRUIT, actor_id=traitor, target=target)
    ).ok
    assert engine.resolve_recruit_offer() == target

    early = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_RESPONSE,
            actor_id=other,
            content="accept",
        )
    )
    assert not early.ok and "offered player" in early.reason

    accepted = engine.submit_action(
        Action(
            action=ActionType.RECRUIT_RESPONSE,
            actor_id=target,
            content="  ACCEPT ",
        )
    )
    assert accepted.ok, accepted.reason
    # Once per phase per player.
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    assert engine.recruit_offered is None


# ----------------------------------------------------------------------
# Prompts and the fake provider
# ----------------------------------------------------------------------


def test_recruitment_prompts_explain_the_choice() -> None:
    builder = PromptBuilder()
    view = AgentView(
        agent_id="alice",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.TRAITOR_NIGHT,
        own_role=Role.TRAITOR,
        known_roles={"alice": Role.TRAITOR},
        alive_players=["alice", "bob"],
        eliminated_players=[],
        public_transcript=[],
        private_conversations=[],
    )

    decision = builder.build_user(view, ActionType.RECRUIT_DECISION, [])
    assert "Required action type: recruit_decision." in decision
    assert "costs tonight's murder" in decision
    assert "A lone traitor can force a recruit" in decision
    assert "'recruit' or 'murder'" in decision

    response_view = view.model_copy(
        update={"agent_id": "bob", "own_role": Role.FAITHFUL, "known_roles": {"bob": Role.FAITHFUL}}
    )
    response = builder.build_user(response_view, ActionType.RECRUIT_RESPONSE, [])
    assert "Required action type: recruit_response." in response
    assert "The offer is real" in response
    assert "allowed to decline" in response
    assert "refusing is fatal" in response
    assert "'accept' or 'decline'" in response


def test_recruitment_json_hints_offer_no_answer_of_their_own() -> None:
    from simulation.agents.prompts import action_json_hint

    # As with the end vote: the example holds a placeholder, not an
    # accepted answer. `recruit`/`accept` in these slots pushed the tower's
    # way, and showing one answer is what the model copies.
    decision = action_json_hint(ActionType.RECRUIT_DECISION)
    assert '"action": "recruit_decision"' in decision
    assert '"target": null' in decision
    assert '"content": "?"' in decision
    assert '"content": "recruit"' not in decision
    assert '"content": "murder"' not in decision

    response = action_json_hint(ActionType.RECRUIT_RESPONSE)
    assert '"action": "recruit_response"' in response
    assert '"target": null' in response
    assert '"content": "?"' in response
    assert '"content": "accept"' not in response
    assert '"content": "decline"' not in response


def test_prompt_script_provider_answers_the_recruitment_turns() -> None:
    provider = PromptScriptProvider()

    def prompt(action: str) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content="You are alice, a player."),
            ChatMessage(
                role="user",
                content=(
                    "Alive players: a, b, c, d\n"
                    f"Required action type: {action}.\n"
                    'Legal targets: none (this action takes no target; '
                    'set "target" to null).'
                ),
            ),
        ]

    decision = json.loads(
        asyncio.run(provider.generate(prompt("recruit_decision"))).content
    )
    assert decision["action"] == "recruit_decision"
    assert decision["content"] in ("recruit", "murder")

    response = json.loads(
        asyncio.run(provider.generate(prompt("recruit_response"))).content
    )
    assert response["action"] == "recruit_response"
    assert response["content"] in ("accept", "decline")


# ----------------------------------------------------------------------
# Replay, transcript, quality, and a full fake game
# ----------------------------------------------------------------------


def recruit_choice_events(outcome: str = "accepted") -> list[Event]:
    events = [
        Event(
            event_id="e1",
            game_id="game-001",
            sequence=1,
            round=1,
            phase="traitor_night",
            type=EventType.RECRUIT_CHOICE_MADE,
            payload={"choice": "recruit", "votes": {"alice": "recruit"}},
        ),
        Event(
            event_id="e2",
            game_id="game-001",
            sequence=2,
            round=1,
            phase="traitor_night",
            type=EventType.RECRUIT_OFFERED,
            actor="alice",
            targets=["bob"],
            payload={"target": "bob", "by": "alice"},
        ),
    ]
    if outcome == "accepted":
        events += [
            Event(
                event_id="e3",
                game_id="game-001",
                sequence=3,
                round=1,
                phase="traitor_night",
                type=EventType.RECRUIT_ACCEPTED,
                actor="bob",
                targets=["alice"],
                payload={"by": "alice"},
            ),
            Event(
                event_id="e4",
                game_id="game-001",
                sequence=4,
                round=1,
                phase="traitor_night",
                type=EventType.ROLE_RECRUITED,
                actor="bob",
                targets=["alice"],
                payload={"by": "alice", "recruits_used": 1},
            ),
        ]
    else:
        events += [
            Event(
                event_id="e3",
                game_id="game-001",
                sequence=3,
                round=1,
                phase="traitor_night",
                type=EventType.RECRUIT_DECLINED,
                actor="bob",
                targets=["alice"],
                payload={"by": "alice"},
            ),
            Event(
                event_id="e4",
                game_id="game-001",
                sequence=4,
                round=1,
                phase="traitor_night",
                type=EventType.ULTIMATUM_ISSUED,
                actor="alice",
                targets=["bob"],
                payload={"target": "bob", "by": "alice"},
            ),
            Event(
                event_id="e5",
                game_id="game-001",
                sequence=5,
                round=1,
                phase="traitor_night",
                type=EventType.PLAYER_ELIMINATED,
                actor="bob",
                payload={"method": "night", "votes": {}},
            ),
        ]
    return events


def test_transcript_renders_the_recruitment_night() -> None:
    text = render_transcript(recruit_choice_events())
    assert "Traitors choose to recruit tonight (alice=recruit)" in text
    assert "Recruit offer: bob is offered a place among the traitors" in text
    assert "Recruit accepted: bob joins the traitors" in text
    assert "Recruited: bob (by alice)" in text

    declined = render_transcript(recruit_choice_events(outcome="declined"))
    assert "Recruit declined: bob refuses to join the traitors" in declined
    assert "Ultimatum: alice murders bob for refusing" in declined
    assert "Eliminated: bob (night)" in declined


def test_replay_and_transcript_handle_the_recruitment_events() -> None:
    events = [
        Event(
            event_id="e0",
            game_id="game-001",
            sequence=0,
            round=0,
            phase="setup",
            type=EventType.GAME_STARTED,
            payload={"players": ["alice", "bob"]},
        ),
        Event(
            event_id="e0a",
            game_id="game-001",
            sequence=0,
            phase="setup",
            type=EventType.ROLE_ASSIGNED,
            actor="alice",
            payload={"role": "traitor"},
        ),
        Event(
            event_id="e0b",
            game_id="game-001",
            sequence=0,
            phase="setup",
            type=EventType.ROLE_ASSIGNED,
            actor="bob",
            payload={"role": "faithful"},
        ),
    ] + recruit_choice_events()

    state = ReplayState.from_events(events)
    assert state.event_count == len(events)
    assert state.roles["bob"] is Role.TRAITOR
    transcript = build_transcript(events)
    assert transcript["recruitments"] == [
        {"round": 1, "player": "bob", "by": "alice"}
    ]


def test_full_fake_game_with_recruit_choice(tmp_path: Path) -> None:
    config = GameConfig(
        game={
            "players": 10,
            "traitors": 3,
            # Pin the traitors to the players the fake vote banishes
            # first, so a recruitment window is guaranteed to open.
            "traitor_names": ["alice", "bob", "charlie"],
            "max_rounds": 6,
            "recruit_choice": True,
            "recruit_on_banish": False,
        },
        seed=11,
    )
    config.llm.provider = "fake"
    runner = GameRunner(
        config,
        runs_dir=tmp_path / "runs",
        db=Database(),
        personas_dir=Path("configs/personas"),
    )
    result = runner.run(game_id="game-001", seed=11)

    assert result.metrics["status"] == "completed"
    assert result.winner in ("faithful", "traitor")
    for name in ARTIFACTS:
        assert (result.run_dir / name).exists(), f"missing artifact {name}"

    types = [e.type for e in result.events]
    assert EventType.RECRUIT_CHOICE_MADE in types
    assert EventType.RECRUIT_OFFERED in types
    assert EventType.RECRUIT_ACCEPTED in types
    assert EventType.ROLE_RECRUITED in types
    assert EventType.ACTION_REJECTED not in types

    # Replay rebuilds the recruited roles from the events alone.
    saved = json.loads((result.run_dir / "game.json").read_text())
    replay = ReplayState.from_events(result.events)
    assert {pid: r.value for pid, r in replay.roles.items()} == saved["roles"]

    narrative = (result.run_dir / "transcript.txt").read_text(encoding="utf-8")
    assert "Recruit offer:" in narrative
    assert "Recruit accepted:" in narrative
