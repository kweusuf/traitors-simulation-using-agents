"""Agent memory, end to end: a real game on the fake backend.

Unit tests prove the rules; this proves they are actually wired into a
running game, which is the failure mode that left `ShortTermMemory` and
`Relationships` as dead code for the whole project so far.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from simulation.actions.actions import ActionType
from simulation.agents.agent import Agent
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.agents.runtime import AgentRuntime
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.environments.traitors.memory import writes_for
from simulation.experiments.config import GameConfig
from simulation.models.fake import PromptScriptProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig
from simulation.persistence.sink import EventSink
from simulation.persistence.event_log import EventType


def play_with_memory(tmp_path: Path, seed: int = 5):
    """Play a full game with memory on; return agents, views and events."""
    config = GameConfig(
        game={
            "players": 6,
            "traitors": 2,
            "max_rounds": 4,
            "agent_memory": True,
            "recruit_choice": False,
        },
        seed=seed,
    )
    sink = EventSink("game-001", jsonl_path=tmp_path / "events.jsonl")
    env = TraitorsEnvironment(config, sink, seed=seed)
    env.initialize()

    agents = {
        pid: Agent(pid, pid.capitalize(), Persona(description=f"{pid} persona."))
        for pid in env.engine.player_ids
    }
    for pid, role in env.state.roles.items():
        agents[pid].assign_role(role)

    runtime = AgentRuntime(
        agents=agents,
        gateway=LLMGateway(PromptScriptProvider(), max_concurrency=2),
        model_config=ModelConfig(),
        memory_enabled=True,
    )
    projector = InformationProjector(env.engine.router)
    views: list[AgentView] = []
    history: list[dict] = []
    cursor = {"at": 0}

    async def callback(agent_id, action_type, legal_targets, extra_instruction=None):
        # The same drain the runner does, including the resume seed: a
        # resumed process starts with `sink.events` cleared, so the history
        # has to come from the log.
        if not history:
            history.extend(
                e.model_dump(mode="json") for e in sink.prior_events()
            )
        seen = max((int(e.get("sequence", -1)) for e in history), default=-1)
        for event in sink.events:
            if event.sequence > seen:
                history.append(event.model_dump(mode="json"))
                seen = event.sequence
        for event in history[cursor["at"] :]:
            for write in writes_for(env.state, event, history):
                for pid, agent in agents.items():
                    if write.visible_to(pid, env.state):
                        await agent.remember(
                            content=write.content,
                            kind=write.kind,
                            round_number=int(event.get("round") or 0),
                            sequence=int(event.get("sequence") or 0),
                            subjects=write.subjects,
                            salience=write.salience,
                        )
        cursor["at"] = len(history)
        view = projector.project(env.state, agent_id)
        views.append(view)
        return await runtime.decide(agent_id, view, action_type, legal_targets)

    phase_engine = PhaseEngine(config, env.engine, env.phases())
    context = PhaseContext(engine=env.engine, config=config, request_action=callback)
    asyncio.run(phase_engine.run(context))
    return env, agents, views, [e.model_dump(mode="json") for e in sink.events]

def test_memory_is_actually_filled_during_a_game(tmp_path) -> None:
    env, agents, views, events = play_with_memory(tmp_path)
    total = sum(len(agent.memory.items) for agent in agents.values())
    assert total > 0, "the game ran but nothing was ever remembered"
    recalled = sum(
        len(agent.memory_items(now_round=env.state.round_number, limit=6))
        for agent in agents.values()
    )
    assert recalled > 0, "memories were written but none survived decay"


def test_the_prompt_actually_carries_the_memories(tmp_path) -> None:
    env, agents, views, events = play_with_memory(tmp_path)
    agent = next(a for a in agents.values() if a.memory.items)
    view = next(v for v in views if v.agent_id == agent.agent_id)
    user = PromptBuilder().build_user(
        view,
        action_type=ActionType.PUBLIC_MESSAGE,
        legal_targets=[],
        memory_items=agent.memory_items(now_round=view.round_number, limit=6),
    )
    assert "What you remember:" in user
    assert "your recent memories" not in user.lower()


def test_no_faithful_player_ever_remembers_a_council(tmp_path) -> None:
    """The guard that matters: the tower must not leak into a memory."""
    env, agents, views, events = play_with_memory(tmp_path)
    traitors = {pid for pid, role in env.state.roles.items() if role is Role.TRAITOR}
    for agent in agents.values():
        if agent.role is Role.TRAITOR:
            continue
        for item in agent.memory.items:
            assert "council chose" not in item["content"], (
                f"{agent.agent_id} remembered a traitor council: {item['content']}"
            )
            for pid in traitors:
                assert f"{pid} argued for" not in item["content"], (
                    f"{agent.agent_id} remembered who argued for whom"
                )


def test_memory_records_the_deaths_that_happened(tmp_path) -> None:
    env, agents, views, events = play_with_memory(tmp_path)
    dead = [e["actor"] for e in events if e["type"] == "PLAYER_ELIMINATED"]
    assert dead, "the game produced no eliminations to remember"
    remembered = " ".join(
        item["content"] for agent in agents.values() for item in agent.memory.items
    )
    for pid in dead:
        assert pid in remembered, f"nobody remembered {pid} leaving"


def test_prior_events_survives_a_resume(tmp_path) -> None:
    """`continue_after` clears `events`; the log must still have it all.

    This is the mechanism a resumed game depends on. If `prior_events`
    only returned the new process's events, every agent would start the
    resumed run having forgotten the rounds before the interruption.
    """
    log = tmp_path / "events.jsonl"
    first = EventSink("game-001", jsonl_path=log)
    for i in range(3):
        first.emit(EventType.PHASE_STARTED, round_number=1, phase=f"p{i}")
    assert len(first.events) == 3

    last_sequence = first.events[-1].sequence
    second = EventSink("game-001", jsonl_path=log)
    second.continue_after(last_sequence)
    assert second.events == [], "continue_after is meant to clear the list"
    assert len(second.prior_events()) == 3, "the log lost the earlier events"
    assert [e.sequence for e in second.prior_events()] == [0, 1, 2]


def test_a_resumed_game_remembers_the_rounds_before_the_resume(tmp_path) -> None:
    """Memory is rebuilt from the log, so a resume is not amnesiac."""
    log = tmp_path / "events.jsonl"
    first = EventSink("game-001", jsonl_path=log)
    state = None
    for round_number in (1, 2):
        first.emit(EventType.ROUND_STARTED, round_number=round_number, phase="round_table")
        first.emit(
            EventType.PLAYER_ELIMINATED,
            round_number=round_number,
            phase="elimination",
            actor=f"p{round_number}",
            payload={"method": "night", "votes": {}},
        )
    last_sequence = first.events[-1].sequence

    # A fresh process picks up the same log, exactly as a resume does.
    second = EventSink("game-001", jsonl_path=log)
    second.continue_after(last_sequence)
    history = [e.model_dump(mode="json") for e in second.prior_events()]

    from simulation.engine.state import GameState, PlayerState, Role

    state = GameState(game_id="g", phase="round_table")
    for pid in ("p1", "p2", "p3"):
        state.players[pid] = PlayerState(player_id=pid, name=pid)
        state.alive_players.add(pid)
        state.roles[pid] = Role.FAITHFUL
    state.alive_players.discard("p1")
    state.alive_players.discard("p2")

    agent = Agent("p3", "P3", Persona(description="p3 persona."))
    asyncio.run(_replay_into(agent, state, history))
    texts = " ".join(item["content"] for item in agent.memory.items)
    assert "p1 was murdered" in texts, "round 1 was lost across the resume"
    assert "p2 was murdered" in texts, "round 2 was lost across the resume"


async def _replay_into(agent, state, history) -> None:
    for event in history:
        for write in writes_for(state, event, history):
            if write.visible_to(agent.agent_id, state):
                await agent.remember(
                    content=write.content,
                    kind=write.kind,
                    round_number=int(event.get("round") or 0),
                    sequence=int(event.get("sequence") or 0),
                    subjects=write.subjects,
                    salience=write.salience,
                )

