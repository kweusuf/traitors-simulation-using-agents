"""Integration tests: deterministic games on the fake LLM backend.

Milestone 1 (spec section 33): complete games with correct information
boundaries and game rules, no Ollama required.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from simulation.actions.actions import Action, ActionType
from simulation.agents.agent import Agent
from simulation.agents.persona import Persona
from simulation.agents.runtime import AgentRuntime
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig, load_config
from simulation.models.base import ChatMessage
from simulation.models.fake import FakeLLMProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.jsonl import EventLog
from simulation.persistence.repositories import EventRepository
from simulation.persistence.sink import EventSink

_ACTION_RE = re.compile(r"Required action type: (\w+)")
_TARGETS_RE = re.compile(r"^Legal targets: (.+)$", re.MULTILINE)


class DeterministicScriptProvider(FakeLLMProvider):
    """A fake provider that answers from the prompt itself.

    Reads the required action type and legal target list out of the
    user message and replies with a deterministic structured action, so
    any game length can be played back without scripting every turn.
    """

    def __init__(self) -> None:
        super().__init__(script={})
        self.counts: dict[str, int] = {}

    async def generate(
        self,
        messages: list[ChatMessage],
        response_schema: type = Action,
        config=None,
    ):
        self.calls.append(messages)
        agent_id = self._agent_id(messages)
        user = next(m for m in reversed(messages) if m.role == "user").content

        action_match = _ACTION_RE.search(user)
        assert action_match, "prompt must state the required action type"
        action_type = ActionType(action_match.group(1))

        targets_match = _TARGETS_RE.search(user)
        raw_targets = targets_match.group(1).strip() if targets_match else ""
        if raw_targets.startswith("none") or not raw_targets:
            targets: list[str] = []
        else:
            targets = [t.strip() for t in raw_targets.split(",")]

        n = self.counts.get(agent_id, 0)
        self.counts[agent_id] = n + 1

        if action_type is ActionType.PUBLIC_MESSAGE:
            return self._resp(
                {
                    "action": "public_message",
                    "content": f"[{agent_id}#{n}] I am watching everyone closely.",
                    "confidence": 0.6,
                }
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            return self._resp(
                {
                    "action": "private_message",
                    "target": targets[0],
                    "content": f"[{agent_id}#{n}] privately sharing a read.",
                    "confidence": 0.5,
                }
            )
        # VOTE and TRAITOR_KILL: pile onto the first legal target.
        return self._resp(
            {
                "action": action_type.value,
                "target": targets[0],
                "confidence": 0.8,
                "reason_summary": "first legal target",
            }
        )

    @staticmethod
    def _resp(payload: dict) -> object:
        import json

        class _R:
            content = json.dumps(payload)

        return _R()


def run_fake_game(
    config: GameConfig,
    seed: int,
    tmp_path: Path,
    db: Database | None = None,
) -> tuple[TraitorsEnvironment, list[AgentView], list[dict]]:
    """Play one full game on the fake backend; return env, audited views, events."""
    jsonl_path = tmp_path / f"events-{seed}.jsonl"
    sink = EventSink("game-001", jsonl_path=jsonl_path, db=db)
    env = TraitorsEnvironment(config, sink, db=db, seed=seed)
    env.initialize()

    agents = {
        pid: Agent(pid, pid.capitalize(), Persona(description=f"{pid} persona."))
        for pid in env.engine.player_ids
    }
    for pid, role in env.state.roles.items():
        agents[pid].assign_role(role)

    provider = DeterministicScriptProvider()
    runtime = AgentRuntime(
        agents=agents,
        gateway=LLMGateway(provider, max_concurrency=config.llm.max_concurrency),
        model_config=ModelConfig(),
    )
    projector = InformationProjector(env.engine.router)
    views: list[AgentView] = []

    async def callback(agent_id: str, action_type: ActionType, legal_targets: list[str]) -> Action:
        view = projector.project(env.state, agent_id)
        views.append(view)
        return await runtime.decide(agent_id, view, action_type, legal_targets)

    phase_engine = PhaseEngine(config, env.engine, env.phases())
    context = PhaseContext(engine=env.engine, config=config, request_action=callback)
    asyncio.run(phase_engine.run(context))

    events = [e.model_dump(mode="json") for e in sink.events]
    # JSONL on disk must match what the sink holds.
    on_disk = [e.model_dump(mode="json") for e in EventLog(jsonl_path).read_all()]
    assert on_disk == events, "jsonl log diverged from in-memory events"
    return env, views, events


def audit_information_boundaries(views: list[AgentView]) -> None:
    """Every observation must respect hidden-information rules (spec section 10)."""
    for view in views:
        observer = view.agent_id
        for pid, role in view.known_roles.items():
            if pid == observer:
                continue
            if role is Role.TRAITOR and view.winner is None:
                # Traitors legitimately know their own team (spec section 10).
                if view.own_role is Role.TRAITOR:
                    continue
                assert pid in view.eliminated_players, (
                    f"{observer} learned {pid} is a traitor while {pid} is still hidden"
                )
        for msg in view.private_conversations:
            involved = observer == msg.sender_id or observer in msg.recipients
            assert involved, (
                f"{observer} saw a private message from {msg.sender_id} "
                f"to {msg.recipients}"
            )


def test_four_agent_mini_game(tmp_path) -> None:
    """Spec section 29: 4-agent miniature game on FakeLLMProvider, no Ollama."""
    config = GameConfig(
        game={"players": 4, "traitors": 1, "max_rounds": 5}, seed=7
    )
    env, views, events = run_fake_game(config, seed=7, tmp_path=tmp_path)

    state = env.state
    assert state.winner in ("faithful", "traitor")
    assert state.phase.value == "game_end"
    assert events[-1]["type"] == "GAME_ENDED"
    audit_information_boundaries(views)

    # No rule violations: the fake agent always produced legal actions.
    assert [e for e in events if e["type"] == "ACTION_REJECTED"] == []

    # Eliminations always targeted living players at the time.
    alive = set(env.engine.player_ids)
    for event in events:
        if event["type"] == "PLAYER_ELIMINATED":
            victim = event["actor"]
            assert victim in alive, f"{victim} eliminated twice"
            alive.discard(victim)


def test_six_player_game_from_basic_config(tmp_path) -> None:
    """Spec section 33: full six-player deterministic game, config-driven."""
    config = load_config("configs/traitors/basic.yaml")
    db = Database()
    env, views, events = run_fake_game(config, seed=config.seed, tmp_path=tmp_path, db=db)

    state = env.state
    assert len(state.players) == 6
    assert sum(1 for r in state.roles.values() if r is Role.TRAITOR) == 2
    assert state.winner in ("faithful", "traitor")
    assert events[-1]["type"] == "GAME_ENDED"
    assert state.round_number <= config.game.max_rounds

    types = [e["type"] for e in events]
    for expected in (
        "GAME_STARTED",
        "ROLE_ASSIGNED",
        "ROUND_STARTED",
        "MISSION_STARTED",
        "PUBLIC_MESSAGE",
        "PRIVATE_MESSAGE",
        "VOTE_CAST",
        "GAME_WON",
    ):
        assert expected in types, f"missing event {expected}"

    assert [e for e in events if e["type"] == "ACTION_REJECTED"] == []
    audit_information_boundaries(views)

    # SQLite mirrors: one event row per event, eliminations consistent.
    from simulation.persistence.repositories import EliminationRepository

    stored_events = EventRepository(db).get_all("game-001")
    assert len(stored_events) == len(events)
    assert len(EliminationRepository(db).get("game-001")) == len(
        state.eliminated_players
    )


def test_full_game_is_deterministic(tmp_path) -> None:
    """Same config + seed twice => byte-identical event streams."""
    config = load_config("configs/traitors/basic.yaml")
    _, _, first = run_fake_game(config, seed=config.seed, tmp_path=tmp_path / "a")
    _, _, second = run_fake_game(config, seed=config.seed, tmp_path=tmp_path / "b")
    assert first == second


def test_different_seeds_can_differ(tmp_path) -> None:
    config = load_config("configs/traitors/basic.yaml")
    _, _, a = run_fake_game(config, seed=1, tmp_path=tmp_path / "s1")
    _, _, b = run_fake_game(config, seed=2, tmp_path=tmp_path / "s2")
    # Role assignment differs, so some event payload must differ.
    roles_a = [e for e in a if e["type"] == "ROLE_ASSIGNED"]
    roles_b = [e for e in b if e["type"] == "ROLE_ASSIGNED"]
    assert roles_a != roles_b


def test_replay_from_jsonl_reconstructs_final_state(tmp_path) -> None:
    """Event-sourced replay: reading events back yields the same outcome
    signals as the live run (spec section 25, 44)."""
    config = GameConfig(game={"players": 4, "traitors": 1, "max_rounds": 5}, seed=3)
    env, _, events = run_fake_game(config, seed=3, tmp_path=tmp_path)

    jsonl = tmp_path / "events-3.jsonl"
    replayed = EventLog(jsonl).read_all()
    assert len(replayed) == len(events)

    won = [e for e in replayed if e.type is EventType.GAME_WON]
    assert len(won) == 1
    assert won[0].payload["team"] == env.state.winner
    eliminated = [e.actor for e in replayed if e.type is EventType.PLAYER_ELIMINATED]
    assert eliminated == sorted(
        env.state.eliminated_players,
        key=lambda p: next(
            e["sequence"] for e in events
            if e["type"] == "PLAYER_ELIMINATED" and e["actor"] == p
        ),
    )
