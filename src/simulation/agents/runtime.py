"""Agent runtime: the observe -> remember -> prompt -> decide loop (spec section 11).

The runtime returns a structured action to the engine; it never
executes actions itself. Malformed or illegal output triggers one
correction retry before giving up (spec section 21).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Optional

from simulation.actions.actions import Action, ActionType, action_schema
from simulation.actions.validator import (
    ActionParseError,
    check_action_constraints,
    parse_action,
    phantom_reason,
    resolve_content_names,
    resolve_target,
)
from simulation.actions.repetition import find_repeat, repeat_reason
from simulation.agents.agent import Agent
from simulation.agents.ledger import (
    DEFAULT_ALLY_SIZE,
    DEFAULT_SUSPECT_SIZE,
    LEDGER_SYSTEM,
    Ledger,
    apply_ledger,
    ledger_lines,
    ledger_prompt,
    ledger_schema,
    parse_ledger,
)
from simulation.agents.prompts import PromptBuilder, action_json_hint
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.memory.short_term import DEFAULT_DECAY, DEFAULT_FLOOR
from simulation.models.base import ChatMessage
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig

# The ledger's generation is recorded under its own `action_type` so its cost
# and failures are countable. It is not an `ActionType` because it is not a
# game action: it produces no decision the engine sees, only the player's
# private read. Naming it distinctly is what lets `tools/run_health.py`
# separate the mechanism's overhead from the game's own calls.
LEDGER_ACTION = "ledger_update"

if TYPE_CHECKING:  # avoids an agents -> experiments import at runtime
    from simulation.experiments.telemetry import TelemetryRecorder


class AgentRuntime:
    """One decide loop per agent turn; `max_retries` correction attempts
    are additional, so the default is three attempts per action (spec
    section 21). Small local models miss required fields often enough
    that a single retry still lost whole runs."""

    def __init__(
        self,
        agents: dict[str, Agent],
        gateway: LLMGateway,
        model_config: ModelConfig,
        prompt_builder: Optional[PromptBuilder] = None,
        max_retries: int = 2,
        telemetry: Optional["TelemetryRecorder"] = None,
        memory_enabled: bool = False,
        memory_decay: float = DEFAULT_DECAY,
        memory_floor: float = DEFAULT_FLOOR,
        memory_items_limit: int = 6,
        reject_invented_players: bool = False,
        reject_repetition: bool = False,
        repetition_threshold: float = 1.0,
        repetition_scope: str = "room",
        repetition_min_words: int = 5,
        suspicion_ledger: bool = False,
        ledger_suspects: int = DEFAULT_SUSPECT_SIZE,
        ledger_allies: int = DEFAULT_ALLY_SIZE,
    ) -> None:
        self.agents = agents
        self.gateway = gateway
        self.model_config = model_config
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.telemetry = telemetry
        self.max_retries = max_retries  # extra attempts after the first
        # Agent memory (decaying, per-observer). Off by default so an
        # existing config plays exactly as it did before.
        self.memory_enabled = memory_enabled
        self.memory_decay = memory_decay
        self.memory_floor = memory_floor
        self.memory_items_limit = memory_items_limit
        # Targets repaired before the legality check, for the run summary.
        self._repair_log: list[str] = []
        # Names invented by the model, kept so a run that hallucinates a
        # player is visible rather than silent.
        self._phantom_log: list[str] = []
        # Phantoms are always logged; this decides whether one also costs
        # the turn. Off until the detector's false-positive rate is known.
        self.reject_invented_players = reject_invented_players
        # Repetition gate. Off by default: the threshold is a judgement
        # call, and turning it on silently changes every run that uses this
        # constructor. Calibrate it against a completed run with
        # tools/repeat_rate.py before enabling it.
        self.reject_repetition = reject_repetition
        # Calibrated by replaying a completed run: 48% of its public
        # messages overlap an earlier one by >= 0.6, because players
        # legitimately keep returning to the same claims. Only an exact
        # match (1.0) separates copying from discussion there - and that is
        # the form the template collapse actually takes.
        self.repetition_threshold = repetition_threshold
        # "self" compares against this agent's own earlier messages, which
        # is the failure a player repeats. "room" also compares against
        # everyone else's, which catches the template collapse where eight
        # players converge on one phrase without any single player saying
        # it twice. Both scopes are nested, so `room` is the default: `self`
        # saw only the smaller half of a measured collapse (zero self-repeats
        # and still 31 byte-identical messages from different players).
        self.repetition_scope = repetition_scope
        self.repetition_min_words = repetition_min_words
        # Messages rejected as repeats, for the run summary. Recorded even
        # when the repeat is accepted on the last attempt.
        self._repeat_log: list[str] = []
        # The suspicion ledger. Off by default for the same reason as every
        # other experimental flag: an existing config must play identically.
        self.suspicion_ledger = suspicion_ledger
        self.ledger_suspects = ledger_suspects
        self.ledger_allies = ledger_allies
        # Counts ledger generations, for the run summary. The ledger costs an
        # extra call per player per round, so a run that silently made ten
        # thousand of them would otherwise look identical to one that did not.
        self._ledger_calls = 0
        self._ledger_failures = 0

    @property
    def ledger_calls(self) -> int:
        """How many dedicated ledger generations this run has spent."""
        return self._ledger_calls

    @property
    def repeats_rejected(self) -> int:
        """How many generated messages were rejected as near-duplicates."""
        return len(self._repeat_log)

    @property
    def repairs(self) -> int:
        """How many near-miss targets were mapped onto a legal player."""
        return len(self._repair_log)

    @property
    def repair_log(self) -> list[str]:
        """Each repair, in order, for the run summary.

        Kept in memory rather than pushed through the call recorder: that
        would mean a new field on `CallRecord` and on every metrics file,
        and this counter only needs to survive to the end of the run.
        """
        return list(self._repair_log)

    @property
    def phantoms(self) -> list[str]:
        """Every invented player name this run has produced."""
        return list(self._phantom_log)

    async def decide(
        self,
        agent_id: str,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
        extra_instruction: Optional[str] = None,
    ) -> Action:
        """Produce one validated structured action for this observation."""
        agent = self.agents[agent_id]
        if agent.role is None:
            raise RuntimeError(f"agent '{agent_id}' has no assigned role")

        memory_items = self._memory_items(agent, view)
        # The ledger is refreshed before the prompt is built, so the prompt
        # always shows this round's read rather than last round's. Once per
        # round per player; `refresh_ledger` is a no-op after the first call
        # in a round and returns None when the flag is off.
        await self.refresh_ledger(agent_id, view)
        builder = self.prompt_builder
        if self.suspicion_ledger:
            # The builder is shared across agents, so the per-player lines go
            # on a copy. Mutating the shared instance would show one player's
            # ledger to every other player in the game.
            builder = copy.copy(builder)
            builder.suspicion_ledger = True
            builder._ledger_lines = ledger_lines(
                agent.beliefs, self.ledger_suspects, self.ledger_allies
            )
            builder._standing_lines = agent.relationships.stand_lines()
        messages = builder.build(
            agent_id=agent_id,
            agent=agent,
            role=agent.role,
            persona=agent.persona,
            goals=agent.goals,
            view=view,
            action_type=action_type,
            legal_targets=legal_targets,
            memory_items=memory_items,
            extra_instruction=extra_instruction,
        )

        attempts = 1 + self.max_retries
        last_reason = ""
        for attempt in range(attempts):
            try:
                response = await self.gateway.generate(
                    messages,
                    action_schema(
                        action_type,
                        want_gist=self.prompt_builder.want_gist,
                        gist_required=self.prompt_builder.gist_required,
                    ),
                    self.model_config,
                )
            except Exception as exc:
                # Transport failures still belong in the run's telemetry.
                # The retry count rides on the error rather than being read
                # off the provider's shared counter: under concurrency that
                # counter moves for other calls too, which would credit this
                # failure with retries it did not spend.
                self._record_call(
                    view,
                    action_type,
                    attempt + 1,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    retries=int(getattr(exc, "retries", 0)),
                    messages=messages,
                )
                raise
            retries_spent = int(getattr(response, "retries", 0))
            try:
                action = parse_action(response.content, agent_id)
                # A near-miss name ('Meryl', 'clare') is the same player the
                # model meant. Repair it before the legality check, but only
                # when the match is unambiguous, and keep the note so the
                # repair shows up in telemetry instead of vanishing.
                action, repair = resolve_target(action, legal_targets)
                if repair is not None:
                    self._repair_log.append(f"{agent_id}: {repair}")
                # Names in the prose, not just the target field. A model can
                # invent a whole player inside `content` and never trip the
                # target check, which is how a phantom got into one run.
                if action.content:
                    roster = list(view.alive_players) + list(
                        view.eliminated_players
                    )
                    fixed, repairs, phantoms = resolve_content_names(
                        action.content, roster, messages[1].content
                    )
                    if repairs:
                        self._repair_log.extend(
                            f"{agent_id}: name {r}" for r in repairs
                        )
                        action = action.model_copy(update={"content": fixed})
                    if phantoms:
                        # Always recorded. Rejected only when the flag is
                        # on: the detector is not calibrated, and a false
                        # positive would discard a legitimate turn. Measure
                        # the rate from the log, then decide.
                        self._phantom_log.append(
                            f"{agent_id}: {', '.join(phantoms)}"
                        )
                        if self.reject_invented_players:
                            raise ActionParseError(
                                phantom_reason(phantoms, roster)
                            )
                    # Repetition, judged against what this agent has
                    # already put into the conversation. Checked after the
                    # name repair so the comparison is on the text the room
                    # will actually see, and raised as a parse error so it
                    # reuses the existing correction retry: the rejected
                    # message is already appended to the conversation,
                    # which is what lets the model see what it said.
                    repeat = self._repetition_reason(
                        view, action, action_type
                    )
                    if repeat is not None:
                        self._repeat_log.append(f"{agent_id}: {repeat[:80]}")
                        # On the last attempt the repeated text is accepted
                        # anyway. A model that cannot break out of a phrase
                        # must not cost the whole game a turn; the repeat
                        # stays visible in the summary instead.
                        if attempt < attempts - 1:
                            raise ActionParseError(repeat)
                reason = check_action_constraints(action, {action_type}, legal_targets)
                if reason is not None:
                    raise ActionParseError(reason)
            except ActionParseError as exc:
                last_reason = str(exc)
                self._record_call(
                    view,
                    action_type,
                    attempt + 1,
                    ok=False,
                    response=response,
                    error=last_reason,
                    retries=retries_spent,
                    messages=messages,
                )
                if attempt == attempts - 1:
                    raise ActionParseError(
                        f"'{agent_id}' failed to produce a valid action after "
                        f"{attempts} attempts: {last_reason}"
                    ) from exc
                messages = messages + [
                    ChatMessage(role="assistant", content=response.content),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Invalid action: {last_reason}. Try again. "
                            f"{action_json_hint(action_type)}"
                        ),
                    ),
                ]
                continue
            self._record_call(
                view,
                action_type,
                attempt + 1,
                ok=True,
                response=response,
                retries=retries_spent,
                messages=messages,
            )
            return action

        raise AssertionError("unreachable")

    # Actions whose `content` is a fixed vocabulary rather than prose. A
    # "banish" is not a repetition of the last "banish", and treating it as
    # one would burn retries on the vote every round.
    _FIXED_CONTENT_ACTIONS = frozenset({
        ActionType.END_VOTE,
        ActionType.RECRUIT_DECISION,
        ActionType.RECRUIT_RESPONSE,
    })

    async def refresh_ledger(
        self, agent_id: str, view: AgentView
    ) -> Optional[Ledger]:
        """Ask one player to record who they suspect and who they have cleared.

        A separate generation, which is the expensive choice. The cheap one -
        reading suspicion out of the `confidence` on a message action - was
        rejected because it reports how sure the model was of what it just
        said, not who it thinks is guilty, and those are different questions.

        Once per round per player, at the round's opening, rather than before
        every action: the ledger is a position that should change when the
        evidence changes, not a value recomputed twenty times between
        statements. Returning None when the flag is off keeps the caller from
        having to branch on the config.
        """
        agent = self.agents.get(agent_id)
        if not self.suspicion_ledger or agent is None:
            return None
        if agent.ledger_round == view.round_number:
            return agent.ledger          # already refreshed this round

        current = agent.beliefs.prompt_lines()
        prompt = ledger_prompt(view, current, view.round_number)
        messages = [
            ChatMessage(
                role="system",
                content=LEDGER_SYSTEM.format(
                    suspects=self.ledger_suspects,
                    allies=self.ledger_allies,
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]
        try:
            response = await self.gateway.generate(
                messages,
                ledger_schema(self.ledger_suspects, self.ledger_allies),
                self.model_config,
            )
        except Exception as exc:
            # A failed ledger must never cost the turn it was supporting.
            # The player's previous read stands, which is the right fallback:
            # a stale position is better than no position.
            #
            # Recorded, because a run whose ledgers silently failed everywhere
            # looks exactly like one where the players had no opinions - the
            # call is the mechanism, so its absence has to be visible.
            self._record_call(
                view,
                LEDGER_ACTION,
                1,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                messages=messages,
            )
            self._ledger_failures += 1
            return agent.ledger
        self._record_call(
            view, LEDGER_ACTION, 1, ok=True, response=response, messages=messages
        )

        ledger = parse_ledger(response.content)
        # Only living, real players are kept. The model was told the roster,
        # but a name that is not on it would otherwise enter the prompt as a
        # suspect and send this player chasing somebody who does not exist.
        roster = set(view.alive_players)
        ledger.suspects = [s for s in ledger.suspects if s.player in roster]
        ledger.allies = [a for a in ledger.allies if a.player in roster]
        ledger.suspects.sort(key=lambda s: s.confidence, reverse=True)
        apply_ledger(ledger, agent.beliefs, view.round_number)
        agent.ledger = ledger
        agent.ledger_round = view.round_number
        self._ledger_calls += 1
        return ledger

    def _repetition_reason(
        self, view: AgentView, action: Action, action_type: ActionType
    ) -> Optional[str]:
        """Why this message repeats what was already said, or None.

        Compares only within the channel the message is going to, since a
        private DM that echoes the public room is expected behaviour.
        """
        if not self.reject_repetition:
            return None
        if action_type in self._FIXED_CONTENT_ACTIONS:
            return None
        content = (action.content or "").strip()
        if not content:
            return None

        if self.repetition_scope == "room":
            # Self plus others. This used to exclude the speaker, which made
            # the two scopes disjoint rather than nested: switching from
            # `self` to `room` *dropped* the self-check instead of adding to
            # it, and a run at `scope: room` finished with 50 byte-identical
            # self-repeats. The room includes the speaker's own messages.
            history = [m.content for m in view.public_transcript]
        else:
            history = [
                m.content
                for m in view.public_transcript
                if m.sender_id == view.agent_id
            ]
        # A private message is compared against the agent's own private
        # history only; comparing it to the public room would reject the
        # natural case of raising in private what was said in public.
        if action_type is ActionType.PRIVATE_MESSAGE:
            history = [
                m.content
                for m in view.private_conversations
                if m.sender_id == view.agent_id
            ]

        found = find_repeat(
            content,
            history,
            self.repetition_threshold,
            self.repetition_min_words,
        )
        if found is None:
            return None
        return repeat_reason(*found)

    def _memory_items(self, agent: Agent, view: AgentView) -> list:
        """What this agent still remembers, or nothing when memory is off.

        Default off, so an existing game plays exactly as it did before.
        """
        if not self.memory_enabled:
            return []
        return agent.memory_items(
            now_round=view.round_number,
            limit=self.memory_items_limit,
            decay=self.memory_decay,
            floor=self.memory_floor,
        )

    def _record_call(
        self,
        view: AgentView,
        action_type: "ActionType | str",
        attempt: int,
        *,
        ok: bool,
        response=None,
        retries: int = 0,
        error: Optional[str] = None,
        messages: Optional[list[ChatMessage]] = None,
    ) -> None:
        """Record one model call.

        `action_type` accepts a plain string as well as an `ActionType`,
        because the ledger's generation is a model call that is not a game
        action. It used to bypass this method entirely, which meant a run
        whose ledgers silently failed everywhere looked exactly like a run
        where the players had no opinions - the mechanism itself was
        invisible in the one place a run's cost and failures are readable.
        """
        if self.telemetry is None:
            return
        self.telemetry.record(
            agent_id=view.agent_id,
            action_type=(
                action_type.value
                if isinstance(action_type, ActionType)
                else str(action_type)
            ),
            phase=view.phase.value,
            round_number=view.round_number,
            attempt=attempt,
            ok=ok,
            response=response,
            retries=retries,
            error=error,
            prompt_chars=sum(len(m.content) for m in (messages or [])),
        )


def make_action_callback(
    runtime: AgentRuntime,
    engine: GameEngine,
    projector: InformationProjector,
):
    """Wire the runtime into a PhaseContext's action callback (spec section 7)."""

    async def callback(
        agent_id: str,
        action_type: ActionType,
        legal_targets: list[str],
        extra_instruction: Optional[str] = None,
    ) -> Action:
        view = projector.project(engine.state, agent_id)
        return await runtime.decide(
            agent_id, view, action_type, legal_targets, extra_instruction
        )

    return callback
