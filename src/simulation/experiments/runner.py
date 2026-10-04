"""Experiment runner: one game, its artifacts, its identity (spec 26, 27, 34, 43).

The runner wires config -> environment -> agents -> gateway -> phase
engine, then writes the run directory artifacts. Game logic itself
never learns about run directories or artifacts.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from simulation.agents.agent import Agent
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona, load_persona_bundle
from simulation.agents.prompts import PromptBuilder
from simulation.agents.runtime import AgentRuntime
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GameState, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.environments.traitors.memory import writes_for
from simulation.experiments.config import GameConfig, LLMSettings
from simulation.experiments.ledger import append_run as append_ledger
from simulation.experiments.observability import NullTracer, build_tracer
from simulation.experiments.quality import analyse as analyse_quality
from simulation.experiments.lock import RunLock
from simulation.experiments.resume import load_point
from simulation.experiments.telemetry import TelemetryRecorder
from simulation.experiments.replay import build_transcript, render_transcript
from simulation.models.fake import PromptScriptProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import LLMProvider
from simulation.models.ollama import OllamaProvider
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.jsonl import EventLog
from simulation.persistence.repositories import (
    ExperimentRepository,
    GameRepository,
)
from simulation.persistence.sink import EventSink

# Reproducibility identity (spec section 27). Bump when the respective
# artifact changes shape or behavior.
PROMPT_VERSION = "v1"
PERSONA_VERSION = "v1"
GAME_RULES_VERSION = "v1"
MEMORY_STRATEGY = "short_term_recency_buffer"

# Artifacts written per run (spec sections 34 and 43).
ARTIFACTS = (
    "config.yaml",
    "events.jsonl",
    "game.json",
    "transcript.json",
    "transcript.txt",
    "metrics.json",
    "llm_calls.jsonl",  # one line per model call: the run's LLM ops log
)

EventObserver = Callable[[Event], None]


@dataclass
class RunResult:
    game_id: str
    experiment_id: str
    seed: int
    run_dir: Path
    winner: Optional[str]
    rounds: int
    events: list[Event] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def artifacts(self) -> list[str]:
        return [name for name in ARTIFACTS if (self.run_dir / name).exists()]


def build_provider(settings: LLMSettings) -> LLMProvider:
    """Provider from config (spec section 17); no provider code elsewhere."""
    if settings.provider == "ollama":
        return OllamaProvider()
    if settings.provider == "fake":
        return PromptScriptProvider()
    raise ValueError(
        f"unknown llm provider '{settings.provider}'; expected 'ollama' or 'fake'"
    )


def model_identity(settings: LLMSettings) -> tuple[str, dict[str, Any]]:
    """`model` label and `model_parameters` for the experiment identity."""
    label = f"{settings.provider}/{settings.model}"
    parameters = {
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "reasoning_effort": settings.reasoning_effort,
        "timeout_seconds": settings.timeout_seconds,
        "retries": settings.retries,
        "max_concurrency": settings.max_concurrency,
        "base_url": settings.base_url,
        "options": dict(settings.options),
    }
    return label, parameters


def make_experiment_id(game_name: str) -> str:
    return f"{game_name}-{uuid.uuid4().hex[:8]}"


class GameRunner:
    """Runs one game per `run()` call and writes its run directory."""

    def __init__(
        self,
        config: GameConfig,
        *,
        runs_dir: str | Path = "runs",
        db: Optional[Database] = None,
        provider: Optional[LLMProvider] = None,
        personas_dir: Optional[str | Path] = None,
        tracer: Optional[NullTracer] = None,
    ) -> None:
        self.config = config
        self.runs_dir = Path(runs_dir)
        self.db = db
        self.provider = provider
        self.personas_dir = Path(personas_dir) if personas_dir else None
        self.tracer = tracer or build_tracer(config.observability)
        # How far into the event log the agents' memories have been filled.
        # Reset per run, so a resumed game re-derives them from the log
        # rather than starting half-remembered.
        self._memory_cursor = 0
        self._memory_history: Optional[list[dict]] = None
        self._memory_seq = -1

    # ------------------------------------------------------------------
    # Identifiers
    # ------------------------------------------------------------------
    def next_game_id(self) -> str:
        """Next free `game-NNN` id inside the runs directory."""
        used = {p.name for p in self.runs_dir.glob("game-*") if p.is_dir()}
        index = 1
        while f"game-{index:03d}" in used:
            index += 1
        return f"game-{index:03d}"

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        game_id: Optional[str] = None,
        seed: Optional[int] = None,
        experiment_id: Optional[str] = None,
        observer: Optional[EventObserver] = None,
        resume: bool = False,
        idle_seconds: Optional[int] = None,
    ) -> RunResult:
        """Play one full game and write `runs/<game_id>/` artifacts.

        With `resume`, the run continues a crashed one instead of
        starting over: the event log is truncated back to its last
        completed phase, the engine is rehydrated from what is left, and
        the phase order picks up where the crash happened. The seed and
        experiment id come from the crashed run's own saved config, so a
        resumed game keeps the identity already recorded for it.
        """
        config = self.config
        seed = config.seed if seed is None else seed
        game_id = game_id or self.next_game_id()
        experiment_id = experiment_id or make_experiment_id(config.game.name)

        run_dir = self.runs_dir / game_id
        # Taken before anything reads or rewrites the log, and held until
        # this run returns. Two writers on one log is the one failure
        # that destroys a run outright, and a slow run cannot be told
        # apart from a dead one by watching its idle time.
        lock = RunLock(run_dir).acquire()
        try:
            return self._run_locked(
                config=config,
                game_id=game_id,
                seed=seed,
                experiment_id=experiment_id,
                observer=observer,
                resume=resume,
                idle_seconds=idle_seconds,
                run_dir=run_dir,
            )
        finally:
            lock.release()

    def _run_locked(
        self,
        *,
        config: GameConfig,
        game_id: str,
        seed: int,
        experiment_id: str,
        observer: Optional[EventObserver],
        resume: bool,
        idle_seconds: Optional[int],
        run_dir: Path,
    ) -> RunResult:
        events_path = run_dir / "events.jsonl"

        resume_at: Optional[tuple[int, int]] = None
        point = None
        if resume:
            # Every check that can refuse has to happen before the run
            # touches the directory. `TelemetryRecorder` truncates
            # `llm_calls.jsonl` on construction, so building it before
            # the resume point is settled would erase a crashed run's
            # telemetry on the very attempt meant to recover it.
            saved = self._resume_identity(run_dir, seed, experiment_id)
            seed, experiment_id = saved
            point_kwargs = (
                {"idle_seconds": idle_seconds} if idle_seconds is not None else {}
            )
            point, last_sequence = load_point(
                run_dir, list(config.phases), **point_kwargs
            )
            # The roster as dealt, which is the order `start()` drew the
            # ambitions in and the only order the seed replay assumes.
            point.recover_ambitions(
                seed,
                list(point.state.players),
                list(config.game.traitor_names or []),
            )
            resume_at = (point.round_number, point.phase_index)
        elif events_path.exists():
            events_path.unlink()  # the JSONL log is append-only

        sink = EventSink(
            game_id, jsonl_path=events_path, db=self.db, observer=observer
        )
        env = TraitorsEnvironment(config, sink, db=self.db, seed=seed)
        if point is not None:
            env.engine.restore_resume(point)
            sink.continue_after(last_sequence)
        else:
            env.initialize()

        agents = self._build_agents(env)
        self._memory_cursor = 0
        self._memory_history = None
        self._memory_seq = -1
        provider = self.provider or build_provider(config.llm)
        gateway = LLMGateway(provider, max_concurrency=config.llm.max_concurrency)
        telemetry = TelemetryRecorder(
            config.llm.model,
            run_dir / "llm_calls.jsonl",
            # A resumed run continues its own telemetry rather than
            # replacing it; a new run starts with an empty log.
            resume=point is not None,
        )
        runtime = AgentRuntime(
            agents=agents,
            gateway=gateway,
            model_config=config.llm.to_model_config(),
            prompt_builder=PromptBuilder(
                transcript_limit=config.communication.transcript_messages_per_prompt,
                language=config.game.language,
                anti_echo_instructions=config.game.anti_echo_instructions,
                want_gist=config.game.co_generate_gist,
                gist_required=config.game.gist_required,
            ),
            telemetry=telemetry,
            memory_enabled=config.game.agent_memory,
            memory_decay=config.game.memory_decay,
            memory_floor=config.game.memory_floor,
            memory_items_limit=config.game.memory_items_in_prompt,
            reject_invented_players=config.game.reject_invented_players,
            reject_repetition=config.game.reject_repetition,
            repetition_threshold=config.game.repetition_threshold,
            repetition_scope=config.game.repetition_scope,
            repetition_min_words=config.game.repetition_min_words,
        )
        context = PhaseContext(
            engine=env.engine,
            config=config,
            request_action=self._callback(env, runtime, agents),
        )

        self.tracer.record("game_started", game_id=game_id, seed=seed)
        started = time.monotonic()
        asyncio.run(
            PhaseEngine(config, env.engine, env.phases()).run(context, resume_at)
        )
        elapsed = time.monotonic() - started

        # Read the log back so artifacts come from the persisted source.
        events = EventLog(events_path).read_all()
        metrics = self._build_metrics(
            experiment_id=experiment_id,
            game_id=game_id,
            seed=seed,
            env=env,
            events=events,
            gateway=gateway,
            provider=provider,
            telemetry=telemetry,
            elapsed=elapsed,
        )
        # The ledger spans runs, so it is appended outside the per-run
        # artifacts, the moment the run has numbers worth comparing.
        append_ledger(self.runs_dir, metrics)
        self._write_artifacts(
            run_dir=run_dir,
            seed=seed,
            state=env.state,
            events=events,
            metrics=metrics,
        )
        self._persist_identity(
            experiment_id=experiment_id,
            game_id=game_id,
            seed=seed,
            winner=metrics["winner"],
            winning_team=metrics["winning_team"],
        )
        self.tracer.record("game_finished", game_id=game_id, winner=metrics["winner"])
        return RunResult(
            game_id=game_id,
            experiment_id=experiment_id,
            seed=seed,
            run_dir=run_dir,
            winner=metrics["winner"],
            rounds=metrics["rounds"],
            events=events,
            metrics=metrics,
        )

    def run_batch(
        self,
        count: int,
        *,
        base_seed: Optional[int] = None,
        experiment_id: Optional[str] = None,
        observer: Optional[EventObserver] = None,
    ) -> list[RunResult]:
        """`count` games, one deterministic seed per game (spec section 26)."""
        if count < 1:
            raise ValueError("batch size must be >= 1")
        base_seed = self.config.seed if base_seed is None else base_seed
        experiment_id = experiment_id or make_experiment_id(self.config.game.name)
        results: list[RunResult] = []
        for index in range(count):
            results.append(
                self.run(
                    seed=base_seed + index,
                    experiment_id=experiment_id,
                    observer=observer,
                )
            )
        return results

    @staticmethod
    def _resume_identity(
        run_dir: Path, seed: int, experiment_id: str
    ) -> tuple[int, str]:
        """Seed and experiment id of the crashed run, when it saved them.

        The run writes `config.yaml` (with the seed resolved) and the
        database row carrying the experiment id. A resumed game has to
        keep both or the ledger ends up with two different games under
        one id. Anything missing falls back to what the caller passed.
        """
        config_path = run_dir / "config.yaml"
        if config_path.exists():
            try:
                data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict) and isinstance(data.get("seed"), int):
                seed = int(data["seed"])
        return seed, experiment_id

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _callback(
        self,
        env: TraitorsEnvironment,
        runtime: AgentRuntime,
        agents: dict[str, Agent],
    ):
        """Wire AgentRuntime into the PhaseContext action callback."""

        async def callback(
            agent_id, action_type, legal_targets, extra_instruction=None
        ):
            self._sync_roles(env, agents)
            await self._sync_memory(env, agents)
            view = env.observe(agent_id)
            return await runtime.decide(
                agent_id, view, action_type, legal_targets, extra_instruction
            )

        return callback

    async def _sync_memory(self, env, agents) -> None:
        """Drain new events into the agents' memories.

        Runs before every decision, so an agent is asked only after the
        board has caught up with what just happened. What each player is
        allowed to remember is decided by `writes_for`, not here: the
        traitor council in particular must never reach a faithful player.

        The history is the whole log, not `sink.events`. `continue_after`
        clears that list on a resume, so a run that read from it would
        start its agents off having forgotten every round that happened
        before the interruption - and `public_rivals` would stop finding
        the earlier nominations and ballots that make the animosity effect
        work at all. A resumed game therefore replays the log into
        everyone's memory once, and then follows along live.
        """
        if not self.config.game.agent_memory:
            return
        if self._memory_history is None:
            # Everything already on disk predates this process.
            self._memory_history = [
                event.model_dump(mode="json")
                for event in env.engine.sink.prior_events()
            ]
            self._memory_seq = max(
                (int(e.get("sequence", -1)) for e in self._memory_history), default=-1
            )
        for event in env.engine.sink.events:
            # Sequences are monotonic and unique, so this is a cheap way to
            # take only what is new. The log already holds the events this
            # process emitted before the first call, hence the resume seed.
            if event.sequence <= self._memory_seq:
                continue
            self._memory_history.append(event.model_dump(mode="json"))
            self._memory_seq = event.sequence
        history = self._memory_history
        fresh = history[self._memory_cursor :]
        if not fresh:
            return
        self._memory_cursor = len(history)
        for event in fresh:
            round_number = int(event.get("round") or 0)
            sequence = int(event.get("sequence") or 0)
            # How sure the model was when it said this, carried onto the
            # memory rather than discarded with the action. Only message
            # actions produce one, so anything else falls back to fully
            # confident - an elimination or a vote is not a matter of
            # opinion and does not want a confidence attached to it.
            confidence = (event.get("payload") or {}).get("confidence")
            writer_confidence = (
                float(confidence) if isinstance(confidence, (int, float)) else 1.0
            )
            for write in writes_for(
                env.state, event, history,
                remember_everything=self.config.game.pointer_memory,
            ):
                for pid, agent in agents.items():
                    if write.visible_to(pid, env.state):
                        await agent.remember(
                            content=write.content,
                            kind=write.kind,
                            round_number=round_number,
                            sequence=sequence,
                            subjects=write.subjects,
                            salience=write.salience,
                            confidence=writer_confidence,
                            decayable=not write.durable,
                        )
            self._bump_relationships(env, agents, event, history)

    def _bump_relationships(self, env, agents, event, history) -> None:
        """How one event moves one player's read of another.

        This is what makes the memory per-observer rather than shared:
        being named raises suspicion, a private word raises trust, and a
        rival left suddenly under suspicion raises suspicion of whoever
        put them there.
        """
        kind = event["type"]
        payload = event.get("payload") or {}
        targets = event.get("targets") or []
        if kind == "NOMINATION_TALLY":
            for accuser, nominee in (payload.get("accusations") or {}).items():
                agent = agents.get(accuser)
                if agent is None:
                    continue
                rel = agent.relationships.get(nominee)
                agent.relationships.update(
                    nominee, suspicion=min(1.0, rel.suspicion + 0.4)
                )
        elif kind == "PRIVATE_MESSAGE":
            sender = event.get("actor")
            for peer in targets:
                for pid in (sender, peer):
                    agent = agents.get(pid)
                    if agent is None:
                        continue
                    other = peer if pid == sender else sender
                    rel = agent.relationships.get(other)
                    agent.relationships.update(other, trust=min(1.0, rel.trust + 0.2))
        elif kind == "PLAYER_ELIMINATED":
            for write in writes_for(env.state, event, history):
                if write.kind != "attention":
                    continue
                rival = (write.subjects or ("",))[0]
                for agent in agents.values():
                    rel = agent.relationships.get(rival)
                    agent.relationships.update(
                        rival, suspicion=min(1.0, rel.suspicion + 0.3)
                    )

    @staticmethod
    def _sync_roles(env: TraitorsEnvironment, agents: dict[str, Agent]) -> None:
        """Keep agent roles in step with the engine.

        Recruitment flips a faithful player to traitor mid-game; the
        prompt reads `agent.role`, so without this the recruit would
        keep playing as faithful.
        """
        for pid, role in env.state.roles.items():
            agent = agents.get(pid)
            if agent is not None and agent.role is not role:
                agent.assign_role(role, env.state.ambitions.get(pid))

    def _build_agents(self, env: TraitorsEnvironment) -> dict[str, Agent]:
        """Create agents with configured personas and the engine's roles."""
        loaded = self._load_personas()
        agents: dict[str, Agent] = {}
        for index, pid in enumerate(env.engine.player_ids):
            if loaded:
                persona, goals = loaded[index % len(loaded)]
            else:
                persona, goals = Persona(description=f"{pid} plays carefully."), Goals()
            agents[pid] = Agent(pid, pid.capitalize(), persona, goals=goals)
        for pid, role in env.state.roles.items():
            agents[pid].assign_role(role, env.state.ambitions.get(pid))
        return agents

    def _load_personas(self) -> list[tuple[Persona, Goals]]:
        """Resolve `game.personas` names against the personas directory."""
        names = self.config.game.personas or []
        if not names:
            return []
        if self.personas_dir is None:
            raise ValueError(
                "game.personas is set but no personas directory was provided"
            )
        loaded: list[tuple[Persona, Goals]] = []
        for name in names:
            filename = name if name.endswith(".yaml") else f"{name}.yaml"
            path = self.personas_dir / filename
            if not path.exists():
                available = sorted(
                    p.stem for p in self.personas_dir.glob("*.yaml")
                ) if self.personas_dir.exists() else []
                raise ValueError(
                    f"persona '{name}' not found at {path}; available: {available}"
                )
            loaded.append(load_persona_bundle(path))
        return loaded

    def _build_metrics(
        self,
        *,
        experiment_id: str,
        game_id: str,
        seed: int,
        env: TraitorsEnvironment,
        events: list[Event],
        gateway: LLMGateway,
        provider: LLMProvider,
        telemetry: TelemetryRecorder,
        elapsed: float,
    ) -> dict[str, Any]:
        """Run metrics plus the experiment identity (spec sections 27, 34)."""
        model, parameters = model_identity(self.config.llm)
        state = env.state
        by_type = Counter(event.type.value for event in events)
        public = by_type.get(EventType.PUBLIC_MESSAGE.value, 0)
        private = by_type.get(EventType.PRIVATE_MESSAGE.value, 0)
        eliminations = [
            {"round": e.round, "player": e.actor, "method": e.payload.get("method")}
            for e in events
            if e.type is EventType.PLAYER_ELIMINATED
        ]
        alive_traitors = [
            p for p in state.alive_players if state.roles[p] is Role.TRAITOR
        ]
        # A traitor faction win with one survivor is a solo win; with
        # two or more it is a team win. Faithful win as a team.
        solo = state.winner == "traitor" and len(alive_traitors) == 1
        outcomes = {
            pid: (
                "won"
                if (
                    (state.winner == "faithful" and role is Role.FAITHFUL)
                    or (
                        state.winner == "traitor"
                        and role is Role.TRAITOR
                        and pid in alive_traitors
                    )
                )
                else "lost"
            )
            for pid, role in state.roles.items()
        }
        return {
            # identity (spec section 27)
            "experiment_id": experiment_id,
            "game_id": game_id,
            "random_seed": seed,
            "model": model,
            "model_parameters": parameters,
            "prompt_version": PROMPT_VERSION,
            "persona_version": PERSONA_VERSION,
            "game_rules_version": GAME_RULES_VERSION,
            "memory_strategy": MEMORY_STRATEGY,
            # outcome
            "status": "completed",
            "winner": state.winner,
            "winning_team": state.winning_team.value if state.winning_team else None,
            "rounds": state.round_number,
            "players": len(state.players),
            "traitors": self.config.game.traitors,
            "duration_seconds": round(elapsed, 3),
            "finale": state.finale,
            "solo_traitor_win": solo,
            "surviving_traitors": sorted(alive_traitors),
            "outcomes": outcomes,
            "ambitions": dict(state.ambitions),
            # activity
            "events": {"total": len(events), "by_type": dict(sorted(by_type.items()))},
            "messages": {
                "public": public,
                "private": private,
                "ratio_public_private": round(public / private, 3) if private else None,
            },
            "votes_cast": by_type.get(EventType.VOTE_CAST.value, 0),
            "eliminations": eliminations,
            "rejected_actions": by_type.get(EventType.ACTION_REJECTED.value, 0),
            "llm": {
                "provider": self.config.llm.provider,
                "model": self.config.llm.model,
                # Gateway counters: successful provider calls and the
                # transport retries it spent.
                "provider_calls": gateway.calls,
                "retries": getattr(provider, "retries", 0),
                "in_flight_peak": gateway.in_flight_peak,
                "latency_ms_total": round(gateway.total_latency_ms, 1),
                "tokens": gateway.total_tokens,  # legacy key: output tokens
                # Per-call telemetry: tokens in and out, latency
                # percentiles, failures and per-action breakdown.
                **telemetry.summary(),
            },
            # Quality signals computed from this run's own messages.
            "quality": analyse_quality(events),
        }

    def _write_artifacts(
        self,
        *,
        run_dir: Path,
        seed: int,
        state: GameState,
        events: list[Event],
        metrics: dict[str, Any],
    ) -> None:
        """Write the run directory contents (spec sections 34, 43)."""
        config_data = self.config.model_dump(mode="json")
        config_data["seed"] = seed
        (run_dir / "config.yaml").write_text(
            yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8"
        )
        (run_dir / "game.json").write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (run_dir / "transcript.json").write_text(
            json.dumps(build_transcript(events), indent=2), encoding="utf-8"
        )
        (run_dir / "transcript.txt").write_text(
            render_transcript(events), encoding="utf-8"
        )
        (run_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _persist_identity(
        self,
        *,
        experiment_id: str,
        game_id: str,
        seed: int,
        winner: Optional[str],
        winning_team: Optional[str],
    ) -> None:
        """Record experiment and game rows when a database is attached."""
        if self.db is None:
            return
        model, parameters = model_identity(self.config.llm)
        ExperimentRepository(self.db).create(
            experiment_id,
            seed,
            model,
            parameters,
            prompt_version=PROMPT_VERSION,
            persona_version=PERSONA_VERSION,
            game_rules_version=GAME_RULES_VERSION,
            memory_strategy=MEMORY_STRATEGY,
        )
        games = GameRepository(self.db)
        games.create(
            game_id,
            experiment_id,
            self.config.model_dump(mode="json"),
            seed,
            status="completed",
        )
        games.update_status(
            game_id, "completed", winner=winner, winning_team=winning_team
        )
